import asyncio

from alembic.script import ScriptDirectory
from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import text

from ..agent import CHECKPOINT_SCHEMA
from ..deps import Session
from ..migrate import MIGRATIONS_DIR

router = APIRouter(tags=["health"])


async def schema_head() -> str | None:
    """The Alembic revision this build's code expects. Alembic reads the migration scripts from
    disk and has no async API, so this runs in a thread; the app reads it once at startup."""
    return await asyncio.to_thread(
        lambda: ScriptDirectory(str(MIGRATIONS_DIR)).get_current_head()
    )


async def schema_revisions() -> frozenset[str]:
    """Every Alembic revision this build's code knows. A database at a revision outside it was
    migrated by a newer Gen9 (found by hand: an older build started on an upgraded database,
    manual-e2e.md, P2-G2). In a thread, as schema_head."""
    return await asyncio.to_thread(
        lambda: frozenset(
            script.revision
            for script in ScriptDirectory(str(MIGRATIONS_DIR)).walk_revisions()
        )
    )


@router.get("/healthz", summary="Liveness")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/readyz",
    summary="Readiness: the database answers and has this version's schema",
)
async def readyz(session: Session, request: Request) -> dict[str, str]:
    # A database that answers isn't enough: recreated empty (make wipe STACKS=postgres), it has no
    # tables until gen9-agent-migrate runs again, and every request would fail
    try:
        tracked = await session.scalar(
            text("select to_regclass('public.alembic_version') is not null")
        )
        version = (
            await session.scalar(text("select version_num from public.alembic_version"))
            if tracked
            else None
        )
        checkpoints = await session.scalar(
            text(
                f"select to_regclass('{CHECKPOINT_SCHEMA}.checkpoint_migrations') is not null"
            )
        )
    except Exception as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Database unavailable"
        ) from exc
    known = getattr(request.app.state, "schema_revisions", frozenset())
    if version is not None and known and version not in known:
        # Migrating can't help: this code is older than the database (a rollback)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Database is from a newer Gen9 (revision {version}): run that version, or restore "
            "a backup made with this one (make restore)",
        )
    if version != request.app.state.schema_head or not checkpoints:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Database not migrated: restart gen9-agent (make up STACKS=agent)",
        )
    return {"status": "ready"}
