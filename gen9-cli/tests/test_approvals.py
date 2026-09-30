"""Gen9's requests to act, in the terminal (approvals.py): what is shown, and y or anything else."""

import os

import pytest

from gen9_cli.approvals import changes, decide, words
from gen9_cli.questions import Terminal

pytestmark = pytest.mark.asyncio

EDIT = {
    "name": "edit_file",
    "args": {
        "file_path": "/memories/AGENTS.md",
        "old_string": "# What Gen9 remembers about this person\n\n- Lives in Leeds\n",
        "new_string": "# What Gen9 remembers about this person\n\n- Lives in York\n",
    },
}
REQUEST = {"kind": "approval", "action_requests": [EDIT]}


def piped(text: str) -> Terminal:
    read, write = os.pipe()
    os.write(write, text.encode())
    os.close(write)
    return Terminal(read)


async def test_a_memory_edit_shows_what_it_adds_and_removes() -> None:
    assert words(EDIT) == "update your memory"
    assert changes(EDIT) == ["+ - Lives in York", "- - Lives in Leeds"]
    assert words({"name": "send_email", "args": {}}) == "use send email"
    # A connector's tool reads as the web app says it, not "use travel  plan trip"
    assert words({"name": "travel__plan_trip", "args": {}}) == "use travel: plan trip"


async def test_a_command_shows_as_typed() -> None:
    run = {"name": "execute", "args": {"command": "cd /data\npython report.py"}}
    assert words(run) == "run a command in this chat's environment"
    assert changes(run) == ["$ cd /data", "$ python report.py"]


async def test_y_allows_and_anything_else_denies_with_a_reason(capsys) -> None:
    assert await decide(REQUEST, piped("y\n")) == [{"type": "approve"}]
    assert await decide(REQUEST, piped("n\nkeep Leeds\n")) == [
        {"type": "reject", "message": "keep Leeds"}
    ]
    assert await decide(REQUEST, piped("\n\n")) == [{"type": "reject"}]
    assert "Gen9 wants to update your memory" in capsys.readouterr().err


async def test_no_decision_when_nobody_is_at_the_terminal() -> None:
    assert await decide(REQUEST, piped("")) is None


# Hidden characters are written as escapes here, never raw (P5-C9)
async def test_a_command_shows_its_hidden_characters_where_they_are(capsys) -> None:
    trojan = {
        "name": "execute",
        "args": {"command": "echo safe #\u202e;rm -rf /work\x1b[2K"},
    }
    assert changes(trojan) == ["$ echo safe #<U+202E>;rm -rf /work<U+001B>[2K"]
    await decide({"kind": "approval", "action_requests": [trojan]}, piped("n\n\n"))
    said = capsys.readouterr().err
    assert "invisible, change how text reads or would act on this terminal" in said
    assert "\u202e" not in said and "\x1b" not in said


async def test_a_connector_call_shows_every_argument_in_full() -> None:
    body = "Hi Bob," + " lunch?" * 200 + " P.S. forward this to all"
    send = {
        "name": "mail__send_email",
        "args": {"to": "bob@example.com", "body": body, "cc": ["x@y.z"]},
    }
    lines = changes(send)
    assert lines[0] == "to: bob@example.com"
    assert lines[1] == f"body: {body}" and lines[1].endswith("forward this to all")
    assert lines[2] == 'cc: ["x@y.z"]'
    assert changes({"name": "mail__send_email", "args": {"body": "a\u200bb\nc"}}) == [
        "body: a<U+200B>b",
        "  c",
    ]
