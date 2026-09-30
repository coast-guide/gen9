"""The API's access log (main.py): health checks that passed are left out, so `make logs` shows
real requests; a failing health check and every other request still show."""

import logging

import pytest

from gen9_agent.main import HealthChecks, log_config

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
    assert config["loggers"]["uvicorn.access"]["filters"] == ["health_checks"]
    assert config["filters"]["health_checks"]["()"] is HealthChecks
    # uvicorn's module-level default is left as it was
    from uvicorn.config import LOGGING_CONFIG

    assert "filters" not in LOGGING_CONFIG["loggers"]["uvicorn.access"]
