import asyncio  # needed because the DB driver is async but Alembic's CLI is sync
import os  # only to read DATABASE_URL from the environment
from logging.config import fileConfig  # reads the [loggers] blocks out of alembic.ini

from sqlalchemy import pool  # gives NullPool, used below
from sqlalchemy.engine import Connection  # type hint only, for do_run_migrations
from sqlalchemy.ext.asyncio import async_engine_from_config  # builds an async engine from ini keys

from alembic import context  # the live migration context Alembic hands to this script

config = context.config  # parsed alembic.ini, plus anything passed via -x on the CLI

if config.config_file_name is not None:  # skipped when Alembic runs without an ini file
    fileConfig(config.config_file_name)  # apply that ini's logging setup

database_url = os.environ.get("DATABASE_URL", config.get_main_option("sqlalchemy.url"))  # env wins over ini
config.set_main_option("sqlalchemy.url", database_url)  # write it back so the engine below picks it up

target_metadata = None  # no models wired yet, so `--autogenerate` produces empty migrations


def run_migrations_offline() -> None:  # PURPOSE: emit SQL to stdout instead of touching a DB (`alembic upgrade head --sql`)
    url = config.get_main_option("sqlalchemy.url")  # used for dialect detection only; never connected to
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,  # inline values into the SQL, since there is no connection to bind params on
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():  # wraps the output in BEGIN/COMMIT
        context.run_migrations()  # runs upgrade()/downgrade() in each pending version file


def do_run_migrations(connection: Connection) -> None:  # PURPOSE: the sync body run inside the async connection
    context.configure(connection=connection, target_metadata=target_metadata)  # bind Alembic to this connection
    with context.begin_transaction():  # one transaction: a failing migration rolls back
        context.run_migrations()  # apply the pending version files


async def run_async_migrations() -> None:  # PURPOSE: open the async engine and hand a connection to Alembic
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),  # all sqlalchemy.* keys from the [alembic] section
        prefix="sqlalchemy.",  # only read keys with this prefix
        poolclass=pool.NullPool,  # no pooling: this is a one-shot CLI run, connection closes after
    )
    async with connectable.connect() as connection:  # open the real DB connection
        await connection.run_sync(do_run_migrations)  # Alembic's API is sync, so run it via run_sync
    await connectable.dispose()  # close the engine so the process can exit cleanly


def run_migrations_online() -> None:  # PURPOSE: sync entry point Alembic calls; bridges into asyncio
    asyncio.run(run_async_migrations())  # own event loop, since the CLI has none


if context.is_offline_mode():  # true when --sql was passed
    run_migrations_offline()  # print SQL, connect to nothing
else:
    run_migrations_online()  # normal path: connect and apply

# ---------------------------------------------------------------------------
# PURPOSE OF THIS FILE
# Alembic's entry point — the script it executes on every `alembic` command,
# before any version file in versions/ runs. Its job is to decide which database
# to connect to (DATABASE_URL, falling back to alembic.ini), set up logging, and
# open a connection for Alembic to apply migrations through. Two modes: offline
# prints SQL without connecting, online connects for real. The async plumbing
# exists only because the driver is asyncpg while Alembic's own API is sync.
# ---------------------------------------------------------------------------
