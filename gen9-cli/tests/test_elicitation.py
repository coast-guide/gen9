"""A connector's server asking, in the terminal (elicitation.py): a form field by field, and an
address opened only on y."""

import os

import pytest

from gen9_cli import elicitation
from gen9_cli.questions import Terminal

pytestmark = pytest.mark.asyncio

FORM = {
    "key": "trip",
    "message": "Where to?",
    "mode": "form",
    "requested_schema": {
        "type": "object",
        "properties": {
            "city": {"type": "string", "title": "City"},
            "nights": {"type": "integer", "default": 2},
            "class": {
                "type": "string",
                "oneOf": [
                    {"const": "eco", "title": "Economy"},
                    {"const": "biz", "title": "Business"},
                ],
            },
            "extras": {"type": "array", "items": {"enum": ["wifi", "meals"]}},
            "window": {"type": "boolean"},
        },
        "required": ["city"],
    },
}
URL = {
    "key": "cal",
    "message": "Connect your calendar",
    "mode": "url",
    "url": "https://calendar.example.com/connect",
}


def piped(text: str) -> Terminal:
    read, write = os.pipe()
    os.write(write, text.encode())
    os.close(write)
    return Terminal(read)


async def test_a_form_is_asked_field_by_field(capsys) -> None:
    request = {
        "kind": "elicitation",
        "tool_name": "travel__plan_trip",
        "requests": [FORM],
    }
    # City needed (an empty line asks again), nights by default, a choice by number, two extras, y
    answers = await elicitation.respond(request, piped("\nLisbon\n\n2\n1,2\ny\n\n"))
    assert answers == {
        "trip": {
            "action": "accept",
            "content": {
                "city": "Lisbon",
                "nights": 2,
                "class": "biz",
                "extras": ["wifi", "meals"],
                "window": True,
            },
        }
    }
    shown = capsys.readouterr().err
    assert (
        "travel asks (while using plan trip):" in shown
        and "This one is needed." in shown
    )


async def test_n_declines_the_form_and_the_address_opens_only_on_y(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(elicitation.webbrowser, "open", opened.append)
    request = {
        "kind": "elicitation",
        "tool_name": "travel__plan_trip",
        "requests": [FORM, URL],
    }
    answers = await elicitation.respond(request, piped("Rome\n\n\n\n\nn\nn\n"))
    assert answers == {"trip": {"action": "decline"}, "cal": {"action": "decline"}}
    assert opened == []
    answers = await elicitation.respond({**request, "requests": [URL]}, piped("y\n\n"))
    assert answers == {"cal": {"action": "accept"}} and opened == [URL["url"]]


async def test_an_address_that_isnt_a_web_page_is_declined_unasked(
    monkeypatch, capsys
) -> None:
    """A server's javascript:, file: or other app's address would reach the system browser, which
    runs or launches it: Gen9 declines it without asking or opening (M9, U1)."""
    opened: list[str] = []
    monkeypatch.setattr(elicitation.webbrowser, "open", opened.append)
    for address in (
        "javascript:alert(document.domain)",
        "file:///etc/passwd",
        "vscode://x",
    ):
        request = {
            "kind": "elicitation",
            "tool_name": "notes__open",
            "requests": [{**URL, "url": address}],
        }
        answers = await elicitation.respond(request, piped("y\n\n"))
        assert answers == {"cal": {"action": "decline"}}
    assert opened == []
    assert "Gen9 opens only web addresses" in capsys.readouterr().err
    assert elicitation.is_web_address("https://calendar.example.com/x")
    assert elicitation.is_web_address(" HTTP://example.com")


async def test_nobody_at_the_terminal_answers_nothing() -> None:
    assert (
        await elicitation.respond({"tool_name": "t__x", "requests": [FORM]}, piped(""))
        is None
    )
