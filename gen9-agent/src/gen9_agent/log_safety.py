"""Log lines nothing outside can forge (docs/plans/manual-e2e.md, P6-C3; ASVS 5.0 16.4.1; CWE-117).

The worker logs what other servers say: a connector server's refusal, word for word. A server that
answered with a newline and a line of its own ("2026-10-01 00:00:00,000 WARNING gen9_agent.audit:
…") put that line in the log as if Gen9 had written it, and terminal escape sequences in it
coloured, or could rewrite, the operator's terminal under `make logs` (seen live with such a
server). OWASP's Logging Cheat Sheet: sanitize event data for "carriage return (CR), line feed
(LF) and delimiter characters".

A filter on the handlers writes every control character of a record's message and arguments as
its escape (`\\n`, `\\x1b`): one record, one line, and nothing a terminal acts on. A traceback
(`exc_info`) keeps its lines; it is Gen9's own.

Each line also says when, in UTC with its zone (`2026-10-01T00:46:24.364Z`; ASVS 5.0 16.2.2): the
worker's said no zone, the API's no time at all (P6-C4).
"""

import logging
import re
import time
from collections.abc import Mapping
from typing import Any

from uvicorn.logging import AccessFormatter, DefaultFormatter

# C0 and C1 controls, DEL, and the Unicode line and paragraph separators
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def escape(text: str) -> str:
    return CONTROL.sub(
        lambda m: m.group().encode("unicode_escape").decode("ascii"), text
    )


def _safe(value: Any) -> Any:
    if isinstance(value, (int, float)):  # bool too: %d and %f keep their numbers
        return value
    text = value if isinstance(value, str) else str(value)
    return escape(text) if CONTROL.search(text) else value


class ControlCharacters(logging.Filter):
    """Escapes the control characters of a record's message and arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _safe(record.msg)
        args = record.args
        if isinstance(args, tuple):
            record.args = tuple(_safe(a) for a in args)
        elif isinstance(args, Mapping):
            record.args = {k: _safe(v) for k, v in args.items()}
        return True


def guard(handlers: list[logging.Handler]) -> None:
    """Adds the filter to these handlers (the root logger's, after basicConfig)."""
    for handler in handlers:
        if not any(isinstance(f, ControlCharacters) for f in handler.filters):
            handler.addFilter(ControlCharacters())


class UTC(logging.Formatter):
    """`%(asctime)s` as ISO 8601 in UTC, to the millisecond, with its Z."""

    @staticmethod
    def converter(seconds: float | None = None) -> time.struct_time:
        return time.gmtime(seconds)

    default_time_format = "%Y-%m-%dT%H:%M:%S"
    default_msec_format = "%s.%03dZ"


class UTCDefault(UTC, DefaultFormatter):
    """uvicorn's own lines, with the time first."""


class UTCAccess(UTC, AccessFormatter):
    """uvicorn's access lines, with the time first."""
