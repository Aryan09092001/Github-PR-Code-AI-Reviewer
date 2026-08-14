"""initial

Revision ID: 0001
Revises:
Create Date: 2026-07-01 00:00:00.000000

"""
from typing import Sequence, Union  # only used by the revision type hints below

from alembic import op  # op = Alembic's schema-change API (execute, create_table, drop_table)
import sqlalchemy as sa  # sa = column types + constraints used to describe the tables

revision: str = "0001"  # this script's ID; Alembic stores it in the alembic_version table
down_revision: Union[str, None] = None  # None = first migration, nothing runs before it
branch_labels: Union[str, Sequence[str], None] = None  # only for branched histories; unused
depends_on: Union[str, Sequence[str], None] = None  # other revisions required first; none


def upgrade() -> None:  # PURPOSE: build the schema from scratch. Runs on `alembic upgrade head`
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')  # enables gen_random_uuid() below

    op.create_table(  # TABLE 1: one row per pull request picked up for review
        "pull_requests",   # This table tracks (metadata) the PRs that the AI reviewer is currently reviewing or has reviewed. Each row represents a unique pull request identified by its repository and PR number. The table includes columns for the PR's head commit SHA, the installation ID of the GitHub App, the review status, and timestamps for when the PR was created in the system.
        sa.Column(
            "id",  # primary key
            sa.UUID(),  # UUID over serial int: unguessable, generatable anywhere
            server_default=sa.text("gen_random_uuid()"),  # Postgres fills it, not Python
            nullable=False,
        ),
        sa.Column("repo_full_name", sa.Text(), nullable=False),  # e.g. "owner/repo"
        sa.Column("pr_number", sa.Integer(), nullable=False),  # PR number within that repo
        sa.Column("head_sha", sa.Text(), nullable=False),  # commit reviewed; changes on new pushes
        sa.Column("installation_id", sa.BigInteger(), nullable=False),  # GitHub App install, for auth
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),  # pending/running/done
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),  # timezone-aware: stored as UTC, no ambiguity
            server_default=sa.text("now()"),  # DB clock sets it on INSERT
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),  # PRIMARY KEY (id)
    )

    op.create_table(  # TABLE 2: one row per issue the AI reviewer reports
        "findings",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("pr_id", sa.UUID(), nullable=True),  # which PR this finding belongs to
        sa.Column("file", sa.Text(), nullable=True),  # path the comment lands on
        sa.Column("line", sa.Integer(), nullable=True),  # line number within that file
        sa.Column("severity", sa.Text(), nullable=True),  # info/warning/error
        sa.Column("message", sa.Text(), nullable=True),  # comment body posted to GitHub
        sa.Column("agent", sa.Text(), nullable=True),  # which reviewer agent produced it
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["pr_id"], ["pull_requests.id"]),  # pr_id must be a real PR row
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(  # TABLE 3: learned per-repo review patterns (memory across PRs)
        "patterns",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("repo_full_name", sa.Text(), nullable=False),  # patterns are scoped per repo
        sa.Column("pattern_text", sa.Text(), nullable=False),  # the rule/observation itself
        sa.Column("frequency", sa.Integer(), server_default="1", nullable=True),  # times seen
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),  # note: set on INSERT only, not on UPDATE
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repo_full_name", "pattern_text"),  # no duplicate pattern per repo
    )


def downgrade() -> None:  # PURPOSE: undo upgrade() exactly. Runs on `alembic downgrade`
    op.drop_table("patterns")  # reverse order of creation: children dropped before parents
    op.drop_table("findings")  # must go before pull_requests — it holds the foreign key
    op.drop_table("pull_requests")

# ---------------------------------------------------------------------------
# PURPOSE OF THIS FILE
# Alembic migration 0001 — the first version of the database schema for the
# GitHub PR AI Reviewer. Creates three tables: pull_requests (PRs under review),
# findings (review comments, linked to a PR by foreign key), and patterns
# (per-repo lessons reused on later PRs). upgrade() applies the schema,
# downgrade() removes it, and Alembic records which one ran in alembic_version.
# Never edit an applied migration — add a new numbered one instead.
# ---------------------------------------------------------------------------
