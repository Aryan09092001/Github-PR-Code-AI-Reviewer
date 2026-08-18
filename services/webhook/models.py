import uuid  # Makes the random id when Python creates the row instead of the database.
from sqlalchemy import BigInteger, Column, Integer, Text  # The column types this table needs.
from sqlalchemy.dialects.postgresql import UUID  # Postgres has a real UUID type, so we use it instead of text.
from sqlalchemy.ext.declarative import declarative_base  # Builds the base class every table class inherits from.
from sqlalchemy.sql import func  # Lets us call database functions such as now() from Python.
from sqlalchemy import TIMESTAMP  # The date and time column type.
from pydantic_settings import BaseSettings  # Reads settings from environment variables and checks their types.

Base = declarative_base()  # Every table class below inherits from this. It collects them into one schema.


class PullRequest(Base):  # WHAT THIS CLASS IS: The Python side of the pull_requests table. One object is one row.
    __tablename__ = "pull_requests"  # The real table name in Postgres. It must match the Alembic migration.

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # A random id, so nobody can guess the next one.
    repo_full_name = Column(Text, nullable=False)  # The repository, written as "owner/repo".
    pr_number = Column(Integer, nullable=False)  # The pull request number inside that repository.
    head_sha = Column(Text, nullable=False)  # The exact commit we reviewed. It changes when someone pushes again.
    installation_id = Column(BigInteger, nullable=False)  # Which GitHub App install to use when we call back. BigInteger because the ids are large.
    status = Column(Text, nullable=False, default="pending")  # How far the review got: pending, running, or done.
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())  # The database sets the time when the row is inserted.


class Settings(BaseSettings):  # WHAT THIS CLASS IS: All settings this service needs, read from the environment.
    database_url: str = "postgresql+asyncpg://user:password@postgres:5432/codereviewer"  # asyncpg is the async Postgres driver. "postgres" is the Docker Compose service name.
    redis_url: str = "redis://redis:6379/0"  # Redis is the queue Celery puts jobs on. "redis" is also a service name.

    class Config:  # WHAT THIS CLASS IS: Tells pydantic where to look for the values above.
        env_file = ".env"  # Read a local .env file in development. Real environment variables win over it.

# ---------------------------------------------------------------------------
# WHAT THIS FILE IS FOR
# This file holds the two things the webhook service needs before it can run:
# the shape of the data, and the settings.
# PullRequest is the Python picture of the pull_requests table. When main.py
# writes or reads a row, it works with this class instead of SQL. The columns
# here must stay in step with the Alembic migration in db/migrations, because
# Alembic owns the real table and this class only mirrors it.
# Settings holds the two addresses this service must know: the database and
# Redis. Both default to Docker Compose service names, so the defaults only
# work inside the container network. In production, pass real values as
# environment variables and they will override what is written here.
# ---------------------------------------------------------------------------
