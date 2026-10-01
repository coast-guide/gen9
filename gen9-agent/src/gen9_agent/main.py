"""`gen9-agent`: run the API server (uvicorn)."""

import copy
import logging
import os
from typing import Any
from urllib.parse import unquote_plus

import uvicorn
from uvicorn.config import LOGGING_CONFIG

from .log_safety import ControlCharacters

HEALTH_PATHS = frozenset({"/healthz", "/readyz"})


class HealthChecks(logging.Filter):
    """Drops the access line of a health check that passed. Compose asks `/readyz` every few
    seconds, so without this `make logs` showed almost nothing else; one that failed still shows.
    uvicorn logs each request as (client, method, path, HTTP version, status)."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5:
            path, status = args[2], args[4]
            return not (
                path in HEALTH_PATHS and isinstance(status, int) and status < 400
            )
        return True


# Query parameters whose values say nothing about a person: counts, cursors, filters by kind
PLAIN_QUERY = frozenset(
    {"limit", "max", "first", "before", "after", "outcome", "mode", "kind", "status"}
    | {"preserveStorageRefs"}
)


class QueryValues(logging.Filter):
    """Masks the query values of an access line but the plain ones: what a person searched
    (`/v1/search?q=`), a file's name (`files?name=`), an email an admin looked up
    (`/v1/admin/users?search=`) never reach the log, while the path, its ids and the names of
    the parameters stay (docs/plans/manual-e2e.md, P6-C2; ASVS 5.0 16.2.5)."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            path, _, query = args[2].partition("?")
            if query:
                masked = "&".join(
                    pair
                    if unquote_plus(pair.partition("=")[0]) in PLAIN_QUERY
                    else f"{pair.partition('=')[0]}=…"
                    for pair in query.split("&")
                )
                record.args = (*args[:2], f"{path}?{masked}", *args[3:])
        return True


def log_config() -> dict[str, Any]:
    """uvicorn's own logging settings: health checks that passed left out of the access log, what
    people typed masked in it, control characters escaped in every line, and Gen9's own warnings
    formatted as uvicorn's."""
    config = copy.deepcopy(LOGGING_CONFIG)
    config["filters"] = {
        "health_checks": {"()": HealthChecks},
        "query_values": {"()": QueryValues},
        "control_characters": {"()": ControlCharacters},
    }
    config["loggers"]["uvicorn.access"]["filters"] = ["health_checks", "query_values"]
    # Every line one line, without terminal escapes (log_safety.py)
    for handler in config["handlers"].values():
        handler["filters"] = ["control_characters"]
    # Gen9's own warnings through the same handler, which Python's last resort printed bare
    config["root"] = {"handlers": ["default"], "level": "WARNING"}
    return config


def main() -> None:
    uvicorn.run(
        "gen9_agent.app:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "17000")),
        proxy_headers=False,
        log_level=os.environ.get("LOG_LEVEL", "info"),
        log_config=log_config(),
    )


if __name__ == "__main__":
    main()
