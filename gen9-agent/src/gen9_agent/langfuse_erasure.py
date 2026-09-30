"""Erase traces from Langfuse, a user's (account deletion) or one chat's (deleting a chat), following
Langfuse's procedure for deletion requests: list the observations by `userId` or by `sessionId`
(a chat's thread id) with the v2 Observations API over their lifetime, collect their trace IDs,
and delete those in batches of at most 1,000. Deleting a trace
also deletes its observations and scores. Langfuse deletes asynchronously (usually within
minutes). https://langfuse.com/docs/administration/data-deletion
"""

import os
from datetime import UTC, datetime, timedelta
from typing import Any

BATCH = 1000


def langfuse_configured() -> bool:
    return bool(
        os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")
    )


async def erase_user_traces(user_id: str, since: datetime, api: Any = None) -> int:
    """Submits every trace of `user_id` since `since` for deletion; returns how many."""
    return await erase_traces({"user_id": user_id}, since, api)


async def erase_session_traces(
    session_id: str, since: datetime, api: Any = None
) -> int:
    """Submits every trace of one chat (`session_id` = its thread id) for deletion."""
    return await erase_traces({"session_id": session_id}, since, api)


async def erase_traces(match: dict[str, str], since: datetime, api: Any = None) -> int:
    """Submits every trace with an observation matching `match` since `since` for deletion."""
    if api is None:
        if not langfuse_configured():
            return 0
        from langfuse import get_client

        api = get_client().async_api  # the client tracing already uses (LANGFUSE_* env)

    trace_ids: set[str] = set()
    pending: list[str] = []
    cursor: str | None = None
    # A day of margin for clock skew between Postgres and Langfuse
    window = {
        "from_start_time": since - timedelta(days=1),
        "to_start_time": datetime.now(UTC),
    }
    while True:
        page = await api.observations.get_many(
            **match, fields="core", limit=100, cursor=cursor, **window
        )
        for observation in page.data:
            if observation.trace_id and observation.trace_id not in trace_ids:
                trace_ids.add(observation.trace_id)
                pending.append(observation.trace_id)
                if len(pending) == BATCH:
                    await api.trace.delete_multiple(trace_ids=pending)
                    pending = []
        cursor = page.meta.cursor
        if not cursor:
            break
    if pending:
        await api.trace.delete_multiple(trace_ids=pending)
    return len(trace_ids)
