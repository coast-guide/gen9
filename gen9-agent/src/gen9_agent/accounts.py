"""Removing a user's data from Gen9, as steps the deletion workflows run as Activities
(`workflows/deletion.py`, `deletion.py`): every step is safe to repeat.

Also finds users deleted in Keycloak directly (its admin console), which Gen9 never hears of: each
candidate is confirmed with its own lookup (404) before anything is deleted, so an incomplete
user listing can never cause a deletion.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from langgraph.store.base import BaseStore
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from temporalio.client import Client

from .keycloak_admin import KeycloakAdmin
from .memory import erase_memory
from .models import Thread, User
from .runs.control import stop_thread_runs


@dataclass(frozen=True)
class MissingUser:
    sub: str
    since: datetime  # their first visit: their traces can't be older


async def delete_user_data(
    sub: str,
    session: AsyncSession,
    engine: AsyncEngine,
    checkpointer: Any,
    temporal: Client,
    store: BaseStore,
) -> None:
    """Every chat of the user (hidden at once, its running answer stopped), their LangGraph
    checkpoints, their memory, then the user row (threads, runs and events cascade)."""
    # First, and whatever else is there: memory is keyed by sub, not by the user row
    await erase_memory(store, sub)
    user = await session.scalar(select(User).where(User.sub == sub))
    if user is None:
        return
    await session.execute(
        update(Thread)
        .where(Thread.user_id == user.id, Thread.deleted_at.is_(None))
        .values(deleted_at=func.now())
    )
    await session.commit()
    thread_ids = list(
        await session.scalars(select(Thread.id).where(Thread.user_id == user.id))
    )
    await stop_thread_runs(engine, temporal, thread_ids)
    for thread_id in thread_ids:
        await checkpointer.adelete_thread(str(thread_id))
    await session.delete(user)
    await session.commit()


async def find_deleted_users(
    session: AsyncSession, keycloak: KeycloakAdmin
) -> list[MissingUser]:
    """Users Gen9 knows who no longer exist in Keycloak, each confirmed by its own lookup."""
    rows = (await session.execute(select(User.sub, User.created_at))).all()
    known = await keycloak.user_ids()
    missing = []
    for sub, created_at in rows:
        if sub in known or await keycloak.user_exists(sub):
            continue
        missing.append(MissingUser(sub, created_at))
    return missing
