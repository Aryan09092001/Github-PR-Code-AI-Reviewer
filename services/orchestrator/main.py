import time  # Used to set the start and end times inside the GitHub App token.

import httpx  # HTTP client that works with async code. We call GitHub and the reviewer service with it.
import jwt  # Signs the short-lived token that proves we are this GitHub App.
from fastapi import FastAPI  # The application object.
from prometheus_fastapi_instrumentator import Instrumentator  # Adds request metrics without writing any code.
from sqlalchemy import select  # Builds SELECT queries in Python instead of writing SQL strings.
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # The async database session and engine.
from sqlalchemy.orm import sessionmaker  # A factory that hands us a new session whenever we need one.

from models import Settings, Finding, Pattern, AnalyzeRequest  # Settings, two tables, and the shape of the incoming request.
from graph import build_graph  # The four-agent review graph. All the model work lives there, not here.

settings = Settings()  # Read the settings once when the file loads, not on every request.
engine = create_async_engine(settings.database_url)  # One engine for the whole service. It holds the connection pool.
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)  # expire_on_commit=False lets us read a row after commit.

app = FastAPI()  # The application object. Uvicorn runs this.
Instrumentator().instrument(app).expose(app)  # Measure every route and publish the numbers at /metrics.


@app.get("/health")
async def health():  # WHAT THIS DOES: Tells Docker, Kubernetes, and the load balancer that this service is alive.
    return {"status": "ok"}  # Answers without touching the database, so it stays fast and never fails by accident.


@app.post("/analyze", status_code=202)
async def analyze(request: AnalyzeRequest):  # WHAT THIS DOES: Runs one whole review, from fetching the diff to handing the findings on.
    token = await get_installation_token(request.installation_id)  # A fresh GitHub token for this repo. It lasts one hour.
    diff = await fetch_diff(request.repo_full_name, request.pr_number, token)  # The code change we are about to review.

    async with AsyncSessionLocal() as session:  # The with block closes the session for us, even if something fails.
        result = await session.execute(  # Load the lessons we learned from earlier reviews of this repo.
            select(Pattern)
            .where(Pattern.repo_full_name == request.repo_full_name)  # Lessons belong to one repository only.
            .order_by(Pattern.frequency.desc())  # Most repeated lessons first, they matter most.
            .limit(10)  # Ten is enough. More would only make the prompt longer and cost more.
        )
        patterns = [row.pattern_text for row in result.scalars().all()]  # Keep just the text. The graph does not need the rest.

    state = build_graph().invoke({"diff": diff, "patterns": patterns, "findings": []})  # Run all four agents. This is the slow part.
    findings_data = state.get("findings", [])  # What the agents found, after duplicates were removed.

    async with AsyncSessionLocal() as session:  # A new session, because the review took a long time.
        for f in findings_data:  # Save every finding as its own row.
            session.add(Finding(  # Stage the row. Nothing is written until the commit below.
                pr_id=request.pr_id,  # Ties the finding to the pull request it belongs to.
                file=f.get("file"),  # Fields come from the model, so we use .get() and accept None.
                line=f.get("line"),  # A missing line is better than a crashed review.
                severity=f.get("severity"),  # info, warning, or error.
                message=f.get("message"),  # The text we will post as a comment.
                agent=f.get("agent"),  # Which of the four agents found it.
            ))
        await session.commit()  # Write them all in one go. Save first, then post, so nothing is lost.

    async with httpx.AsyncClient() as client:  # The with block closes the connection for us, even if something fails.
        await client.post(
            "http://reviewer:8003/post-review",  # "reviewer" is a Docker Compose service name, reachable only inside our network.
            json={  # Everything the reviewer needs to write the comments on GitHub.
                "pr_id": str(request.pr_id),  # UUID becomes text, because JSON has no UUID type.
                "repo_full_name": request.repo_full_name,  # The repository, written as "owner/repo".
                "pr_number": request.pr_number,  # Which pull request to comment on.
                "installation_id": request.installation_id,  # The reviewer gets its own token with this.
                "findings": findings_data,  # Sent along, so the reviewer does not have to read the database again.
            },
            timeout=60,  # One minute. Posting comments is much faster than reviewing.
        )

    return {"status": "accepted"}  # 202. The caller was a Celery worker, so nobody is waiting on this answer.


async def get_installation_token(installation_id: int) -> str:  # WHAT THIS DOES: Trades our app's signed token for a token that can act on one repo.
    now = int(time.time())  # The current time in seconds.
    payload = {"iat": now - 60, "exp": now + 600, "iss": settings.github_app_id}  # Backdated a minute for clock drift, valid ten minutes, GitHub's maximum.
    private_key = settings.github_app_private_key.replace("\\n", "\n")  # Environment variables cannot hold real newlines, so we put them back.
    encoded_jwt = jwt.encode(payload, private_key, algorithm="RS256")  # Sign it. Only the holder of the private key can do this.

    async with httpx.AsyncClient() as client:  # The with block closes the connection for us, even if something fails.
        response = await client.post(
            f"https://api.github.com/app/installations/{installation_id}/access_tokens",  # Ask GitHub for a token for this one install.
            headers={
                "Authorization": f"Bearer {encoded_jwt}",  # Prove we are the app by sending the signed token.
                "Accept": "application/vnd.github.v3+json",  # Ask for the stable version of the API.
            },
        )
        response.raise_for_status()  # A bad key or a removed install fails loudly here, not later.
        return response.json()["token"]  # The token that can read this repo. It lasts one hour.


async def fetch_diff(repo_full_name: str, pr_number: int, token: str) -> str:  # WHAT THIS DOES: Downloads the pull request as a plain git diff.
    async with httpx.AsyncClient() as client:  # The with block closes the connection for us, even if something fails.
        response = await client.get(
            f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}",  # The normal pull request address.
            headers={
                "Authorization": f"Bearer {token}",  # The install token from the function above.
                "Accept": "application/vnd.github.v3.diff",  # This header is the trick: it returns a diff instead of JSON.
            },
        )
        response.raise_for_status()  # A deleted pull request or a missing permission fails loudly here.
        return response.text  # Plain text, not JSON, because of the Accept header above.

# ---------------------------------------------------------------------------
# WHAT THIS FILE IS FOR
# This is the orchestrator service. It runs one review from end to end and is
# the only service that talks to GitHub, to the database, and to the model side
# of the system in the same request.
# The order matters. First it swaps the app's signed token for a token that can
# read one repo, then it downloads the pull request as a plain git diff. Next it
# loads the ten most common lessons we have learned about that repo and hands
# them, with the diff, to the four-agent graph in graph.py. When the agents come
# back it saves every finding to the database first, then asks the reviewer
# service to post them on GitHub, so a failure to post never loses the work.
# The thinking lives in graph.py. This file only fetches, stores, and passes on.
# ---------------------------------------------------------------------------
