"""The database not taking a request (P6-B3): a 503 in words from the API, and a Retry that says it
was Gen9, not the model provider."""

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import DBAPIError, IntegrityError

from gen9_agent.db_unavailable import (
    CANT_SAVE,
    NO_ANSWER,
    cannot_write,
    database_unavailable,
    unavailable,
)
from gen9_agent.runs.store import PUBLIC_NO_ANSWER, PUBLIC_NO_SAVE, retry_reason

pytestmark = pytest.mark.asyncio


def wrapped(orig: Exception) -> DBAPIError:
    """As SQLAlchemy raises psycopg's error."""
    return DBAPIError("insert into users …", {}, orig)


@pytest.mark.parametrize(
    ("orig", "said"),
    [
        (psycopg.errors.ReadOnlySqlTransaction("read-only transaction"), CANT_SAVE),
        (psycopg.errors.DiskFull("could not extend file"), CANT_SAVE),
        (psycopg.errors.OutOfMemory("out of memory"), CANT_SAVE),
        (psycopg.errors.AdminShutdown("terminating connection"), NO_ANSWER),
        (psycopg.OperationalError("server closed the connection"), NO_ANSWER),
    ],
)
async def test_what_the_database_not_taking_it_says(orig: Exception, said: str) -> None:
    assert unavailable(wrapped(orig)) == said
    assert cannot_write(wrapped(orig)) == (said == CANT_SAVE)


async def test_any_other_database_error_isnt_mistaken_for_it() -> None:
    other = psycopg.errors.UniqueViolation("duplicate key")
    assert unavailable(wrapped(other)) is None
    assert not cannot_write(IntegrityError("…", {}, other))


def api() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(DBAPIError, database_unavailable)
    app.add_exception_handler(psycopg.Error, database_unavailable)

    @app.post("/read-only")
    async def read_only() -> dict:
        raise wrapped(psycopg.errors.ReadOnlySqlTransaction("read-only transaction"))

    @app.put("/memory")
    async def memory() -> dict:
        # As LangGraph's store raises it: psycopg's own, not wrapped by SQLAlchemy
        raise psycopg.errors.ReadOnlySqlTransaction("read-only transaction")

    @app.post("/duplicate")
    async def duplicate() -> dict:
        raise IntegrityError("…", {}, psycopg.errors.UniqueViolation("duplicate key"))

    return app


async def test_the_api_answers_503_in_words_and_leaves_other_errors_alone() -> None:
    transport = httpx.ASGITransport(app=api(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as http:
        refused = await http.post("/read-only")
        memory = await http.put("/memory")
        other = await http.post("/duplicate")
    assert refused.status_code == 503 and refused.json() == {"detail": CANT_SAVE}
    assert refused.headers["retry-after"] == "30"
    assert memory.status_code == 503 and memory.json() == {"detail": CANT_SAVE}
    assert other.status_code == 500


@pytest.mark.parametrize(
    "failure",
    [
        (
            "ApplicationError: ReadOnlySqlTransaction: cannot execute SELECT FOR UPDATE in a"
            " read-only transaction"
        ),
        "ApplicationError: DiskFull: could not extend file: No space left on device",
        "ApplicationError: OperationalError: consuming input failed",
    ],
)
async def test_a_turn_the_database_stopped_says_so_on_its_retry(failure: str) -> None:
    assert retry_reason(failure) == PUBLIC_NO_SAVE


async def test_a_provider_that_didnt_answer_still_says_that() -> None:
    assert retry_reason("ApplicationError: APIConnectionError: timed out") == (
        PUBLIC_NO_ANSWER
    )
