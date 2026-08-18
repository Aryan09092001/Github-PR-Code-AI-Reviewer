import uuid  # Makes the random id when Python creates the row instead of the database.
from sqlalchemy import BigInteger, Column, Integer, Text, ForeignKey  # The column types and the link between two tables.
from sqlalchemy.dialects.postgresql import UUID  # Postgres has a real UUID type, so we use it instead of text.
from sqlalchemy.ext.declarative import declarative_base  # Builds the base class every table class inherits from.
from sqlalchemy.sql import func  # Lets us call database functions such as now() from Python.
from sqlalchemy import TIMESTAMP  # The date and time column type.
from pydantic import BaseModel  # Checks the shape of data coming in over HTTP. Not a database table.
from pydantic_settings import BaseSettings  # Reads settings from environment variables and checks their types.

Base = declarative_base()  # Every table class below inherits from this. It collects them into one schema.


class PullRequest(Base):  # WHAT THIS CLASS IS: The pull_requests table. One object is one pull request we reviewed.
    __tablename__ = "pull_requests"  # The real table name in Postgres. It must match the Alembic migration.

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # A random id, so nobody can guess the next one.
    repo_full_name = Column(Text, nullable=False)  # The repository, written as "owner/repo".
    pr_number = Column(Integer, nullable=False)  # The pull request number inside that repository.
    head_sha = Column(Text, nullable=False)  # The exact commit we reviewed. It changes when someone pushes again.
    installation_id = Column(BigInteger, nullable=False)  # Which GitHub App install to use when we call back. BigInteger because the ids are large.
    status = Column(Text, default="pending")  # How far the review got: pending, running, or done.
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())  # The database sets the time when the row is inserted.


class Finding(Base):  # WHAT THIS CLASS IS: The findings table. One object is one comment an agent produced.
    __tablename__ = "findings"  # The real table name in Postgres. It must match the Alembic migration.

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # A random id, made by Python before the insert.
    pr_id = Column(UUID(as_uuid=True), ForeignKey("pull_requests.id"), nullable=True)  # The link home. Postgres refuses an id with no matching pull request.
    file = Column(Text, nullable=True)  # The file the comment is about. Nullable, because the model may leave it out.
    line = Column(Integer, nullable=True)  # The line inside that file.
    severity = Column(Text, nullable=True)  # How serious it is: info, warning, or error.
    message = Column(Text, nullable=True)  # The text we post as a comment on GitHub.
    agent = Column(Text, nullable=True)  # Which of the four agents found it.
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())  # The database sets the time when the row is inserted.


class Pattern(Base):  # WHAT THIS CLASS IS: The patterns table. One object is one lesson we learned about a repo.
    __tablename__ = "patterns"  # The real table name in Postgres. It must match the Alembic migration.

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # A random id, made by Python before the insert.
    repo_full_name = Column(Text, nullable=False)  # Lessons belong to one repository, not to all of them.
    pattern_text = Column(Text, nullable=False)  # The lesson itself. main.py feeds this to the style agent.
    frequency = Column(Integer, default=1)  # How many times we have seen it. main.py sorts by this and keeps the top ten.
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now())  # Careful: this is set on insert, not on update.


class AnalyzeRequest(BaseModel):  # WHAT THIS CLASS IS: The shape of the body POSTed to /analyze. Pydantic rejects anything else.
    pr_id: uuid.UUID  # Which pull_requests row this review belongs to.
    pr_number: int  # The pull request number inside the repository.
    repo_full_name: str  # The repository, written as "owner/repo".
    head_sha: str  # The commit to review. Accepted here, though the diff is fetched by pull request number.
    installation_id: int  # Used to get a GitHub token that can read this repo.


class Settings(BaseSettings):  # WHAT THIS CLASS IS: All settings this service needs, read from the environment.
    database_url: str = "postgresql+asyncpg://user:password@postgres:5432/codereviewer"  # asyncpg is the async Postgres driver. "postgres" is a Docker Compose service name.
    redis_url: str = "redis://redis:6379/0"  # Redis is the Celery queue. This service does not use it yet.
    github_app_id: str = ""  # Goes inside the signed token, telling GitHub which app we are.
    github_app_private_key: str = ""  # Signs that token. This is the most sensitive value here, never commit it.
    openai_api_key: str = ""  # Read by the OpenAI client in graph.py straight from the environment.
    langfuse_public_key: str = ""  # Langfuse records every model call, so we can see cost and prompts.
    langfuse_secret_key: str = ""  # The other half of the Langfuse pair.
    langfuse_host: str = "http://langfuse:3000"  # Where Langfuse runs. Another Docker Compose service name.

    class Config:  # WHAT THIS CLASS IS: Tells pydantic where to look for the values above.
        env_file = ".env"  # Read a local .env file in development. Real environment variables win over it.

# ---------------------------------------------------------------------------
# WHAT THIS FILE IS FOR
# This file holds the shapes the orchestrator works with, and nothing that acts.
# There are three kinds of class here. PullRequest, Finding, and Pattern are the
# Python pictures of the three database tables, so main.py can read and write
# rows without writing SQL. They must stay in step with the Alembic migration in
# db/migrations, because Alembic owns the real tables and these classes only
# mirror them. AnalyzeRequest is different: it is not a table, it is the shape of
# the request body FastAPI accepts at /analyze, and pydantic turns a bad body
# into a 422 before our code runs. Settings holds every address and key the
# service needs, with defaults that only work inside Docker Compose. In
# production, pass real values as environment variables to override them.
# ---------------------------------------------------------------------------
