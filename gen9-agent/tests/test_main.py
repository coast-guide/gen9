"""The API's access log (main.py): health checks that passed are left out, so `make logs` shows
real requests; a failing health check and every other request still show. What a person typed
into a query (a search, a file's name, an email looked up) is masked; counts and cursors stay."""

import logging

import pytest

from gen9_agent.main import HealthChecks, QueryValues, log_config

pytestmark = pytest.mark.asyncio


def access(path: str, status: int) -> logging.LogRecord:
    # As uvicorn 0.53 logs a request: client, method, path, HTTP version, status
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", path, "1.1", status),
        None,
    )


async def test_a_passing_health_check_is_left_out() -> None:
    kept = HealthChecks().filter
    assert not kept(access("/readyz", 200))
    assert not kept(access("/healthz", 200))


async def test_a_failing_health_check_and_real_requests_show() -> None:
    kept = HealthChecks().filter
    assert kept(access("/readyz", 503))
    assert kept(access("/v1/me", 200))
    assert kept(access("/readyz?verbose=1", 200))
    # A line not shaped like an access line is never dropped
    assert kept(
        logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 1, "ready", None, None
        )
    )


async def test_uvicorns_own_settings_carry_the_filter() -> None:
    config = log_config()
    assert config["loggers"]["uvicorn.access"]["filters"] == [
        "health_checks",
        "query_values",
    ]
    assert config["filters"]["health_checks"]["()"] is HealthChecks
    assert config["filters"]["query_values"]["()"] is QueryValues
    # uvicorn's module-level default is left as it was
    from uvicorn.config import LOGGING_CONFIG

    assert "filters" not in LOGGING_CONFIG["loggers"]["uvicorn.access"]


@pytest.mark.parametrize(
    ("path", "logged"),
    [
        (
            "/v1/search?q=my+divorce+lawyer&mode=keyword&limit=20",
            "/v1/search?q=…&mode=keyword&limit=20",
        ),
        (
            "/v1/threads/4c2a/files?name=tax-return-2025.pdf",
            "/v1/threads/4c2a/files?name=…",
        ),
        (
            "/v1/admin/users?max=50&search=mary%40example.com",
            "/v1/admin/users?max=50&search=…",
        ),
        (
            "/v1/threads/4c2a/runs/9b1e/stream?after=12",
            "/v1/threads/4c2a/runs/9b1e/stream?after=12",
        ),
        ("/v1/me", "/v1/me"),
    ],
)
async def test_what_a_person_typed_is_masked_in_the_access_line(
    path: str, logged: str
) -> None:
    record = access(path, 200)
    assert QueryValues().filter(record)
    assert record.args[2] == logged  # ty: ignore[not-subscriptable]
    assert "divorce" not in record.getMessage() and "mary" not in record.getMessage()
