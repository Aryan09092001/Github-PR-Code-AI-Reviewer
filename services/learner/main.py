from fastapi import FastAPI  # The application object.
from prometheus_fastapi_instrumentator import Instrumentator  # Adds request metrics without writing any code.
from sqlalchemy import select, update  # select builds the query below. update is imported but never used here.

from sqlalchemy.dialects.postgresql import insert  # The Postgres insert, the only one with on_conflict_do_update.
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # The async database session and engine.
from sqlalchemy.orm import sessionmaker  # A factory that hands us a new session whenever we need one.

from models import Settings, Finding, Pattern, LearnRequest  # Settings, the two tables, and the shape of the incoming request.

settings = Settings()  # Read the settings once when the file loads, not on every request.
engine = create_async_engine(settings.database_url)  # One engine for the whole service. It holds the connection pool.
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)  # expire_on_commit=False lets us read a row after commit.

app = FastAPI()  # The application object. Uvicorn runs this.
Instrumentator().instrument(app).expose(app)  # Measure every route and publish the numbers at /metrics.


@app.get("/health")
async def health():  # WHAT THIS DOES: Tells Docker, Kubernetes, and the load balancer that this service is alive.
    return {"status": "ok"}  # Answers without touching the database, so it stays fast and never fails by accident.


@app.post("/learn")
async def learn(request: LearnRequest):  # WHAT THIS DOES: Turns the findings of one merged pull request into lasting lessons for that repo.
    async with AsyncSessionLocal() as session:  # The with block closes the session for us, even if something fails.
        result = await session.execute(  # Read back what the reviewers said about this pull request.
            select(Finding)
            .where(
                Finding.pr_id == request.pr_id,  # Only this pull request's findings.
                Finding.severity.in_(["warning", "error"]),  # Skip info. Only real problems are worth remembering.
            )
        )
        findings = result.scalars().all()  # The rows themselves, not the wrapper around them.

        for finding in findings:  # Each finding becomes a lesson, or makes an old lesson stronger.
            stmt = (
                insert(Pattern)  # The Postgres insert, because we need the clause below.
                .values(
                    repo_full_name=request.repo_full_name,  # Lessons belong to one repository, not to all of them.
                    pattern_text=finding.message,  # The finding's text is the lesson we store.
                    frequency=1,  # First time we have seen it.
                )
                .on_conflict_do_update(  # This is the whole trick: insert if new, count up if we have seen it before.
                    index_elements=["repo_full_name", "pattern_text"],  # Matches the UNIQUE constraint in the migration.
                    set_={"frequency": Pattern.frequency + 1},  # Postgres does the adding, so two workers cannot lose a count.
                )
            )
            await session.execute(stmt)  # One statement per finding. Nothing is written until the commit below.

        await session.commit()  # Write them all together, so a failure leaves no half-learned state.

    return {"status": "ok"}  # The caller is a Celery worker, so nobody is waiting on this answer.

# ---------------------------------------------------------------------------
# WHAT THIS FILE IS FOR
# This is the learner service, the part that makes the system get better over
# time instead of reviewing every pull request as if it were the first.
# It runs only after a pull request is merged. The webhook service queues a job,
# that job calls /learn, and this file reads back the findings we saved for that
# pull request. Anything marked info is dropped; only warnings and errors are
# worth keeping. Each surviving finding is written into the patterns table, and
# if that exact lesson already exists for that repo, its frequency counter goes
# up by one instead. Postgres does that counting inside a single statement, so
# two workers running at once cannot lose a count. The orchestrator later reads
# the ten most frequent lessons for a repo and feeds them to the style agent.
# ---------------------------------------------------------------------------
