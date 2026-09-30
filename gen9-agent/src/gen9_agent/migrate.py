"""`gen9-agent-migrate`: apply all schema changes, then exit.

1. Alembic migrations for the app tables (schema `public`).
2. LangGraph's own checkpoint migrations (schema `langgraph`), which it tracks itself.
3. What the services' role may do (`services_privileges`), asserted again every time.
Run before starting the API (Compose runs it as a one-shot service), as the owner of the
database, `gen9_agent`: the only process that holds the owner's password.
"""

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore
from sqlalchemy import Connection, text

from .agent import CHECKPOINT_SCHEMA, checkpoint_pool
from .db import create_engine
from .settings import DatabaseSettings

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

# The role the API and worker connect as (gen9-postgres creates it: scripts/ensure-roles.sh). It
# owns nothing and may create nothing: it gets rows, and search_index.py's functions for the one
# thing it builds (docs/plans/harness.md, "The services stop owning the tables")
SERVICES_ROLE = "gen9_agent_app"


def services_privileges(owner: str) -> list[str]:
    """The grants that make `SERVICES_ROLE` able to work on the data and nothing more. Safe to
    repeat, so every run restores them: tables made by later migrations get the same through
    default privileges for `owner`, the role that runs the migrations."""
    schemas = f"public, {CHECKPOINT_SCHEMA}"
    rows = "select, insert, update, delete"
    return [
        f"grant usage on schema {CHECKPOINT_SCHEMA} to {SERVICES_ROLE}",
        f"grant {rows} on all tables in schema {schemas} to {SERVICES_ROLE}",
        f"grant usage, select on all sequences in schema {schemas} to {SERVICES_ROLE}",
        (
            f"alter default privileges for role {owner} in schema {schemas}"
            f" grant {rows} on tables to {SERVICES_ROLE}"
        ),
        (
            f"alter default privileges for role {owner} in schema {schemas}"
            f" grant usage, select on sequences to {SERVICES_ROLE}"
        ),
        # Who did what is written, never changed (audit.py; its trigger refuses even the owner)
        f"revoke update, delete on audit_events from {SERVICES_ROLE}",
        # The migrations' own bookkeeping: read (readyz checks it), never written
        f"revoke insert, update, delete on alembic_version from {SERVICES_ROLE}",
    ]


def _upgrade(connection: Connection, config: Config) -> None:
    """Runs inside `AsyncConnection.run_sync`: Alembic's command API is synchronous."""
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


async def _migrate() -> None:
    # The database alone: this container holds no other secret (compose.yaml)
    settings = DatabaseSettings()
    engine = create_engine(settings)
    try:
        config = Config()
        config.set_main_option("script_location", str(MIGRATIONS_DIR))
        async with engine.begin() as conn:
            await conn.run_sync(_upgrade, config)
        async with engine.begin() as conn:
            await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {CHECKPOINT_SCHEMA}"))
    finally:
        await engine.dispose()
    async with checkpoint_pool(settings) as pool:
        await AsyncPostgresSaver(pool).setup()  # ty: ignore[invalid-argument-type]
        # The store of each person's memory (memory.py), in the same schema
        await AsyncPostgresStore(pool).setup()  # ty: ignore[invalid-argument-type]
    await _grant_services(settings)


async def _grant_services(settings: DatabaseSettings) -> None:
    engine = create_engine(settings)
    try:
        async with engine.begin() as conn:
            quote = conn.dialect.identifier_preparer.quote
            owner = await conn.scalar(text("select current_user"))
            for statement in services_privileges(quote(str(owner))):
                await conn.execute(text(statement))
            # LangGraph's own bookkeeping, whatever tables its versions keep it in
            tables = (
                await conn.scalars(
                    text(
                        "select tablename from pg_tables where schemaname = :schema"
                        " and tablename like '%\\_migrations'"
                    ),
                    {"schema": CHECKPOINT_SCHEMA},
                )
            ).all()
            for table in tables:
                await conn.execute(
                    text(
                        f"revoke insert, update, delete on {CHECKPOINT_SCHEMA}.{quote(table)}"
                        f" from {SERVICES_ROLE}"
                    )
                )
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(_migrate())
    print("migrations applied")


if __name__ == "__main__":
    main()
