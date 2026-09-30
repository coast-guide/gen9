"""`gen9-agent`: run the API server (uvicorn)."""

import copy
import logging
import os
from typing import Any

import uvicorn
from uvicorn.config import LOGGING_CONFIG

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


def log_config() -> dict[str, Any]:
    """uvicorn's own logging settings, with health checks that passed left out of the access log."""
    config = copy.deepcopy(LOGGING_CONFIG)
    config["filters"] = {"health_checks": {"()": HealthChecks}}
    config["loggers"]["uvicorn.access"]["filters"] = ["health_checks"]
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
