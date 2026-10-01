"""Log lines nothing outside can forge (log_safety.py; manual-e2e.md, P6-C3): what a connector
server says is written into one line, its newlines and terminal escapes as escapes, while Gen9's
own tracebacks keep their lines."""

import io
import logging

import pytest

from gen9_agent.log_safety import ControlCharacters, escape, guard
from gen9_agent.main import log_config

pytestmark = pytest.mark.asyncio

FORGED = "boom\n2026-10-01 00:00:00,000 WARNING gen9_agent.audit: FORGED\x1b[31m RED\r"


def record(
    msg: object, args: object = (), exc_info: object = None
) -> logging.LogRecord:
    return logging.LogRecord(
        "gen9_agent.connectors",
        logging.INFO,
        __file__,
        1,
        msg,
        args,
        exc_info,  # ty: ignore[invalid-argument-type]
    )


async def test_control_characters_are_written_as_their_escapes() -> None:
    assert (
        escape("a\nb\r\tc\x1b[31m\x7f\x85\u2028")
        == "a\\nb\\r\\tc\\x1b[31m\\x7f\\x85\\u2028"
    )
    assert escape("plain, ünïcode and % signs") == "plain, ünïcode and % signs"


async def test_a_servers_forged_line_stays_on_the_line_it_came_in() -> None:
    r = record("connector %s: not listed: %s", ("http://x/mcp", f"MCPError: {FORGED}"))
    assert ControlCharacters().filter(r)
    line = r.getMessage()
    assert "\n" not in line and "\r" not in line and "\x1b" not in line
    assert line.endswith(
        "not listed: MCPError: boom\\n2026-10-01 00:00:00,000 WARNING gen9_agent.audit: FORGED\\x1b[31m RED\\r"
    )


async def test_numbers_keep_their_formats_and_mappings_are_escaped() -> None:
    r = record("run %s: attempt %d took %.1f s", ("a\nb", 3, 1.25))
    ControlCharacters().filter(r)
    assert r.getMessage() == "run a\\nb: attempt 3 took 1.2 s"
    r = record("%(who)s did %(what)s", ({"who": "ada", "what": "x\ny"},))
    ControlCharacters().filter(r)
    assert r.getMessage() == "ada did x\\ny"


async def test_an_exception_as_the_message_or_an_argument_is_escaped() -> None:
    error = ValueError("first\nsecond")
    r = record(error)
    ControlCharacters().filter(r)
    assert r.getMessage() == "first\\nsecond"
    r = record("failed: %s", (error,))
    ControlCharacters().filter(r)
    assert r.getMessage() == "failed: first\\nsecond"


async def test_a_traceback_keeps_its_lines() -> None:
    try:
        raise RuntimeError("Gen9's own")
    except RuntimeError:
        import sys

        r = record("crashed: %s", ("x\ny",), sys.exc_info())
    ControlCharacters().filter(r)
    out = logging.Formatter("%(message)s").format(r)
    first, *rest = out.split("\n")
    assert first == "crashed: x\\ny" and rest[0] == "Traceback (most recent call last):"


async def test_the_worker_and_the_api_carry_the_filter() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    guard([handler])
    guard([handler])  # once only
    assert sum(isinstance(f, ControlCharacters) for f in handler.filters) == 1
    logger = logging.getLogger("gen9_agent.test_log_safety")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        logger.warning("said: %s", FORGED)
    finally:
        logger.removeHandler(handler)
    assert stream.getvalue().count("\n") == 1 and "\x1b" not in stream.getvalue()

    config = log_config()
    assert config["filters"]["control_characters"]["()"] is ControlCharacters
    assert all(
        h["filters"] == ["control_characters"] for h in config["handlers"].values()
    )
    assert config["root"] == {"handlers": ["default"], "level": "WARNING"}
