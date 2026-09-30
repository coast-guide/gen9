"""Making a finished run searchable (`chat_search`, served by `api/search.py`).

First its text, a question and answer, which BM25 can find at once. Then their embedding through
the model router's `embed` alias, for search by meaning: on behalf of the run's user after a run,
and of no one when the reindex workflow re-embeds or backfills (`workflows/search.py`). The run's
workflow retries this Activity while the router is down (`workflows/runs.py`, INDEX_RETRY).
Repeating it rewrites the same row, with whatever model `embed` is now.
"""

import contextlib
import uuid

from sqlalchemy import func, literal, select, update
from sqlalchemy.dialects.postgresql import aggregate_order_by, insert
from temporalio.exceptions import ApplicationError

from ..model_router import OverBudget, embed, on_behalf_of
from ..models import ChatSearch, Run, RunEvent, Thread
from ..runtime import Runtime

# About 6,000 tokens: within text-embedding-3-small's 8,191, with room for other models
MAX_EMBED_CHARS = 24_000


async def index_run(runtime: Runtime, run_id: uuid.UUID, user_sub: str | None) -> bool:
    """Returns False when there is nothing to index (the run or its chat is gone meanwhile)."""
    async with runtime.engine.begin() as conn:
        found = (
            await conn.execute(
                select(Run.thread_id, Thread.user_id, Run.input)
                .join(Thread, Thread.id == Run.thread_id)
                .where(
                    Run.id == run_id,
                    Run.status == "success",
                    Thread.deleted_at.is_(None),
                )
            )
        ).first()
        if found is None:
            return False
        answer = await conn.scalar(
            select(
                func.string_agg(
                    RunEvent.data["text"].astext,
                    aggregate_order_by(literal("\n\n"), RunEvent.seq),
                )
            ).where(RunEvent.run_id == run_id, RunEvent.type == "message.completed")
        )
        body = f"{found.input['message']}\n\n{answer or ''}".strip()
        await conn.execute(
            insert(ChatSearch)
            .values(
                run_id=run_id,
                thread_id=found.thread_id,
                user_id=found.user_id,
                body=body,
            )
            .on_conflict_do_update(
                index_elements=[ChatSearch.run_id], set_={"body": body}
            )
        )

    attribution = on_behalf_of(user_sub) if user_sub else contextlib.nullcontext()
    try:
        with attribution:
            [embedding], model = await embed(
                runtime.settings, runtime.models, [body[:MAX_EMBED_CHARS]]
            )
    except OverBudget:
        # The text stays searchable by keyword; retrying won't help until the budget resets
        raise ApplicationError(
            "user over their model budget", non_retryable=True
        ) from None
    async with runtime.engine.begin() as conn:
        await conn.execute(
            update(ChatSearch)
            .where(ChatSearch.run_id == run_id)
            .values(embedding=embedding, embed_model=model)
        )
    return True
