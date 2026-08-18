from fastapi import FastAPI, Request  # The application object and the raw incoming request.
from prometheus_fastapi_instrumentator import Instrumentator  # Adds request metrics without writing any code.
from sqlalchemy import select  # Builds SELECT queries in Python instead of writing SQL strings.
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # The async database session and engine.
from sqlalchemy.orm import sessionmaker  # A factory that hands us a new session whenever we need one.

from models import Settings, PullRequest, Base  # Our settings, the pull_requests table, and the ORM base class.
from worker import analyze_pr, trigger_learning  # The two background jobs. We queue them, we do not run them here.

settings = Settings()  # Read the settings once when the file loads, not on every request.
engine = create_async_engine(settings.database_url)  # One engine for the whole service. It holds the connection pool.
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)  # expire_on_commit=False lets us read a row after commit.

app = FastAPI()  # The application object. Uvicorn runs this.
Instrumentator().instrument(app).expose(app)  # Measure every route and publish the numbers at /metrics.


@app.get("/health")
async def health():  # WHAT THIS DOES: Tells Docker, Kubernetes, and the load balancer that this service is alive.
    return {"status": "ok"}  # Answers without touching the database, so it stays fast and never fails by accident.


@app.post("/events", status_code=202)
async def receive_event(request: Request):  # WHAT THIS DOES: Reads one GitHub event, saves the pull request, and queues the real work.
    body = await request.json()  # The event, already checked by the gateway, so we can trust it here.
    action = body.get("action", "")  # What happened: opened, reopened, synchronize, closed, and so on.
    pull_request = body.get("pull_request", {})  # The pull request part of the event. Empty for events that have none.

    if action == "closed" and pull_request.get("merged"):  # A merged pull request is the one case we can learn from.
        pr_number = pull_request.get("number")  # The pull request number inside the repository.
        repo_full_name = body.get("repository", {}).get("full_name", "")  # The repository, written as "owner/repo".
        async with AsyncSessionLocal() as session:  # The with block closes the session for us, even if something fails.
            result = await session.execute(  # Look for the row we saved when this pull request was opened.
                select(PullRequest).where(
                    PullRequest.repo_full_name == repo_full_name,  # Match the repository.
                    PullRequest.pr_number == pr_number,  # Match the pull request number.
                )
            )
            pr = result.scalar_one_or_none()  # Gives us the row, or None if we never saw this pull request.
            if pr:  # Only learn from pull requests we actually reviewed.
                trigger_learning.apply_async(args=[repo_full_name, str(pr.id)], queue="learning")  # Hand the job to Celery and move on.
        return {"status": "accepted"}  # Answer GitHub straight away. The learning happens in the background.

    if action not in ("opened", "reopened", "synchronize"):  # These three mean there is new code to review.
        return {"status": "skipped"}  # Anything else is not our business, so we stop here.

    pr_number = pull_request.get("number")  # The pull request number inside the repository.
    repo_full_name = body.get("repository", {}).get("full_name", "")  # The repository, written as "owner/repo".
    head_sha = pull_request.get("head", {}).get("sha", "")  # The newest commit. It changes every time someone pushes.
    installation_id = body.get("installation", {}).get("id", 0)  # Tells us which GitHub App install to use when we call back.

    async with AsyncSessionLocal() as session:  # The with block closes the session for us, even if something fails.
        result = await session.execute(  # Have we already started on this exact commit?
            select(PullRequest).where(
                PullRequest.repo_full_name == repo_full_name,  # Match the repository.
                PullRequest.pr_number == pr_number,  # Match the pull request number.
                PullRequest.head_sha == head_sha,  # Match the commit too, so a new push is treated as new work.
            )
        )
        if result.scalar_one_or_none():  # A row already exists, so this is a repeat delivery from GitHub.
            return {"status": "already_processing"}  # Stop here. Reviewing the same commit twice wastes money.

        pr_record = PullRequest(  # Build the row that tracks this review from start to finish.
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            head_sha=head_sha,
            installation_id=installation_id,
            status="pending",  # Nothing has run yet. The orchestrator moves this along.
        )
        session.add(pr_record)  # Stage the row. Nothing is written to the database yet.
        await session.commit()  # Now write it, so the row exists before any job can look for it.
        await session.refresh(pr_record)  # Read back the values the database filled in, such as the id.
        pr_id = str(pr_record.id)  # Celery sends plain JSON, so the UUID has to become text.

    analyze_pr.apply_async(args=[pr_id, pr_number, repo_full_name, head_sha, installation_id], queue="webhook")  # Queue the review outside the session.
    return {"status": "accepted"}  # 202 means "I have taken this, I have not finished it". GitHub is happy with that.

# ---------------------------------------------------------------------------
# WHAT THIS FILE IS FOR
# This is the webhook service. It sits behind the gateway, so it is not open to
# the internet and it never checks signatures. The gateway has already proved
# the event came from GitHub before it gets here.
# Its job is to decide what an event means and record it. Three actions matter
# to it: opened, reopened, and synchronize all mean there is new code, so it
# saves a pull_requests row and queues the analyze_pr job. A merged pull
# request instead queues trigger_learning, so we learn from it later.
# Everything else is ignored. It also refuses work it has already started on
# the same commit, because GitHub retries deliveries and reviewing twice costs
# money. The slow work always goes to Celery, never inline, so GitHub gets its
# answer in milliseconds. This file also answers /health and /metrics.
# ---------------------------------------------------------------------------
