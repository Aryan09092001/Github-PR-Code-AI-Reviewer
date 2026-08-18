import uuid  # Makes the random id when Python creates the row instead of the database.
from typing import Any  # Used below to say a finding may hold values of any type.
from sqlalchemy import BigInteger, Column, Integer, Text  # The column types this table needs.
from sqlalchemy.dialects.postgresql import UUID  # Postgres has a real UUID type, so we use it instead of text.
from sqlalchemy.ext.declarative import declarative_base  # Builds the base class every table class inherits from.
from sqlalchemy.sql import func  # Lets us call database functions such as now() from Python.
from sqlalchemy import TIMESTAMP  # The date and time column type.
from pydantic import BaseModel  # Checks the shape of data coming in over HTTP. Not a database table.
from pydantic_settings import BaseSettings  # Reads settings from environment variables and checks their types.

Base = declarative_base()  # Every table class below inherits from this. It collects them into one schema.


class PullRequest(Base):  # WHAT THIS CLASS IS: The pull_requests table. This service only updates the status column.
    __tablename__ = "pull_requests"  # The real table name in Postgres. It must match the Alembic migration.

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # A random id, so nobody can guess the next one.
    repo_full_name = Column(Text, nullable=False)  # The repository, written as "owner/repo".
    pr_number = Column(Integer, nullable=False)  # The pull request number inside that repository.
    head_sha = Column(Text, nullable=False)  # The exact commit that was reviewed.
    installation_id = Column(BigInteger, nullable=False)  # Which GitHub App install to use. BigInteger because the ids are large.
    status = Column(Text, nullable=False, default="pending")  # main.py sets this to "reviewed" once GitHub accepts the review.
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())  # The database sets the time when the row is inserted.


class ReviewRequest(BaseModel):  # WHAT THIS CLASS IS: The shape of the body POSTed to /post-review. Pydantic rejects anything else.
    pr_id: uuid.UUID  # Which pull_requests row to mark as reviewed when we are done.
    repo_full_name: str  # The repository, written as "owner/repo".
    pr_number: int  # Which pull request to comment on.
    installation_id: int  # Used to get a GitHub token that can write to this repo.
    findings: list[dict[str, Any]]  # Loose on purpose: these came from a model, so we check each field in main.py instead.


class Settings(BaseSettings):  # WHAT THIS CLASS IS: All settings this service needs, read from the environment.
    database_url: str = "postgresql+asyncpg://user:password@postgres:5432/codereviewer"  # asyncpg is the async Postgres driver. "postgres" is a Docker Compose service name.
    github_app_id: str = ""  # Goes inside the signed token, telling GitHub which app we are.
    github_app_private_key: str = ""  # Signs that token. This is the most sensitive value here, never commit it.

    class Config:  # WHAT THIS CLASS IS: Tells pydantic where to look for the values above.
        env_file = ".env"  # Read a local .env file in development. Real environment variables win over it.

# ---------------------------------------------------------------------------
# WHAT THIS FILE IS FOR
# This file holds the shapes the reviewer service works with, and nothing that
# acts. It is the smallest models file in the project, because this service does
# the least with the database.
# PullRequest is the Python picture of the pull_requests table, and only one
# column of it is really used here: main.py sets status to "reviewed" after
# GitHub accepts the review. It must stay in step with the Alembic migration in
# db/migrations, which owns the real table. ReviewRequest is not a table, it is
# the shape of the body FastAPI accepts at /post-review, so a bad request
# becomes a 422 before our code runs. Settings holds only what this service
# needs: the database and the two GitHub App values used to sign a token.
# ---------------------------------------------------------------------------
