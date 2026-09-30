"""A connector's server asking the person mid-call (gen9-agent's elicitation.py, kind
`elicitation`), answered in the terminal. It says which connector asks. A form is asked field by
field (its choices numbered, a default in brackets); an address is shown in full and opened in
the browser only when the person says y. Gen9 checks the answers again, and asks again on a
mistake."""

import asyncio
import sys
import webbrowser
from typing import Any
from urllib.parse import urlsplit

from .questions import Terminal


def say(text: str, end: str = "\n") -> None:
    print(text, end=end, file=sys.stderr, flush=True)


def _choices(schema: dict) -> list[tuple[str, str]]:
    if "enum" in schema:
        return [(v, v) for v in schema["enum"]]
    return [
        (o["const"], o.get("title") or o["const"])
        for o in schema.get("oneOf") or schema.get("anyOf") or []
    ]


async def _field(
    name: str, schema: dict, required: bool, terminal: Terminal
) -> tuple[bool, Any]:
    """(False, None) when the input ended; else (True, value), None for an optional one left empty."""
    label = schema.get("title") or name
    kind = schema.get("type")
    choices = (
        _choices(schema.get("items") or {}) if kind == "array" else _choices(schema)
    )
    if choices:
        for i, (_, text) in enumerate(choices, 1):
            say(f"  {i}. {text}")
    default = schema.get("default")
    hint = (
        " (numbers, comma-separated)"
        if kind == "array"
        else " [y/n]"
        if kind == "boolean"
        else ""
    )
    while True:
        shown = f" [{default}]" if default not in (None, "", []) else ""
        say(f"{label}{'*' if required else ''}{hint}{shown}: ", end="")
        line = await terminal.line()
        if line is None:
            return False, None
        line = line.strip()
        if not line:
            if default is not None:
                return True, default
            if not required:
                return True, None
            say("  This one is needed.")
            continue
        try:
            if kind == "boolean":
                return True, line.lower() in ("y", "yes", "true")
            if kind == "integer":
                return True, int(line)
            if kind == "number":
                return True, float(line)
            if kind == "array":
                return True, [
                    choices[int(n) - 1][0]
                    for n in line.replace(" ", "").split(",")
                    if n
                ]
            if choices:
                return True, choices[int(line) - 1][0] if line.isdigit() else line
            return True, line
        except (ValueError, IndexError):
            say("  That doesn't fit. Try again.")


def is_web_address(url: str) -> bool:
    """Whether a server's address is a web page (http or https), the only kind Gen9 opens."""
    return urlsplit(url.strip()).scheme.lower() in ("http", "https")


async def respond(request: dict, terminal: Terminal) -> dict[str, dict] | None:
    """One answer per request of this round, or None if the input ended first."""
    tool = request.get("tool_name", "")
    connector = request.get("connector") or tool.partition("__")[0]
    say(
        f"\n{connector} asks (while using {tool.rpartition('__')[2].replace('_', ' ')}):"
    )
    answers: dict[str, dict] = {}
    for asked in request.get("requests") or []:
        say(asked.get("message", ""))
        if asked.get("mode") == "url":
            say(f"  {asked['url']}")
            if not is_web_address(asked["url"]):
                # A server's address goes to the system browser, which would also launch a local
                # file or another app's scheme: only web pages open (M9, U1)
                say(
                    "Gen9 opens only web addresses (http or https), so it declines this one."
                )
                answers[asked["key"]] = {"action": "decline"}
                continue
            say("Open it in your browser? [y/N] ", end="")
            line = await terminal.line()
            if line is None:
                return None
            if line.strip().lower() in ("y", "yes"):
                # The system browser, not Gen9, opens it (webbrowser blocks, hence a thread)
                await asyncio.to_thread(webbrowser.open, asked["url"])
                say("Press Enter once you've finished there. ", end="")
                if await terminal.line() is None:
                    return None
                answers[asked["key"]] = {"action": "accept"}
            else:
                answers[asked["key"]] = {"action": "decline"}
            continue
        schema = asked.get("requested_schema") or {}
        required = set(schema.get("required") or [])
        content: dict[str, Any] = {}
        properties = schema.get("properties") or {}
        # The server's order: gen9-agent keeps it, since the stored request's JSON doesn't
        order = [n for n in asked.get("order") or [] if n in properties]
        order += [n for n in properties if n not in order]
        for field in order:
            ok, value = await _field(
                field, properties[field], field in required, terminal
            )
            if not ok:
                return None
            if value is not None:
                content[field] = value
        say("Send it? [Y/n] (n declines) ", end="")
        line = await terminal.line()
        if line is None:
            return None
        declined = line.strip().lower() in ("n", "no")
        answers[asked["key"]] = (
            {"action": "decline"}
            if declined
            else {"action": "accept", "content": content}
        )
    return answers
