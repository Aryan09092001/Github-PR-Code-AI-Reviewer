import uuid  # Makes the random id when Python creates the row instead of the database.
from sqlalchemy import Column, Integer, Text, ForeignKey  # The column types and the link between two tables.
from sqlalchemy.dialects.postgresql import UUID  # Postgres has a real UUID type, so we use it instead of text.
from sqlalchemy.ext.declarative import declarative_base  # Builds the base class every table class inherits from.
from sqlalchemy.sql import func  # Lets us call database functions such as now() from Python.
from sqlalchemy import TIMESTAMP  # The date and time column type.
from pydantic import BaseModel  # Checks the shape of data coming in over HTTP. Not a database table.
from pydantic_settings import BaseSettings  # Reads settings from environment variables and checks their types.

Base = declarative_base()  # Every table class below inherits from this. It collects them into one schema.


class Finding(Base):  # WHAT THIS CLASS IS: The findings table. This service only reads it, never writes to it.
    __tablename__ = "findings"  # The real table name in Postgres. It must match the Alembic migration.

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # A random id, so nobody can guess the next one.
    pr_id = Column(UUID(as_uuid=True), ForeignKey("pull_requests.id"), nullable=True)  # The link home. main.py filters on this column.
    file = Column(Text, nullable=True)  # The file the comment was about. Not used when learning.
    line = Column(Integer, nullable=True)  # The line inside that file. Not used when learning.
    severity = Column(Text, nullable=True)  # info, warning, or error. main.py keeps only the last two.
    message = Column(Text, nullable=True)  # The finding's text. This becomes the lesson we store.
    agent = Column(Text, nullable=True)  # Which of the four agents found it.
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())  # The database sets the time when the row is inserted.


class Pattern(Base):  # WHAT THIS CLASS IS: The patterns table. This is the only table this service writes to.
    __tablename__ = "patterns"  # The real table name in Postgres. It must match the Alembic migration.

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # A random id, made by Python before the insert.
    repo_full_name = Column(Text, nullable=False)  # Lessons belong to one repository, not to all of them.
    pattern_text = Column(Text, nullable=False)  # The lesson itself. With the column above it forms the unique pair.
    frequency = Column(Integer, default=1)  # How many times we have seen it. main.py counts this up on a repeat.
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now())  # Careful: this is set on insert, not on update.


class LearnRequest(BaseModel):  # WHAT THIS CLASS IS: The shape of the body POSTed to /learn. Pydantic rejects anything else.
    repo_full_name: str  # Which repository the lessons belong to.
    pr_id: uuid.UUID  # Which merged pull request to read the findings from.


class Settings(BaseSettings):  # WHAT THIS CLASS IS: All settings this service needs, read from the environment.
    database_url: str = "postgresql+asyncpg://user:password@postgres:5432/codereviewer"  # asyncpg is the async Postgres driver. "postgres" is a Docker Compose service name.
    redis_url: str = "redis://redis:6379/0"  # Redis is the Celery queue, used by worker.py rather than by main.py.

    class Config:  # WHAT THIS CLASS IS: Tells pydantic where to look for the values above.
        env_file = ".env"  # Read a local .env file in development. Real environment variables win over it.

# ---------------------------------------------------------------------------
# WHAT THIS FILE IS FOR
# This file holds the shapes the learner service works with, and nothing that
# acts. The two table classes here have clearly different jobs, which is worth
# noticing: Finding is read-only for this service, since the orchestrator is what
# writes findings, while Pattern is the one table this service owns and updates.
# Both must stay in step with the Alembic migration in db/migrations, which owns
# the real tables. The unique pair of repo_full_name and pattern_text on Pattern
# is what makes the counting in main.py work, so it cannot be dropped. Then
# LearnRequest is not a table, it is the shape of the body FastAPI accepts at
# /learn, and Settings holds the database and Redis addresses, with defaults
# that only work inside Docker Compose.
# ---------------------------------------------------------------------------
