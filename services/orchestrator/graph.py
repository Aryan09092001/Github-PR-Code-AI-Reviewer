import json  # The model answers with JSON text, so we turn that text into Python objects.
import operator  # Gives us operator.add, used below to join lists from the four agents.
import re  # Used to pull the JSON out when the model wraps it in a code block.
from typing import TypedDict, Annotated  # TypedDict describes the shared state. Annotated attaches the merge rule.

from langfuse.openai import OpenAI  # The normal OpenAI client, wrapped so every call is traced in Langfuse.
from langgraph.graph import StateGraph, END  # StateGraph holds the steps. END marks the finish.
from langgraph.constants import Send  # Send starts one step with its own copy of the state.

client = OpenAI()  # Created once when the file loads. It reads the API key from the environment.

PROMPTS = {  # One instruction per reviewer. Three are fixed text, style is built at run time.
    "static_analysis": "You are a static analysis tool. Review this git diff for code complexity issues, unused variables, and poor naming. Return only a JSON array. Each item must have keys: file, line, severity (info/warning/error), message.",  # Looks for messy code.
    "security": "You are a security scanner. Review this git diff for OWASP Top 10 vulnerabilities, hardcoded secrets, and SQL injection risks. Return only a JSON array. Each item must have keys: file, line, severity, message.",  # Looks for unsafe code.
    "architecture": "You are an architecture reviewer. Review this git diff for separation of concerns violations, missing error handling, and improper dependency usage. Return only a JSON array. Each item must have keys: file, line, severity, message.",  # Looks at the design.
}


def parse_json_response(raw: str) -> list:  # WHAT THIS DOES: Turns the model's reply into a list, and never raises.
    raw = raw.strip()  # Drop the blank space around the reply.
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)  # Models often wrap JSON in a code fence even when told not to.
    if match:  # A fence was found.
        raw = match.group(1).strip()  # Keep only what was inside it.
    try:
        return json.loads(raw)  # The happy path: valid JSON becomes a list of findings.
    except Exception:
        return []  # Bad JSON means no findings from this agent, rather than a crashed review.


class GraphState(TypedDict):  # WHAT THIS CLASS IS: The shared bag of data every step reads and writes.
    diff: str  # The code change under review. Read-only for every step.
    patterns: list[str]  # Lessons from earlier reviews of this repo. Only the style agent uses them.
    findings: Annotated[list[dict], operator.add]  # operator.add means the four agents' lists are joined, not overwritten.


def make_node(agent_name: str, get_prompt):  # WHAT THIS DOES: Builds one reviewer step, so we do not write the same code four times.
    def node(state: GraphState) -> dict:  # This inner function is the step LangGraph will call.
        prompt = get_prompt(state) if callable(get_prompt) else get_prompt  # Either fixed text, or a function that builds text from the state.
        response = client.chat.completions.create(  # Ask the model to review the diff.
            model="gpt-4o-mini",  # A small, cheap model. Four of these run for every pull request.
            messages=[
                {"role": "system", "content": prompt},  # The instruction: what to look for and what shape to answer in.
                {"role": "user", "content": state["diff"]},  # The code change itself.
            ],
        )
        items = parse_json_response(response.choices[0].message.content)  # Reply text becomes a list. Bad JSON gives an empty one.
        for item in items:  # Stamp every finding with the agent that produced it.
            item["agent"] = agent_name  # Lets us show later which reviewer said what.
        return {"findings": items}  # Only return findings. LangGraph joins this list onto the shared one.
    return node  # Hand back the step. build_graph() registers it.


def _style_prompt(state: GraphState) -> str:  # WHAT THIS DOES: Builds the style instruction, with this repo's past lessons mixed in.
    patterns_str = "\n".join(state["patterns"]) if state["patterns"] else "None"  # "None" keeps the sentence readable when there are no lessons yet.
    return f"You are a code style reviewer. Review this git diff for formatting, readability, and consistency issues. Common patterns this team has had before: {patterns_str}. Return only a JSON array. Each item must have keys: file, line, severity, message."  # This is the one prompt that learns.


def merge_node(state: GraphState) -> dict:  # WHAT THIS DOES: Removes duplicate findings after all four agents have finished.
    seen = set()  # Remembers what we have already kept.
    merged = []  # The findings we keep, in the order we first met them.
    for finding in state["findings"]:  # Walk the joined list from all four agents.
        key = (finding.get("file"), finding.get("line"), finding.get("agent"), finding.get("message"))  # Four fields decide what counts as the same finding.
        if key not in seen:  # First time we have seen this one.
            seen.add(key)  # Remember it.
            merged.append(finding)  # Keep it.
    return {"findings": merged}  # Note: this list is joined onto the old one, it does not replace it. See the note at the bottom.


def fan_out(state: GraphState):  # WHAT THIS DOES: Starts all four reviewers at once instead of one after another.
    return [
        Send("static_analysis", state),  # Each Send starts that step with its own copy of the state.
        Send("security", state),  # They run side by side, so the review takes as long as the slowest agent.
        Send("style", state),  # Not four times as long, which is the whole point.
        Send("architecture", state),
    ]


def build_graph() -> StateGraph:  # WHAT THIS DOES: Wires the steps together and returns a graph that is ready to run.
    builder = StateGraph(GraphState)  # The graph will pass a GraphState between steps.

    builder.add_node("static_analysis", make_node("static_analysis", PROMPTS["static_analysis"]))  # Register the complexity reviewer.
    builder.add_node("security", make_node("security", PROMPTS["security"]))  # Register the security reviewer.
    builder.add_node("style", make_node("style", _style_prompt))  # Register the style reviewer, the one that uses past lessons.
    builder.add_node("architecture", make_node("architecture", PROMPTS["architecture"]))  # Register the design reviewer.
    builder.add_node("merge", merge_node)  # Register the step that removes duplicates.

    builder.set_conditional_entry_point(fan_out)  # Start here. fan_out decides what runs first, which is everything.

    for name in ("static_analysis", "security", "style", "architecture"):  # Every reviewer leads to merge.
        builder.add_edge(name, "merge")  # merge waits until all four have finished.
    builder.add_edge("merge", END)  # After merge, the run is over.

    return builder.compile()  # compile() checks the wiring and gives back a runnable graph.

# ---------------------------------------------------------------------------
# WHAT THIS FILE IS FOR
# This file holds the review itself, written as a LangGraph graph. One diff goes
# in, a list of findings comes out. Nothing here talks to the database or to
# GitHub, which is what makes it easy to test on its own.
# The shape is fan out, then merge. fan_out starts four reviewers at the same
# time, each with its own instruction: complexity, security, style, and
# architecture. Because they run side by side, a review costs the time of the
# slowest agent, not the sum of all four. Each one is asked to answer in JSON,
# and parse_json_response quietly returns an empty list when the answer is not
# valid JSON, so one bad reply cannot break the whole review. LangGraph joins
# the four lists into one, then merge_node drops the duplicates that different
# agents naturally report about the same line. main.py runs this graph.
# ---------------------------------------------------------------------------
