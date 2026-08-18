import time  # Used to set the start and end times inside the GitHub App token.

import httpx  # HTTP client. We use both its async and its plain form in this file.
import jwt  # Signs the short-lived token that proves we are this GitHub App.
from fastapi import FastAPI  # The application object.
from prometheus_fastapi_instrumentator import Instrumentator  # Adds request metrics without writing any code.
from sqlalchemy import update  # Builds an UPDATE statement without loading the row first.
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # The async database session and engine.
from sqlalchemy.orm import sessionmaker  # A factory that hands us a new session whenever we need one.

from models import Settings, PullRequest, ReviewRequest  # Settings, the pull_requests table, and the shape of the incoming request.

settings = Settings()  # Read the settings once when the file loads, not on every request.
engine = create_async_engine(settings.database_url)  # One engine for the whole service. It holds the connection pool.
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)  # expire_on_commit=False lets us read a row after commit.

app = FastAPI()  # The application object. Uvicorn runs this.
Instrumentator().instrument(app).expose(app)  # Measure every route and publish the numbers at /metrics.


@app.get("/health")
async def health():  # WHAT THIS DOES: Tells Docker, Kubernetes, and the load balancer that this service is alive.
    return {"status": "ok"}  # Answers without touching the database, so it stays fast and never fails by accident.


def _finding_summary_line(f: dict) -> str:  # WHAT THIS DOES: Turns one finding into one line of Markdown for the summary.
    severity = f.get("severity", "info").upper()  # Upper case so INFO, WARNING, and ERROR stand out when read.
    return f"**[{severity}]** `{f.get('file', 'unknown')}:{f.get('line', '?')}` ({f.get('agent', '')})\n{f.get('message', '')}\n"  # Every field has a fallback, because the model may leave any of them out.


def _build_summary(findings: list) -> str:  # WHAT THIS DOES: Joins all the findings into the one comment that opens the review.
    lines = ["## AI Code Review\n"] + [_finding_summary_line(f) for f in findings]  # A heading first, then one block per finding.
    return "\n".join(lines)  # This text is posted even when no inline comment can be placed.


@app.post("/post-review")
async def post_review(request: ReviewRequest):  # WHAT THIS DOES: Writes the findings onto the pull request as a GitHub review.
    token = get_installation_token(request.installation_id)  # A fresh GitHub token for this repo. It lasts one hour.

    if not request.findings:  # The reviewers found nothing worth saying.
        return {"status": "ok"}  # Say nothing on GitHub. A review with no findings is noise.

    inline_comments = []  # The comments that will sit on a real line of the diff.
    for f in request.findings:  # Not every finding can become an inline comment, so we sort them here.
        try:
            line = int(f.get("line") or 0)  # The model sometimes sends "42" as text, or nothing at all.
        except (ValueError, TypeError):
            line = 0  # Anything we cannot read as a number is treated as no line.
        if f.get("file") and line > 0:  # GitHub needs both a file and a real line, or it rejects the whole review.
            inline_comments.append({
                "path": f.get("file"),  # The file, as it appears in the diff.
                "line": line,  # The line inside that file.
                "side": "RIGHT",  # RIGHT means the new version of the code, not the old one.
                "body": f"**[{f.get('severity', 'info').upper()}]** ({f.get('agent', '')})\n{f.get('message', '')}",  # Shorter than the summary line, since the file and line are already shown.
            })

    headers = {  # The same headers for both calls below.
        "Authorization": f"Bearer {token}",  # The install token from the function at the bottom.
        "Accept": "application/vnd.github.v3+json",  # Ask for the stable version of the API.
    }
    url = f"https://api.github.com/repos/{request.repo_full_name}/pulls/{request.pr_number}/reviews"  # One call posts the summary and every inline comment together.
    summary = _build_summary(request.findings)  # Built from all the findings, including those with no usable line.

    async with httpx.AsyncClient() as client:  # The with block closes the connection for us, even if something fails.
        response = await client.post(
            url,
            json={"event": "COMMENT", "body": summary, "comments": inline_comments},  # COMMENT means comment only, do not approve and do not block the merge.
            headers=headers,
            timeout=30,  # Half a minute. This is one plain API call.
        )
        if response.status_code == 422 and inline_comments:  # 422 means GitHub refused a line, usually because it is not part of the diff.
            response = await client.post(  # Try again with the summary alone, so the review is still delivered.
                url,
                json={"event": "COMMENT", "body": summary},  # No inline comments this time. Nothing can be refused.
                headers=headers,
                timeout=30,
            )
        response.raise_for_status()  # If even the plain summary failed, fail loudly so the error is visible.

    async with AsyncSessionLocal() as session:  # The with block closes the session for us, even if something fails.
        await session.execute(  # Mark the work finished, but only after GitHub accepted it.
            update(PullRequest).where(PullRequest.id == request.pr_id).values(status="reviewed")  # One UPDATE, without loading the row first.
        )
        await session.commit()  # Write it. Until this line runs, the row still says pending.

    return {"status": "ok"}  # The caller is the orchestrator, so nobody is waiting on this answer.


def get_installation_token(installation_id: int) -> str:  # WHAT THIS DOES: Trades our app's signed token for a token that can act on one repo.
    now = int(time.time())  # The current time in seconds.
    payload = {"iat": now - 60, "exp": now + 600, "iss": settings.github_app_id}  # Backdated a minute for clock drift, valid ten minutes, GitHub's maximum.
    private_key = settings.github_app_private_key.replace("\\n", "\n")  # Environment variables cannot hold real newlines, so we put them back.
    encoded_jwt = jwt.encode(payload, private_key, algorithm="RS256")  # Sign it. Only the holder of the private key can do this.

    with httpx.Client() as client:  # The plain client, because this function is not async. See the note at the bottom.
        response = client.post(
            f"https://api.github.com/app/installations/{installation_id}/access_tokens",  # Ask GitHub for a token for this one install.
            headers={
                "Authorization": f"Bearer {encoded_jwt}",  # Prove we are the app by sending the signed token.
                "Accept": "application/vnd.github.v3+json",  # Ask for the stable version of the API.
            },
        )
        response.raise_for_status()  # A bad key or a removed install fails loudly here, not later.
        return response.json()["token"]  # The token that can write to this repo. It lasts one hour.

# ---------------------------------------------------------------------------
# WHAT THIS FILE IS FOR
# This is the reviewer service, the last step in the chain and the only one that
# writes anything back to GitHub. The orchestrator hands it the findings; its job
# is to turn them into a review a person can read.
# Each finding is sorted into one of two kinds. Findings with a file and a real
# line number become inline comments, sitting on the exact line they are about.
# The rest still appear in the summary comment at the top, so nothing is lost.
# Both go to GitHub in a single call, as a COMMENT review, which means it never
# approves and never blocks a merge. If GitHub answers 422, which happens when a
# line is not part of the diff, it posts the summary on its own rather than
# dropping the review. Only after GitHub accepts does it mark the pull request
# reviewed in the database, so a failed post leaves the work ready to retry.
# ---------------------------------------------------------------------------
