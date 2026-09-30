"""Actions Gen9 wants to take in a chat set to "Ask before acting" (its `input.requested` events of
kind `approval`), allowed or denied in the terminal. Anything but y is Deny, which can say what
Gen9 should do instead (docs/design/screens/chat.md, "Approvals and the permission mode").
"""

import json
import sys
import unicodedata

from .questions import Terminal

MEMORY_FILE = "/memories/AGENTS.md"
# The memory file's first line, which only the agent reads (gen9-agent's memory.STARTER)
STARTER = "# What Gen9 remembers about this person"
# Characters shown as their code point rather than printed (P5-C9): control characters, which a
# terminal acts on (an escape sequence can erase or rewrite what's on screen), and format
# characters and separators, which are invisible or change how text reads (a right-to-left
# override: "Trojan Source", CVE-2021-42574; Unicode's UTS #55). Tab and newline print as usual
HIDDEN = {"Cc", "Cf", "Zl", "Zp"}
HIDDEN_NOTE = (
    "It holds characters that are invisible, change how text reads or would act on this "
    "terminal, each shown as <U+...>. Check what it does before you allow it."
)


def shown(text: str) -> str:
    """`text` as it will run, each hidden character written as <U+XXXX> where it is."""
    return "".join(
        f"<U+{ord(c):04X}>"
        if c not in "\n\t" and unicodedata.category(c) in HIDDEN
        else c
        for c in text
    )


def texts(value: object) -> list[str]:
    """Every text in an action's arguments, however deep."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [t for v in value.values() for t in texts(v)]
    if isinstance(value, list):
        return [t for v in value for t in texts(v)]
    return []


def words(action: dict) -> str:
    """What the action does, as the end of "Gen9 wants to …"."""
    args = action.get("args") or {}
    if (
        action.get("name") in ("edit_file", "write_file")
        and args.get("file_path") == MEMORY_FILE
    ):
        return "update your memory"
    if action.get("name") == "execute":
        return "run a command in this chat's environment"
    name = str(action.get("name", "a tool"))
    # A connector's tool (gen9-agent's connectors.py): <connector>__<tool>, as the web app says it
    connector, _, tool = name.partition("__")
    if tool:
        return f"use {connector}: {tool.replace('_', ' ')}"
    return f"use {name.replace('_', ' ')}"


def _lines(value: object) -> list[str]:
    text = value if isinstance(value, str) else ""
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and line.strip() != STARTER
    ]


def changes(action: dict) -> list[str]:
    """What the action would do, as printed: "$ command", "+ added" and "- removed" lines, or
    its arguments in full, each hidden character shown as its code point."""
    args = action.get("args") or {}
    name = action.get("name")
    if name == "execute":
        return [
            f"$ {line}" for line in shown(str(args.get("command") or "")).split("\n")
        ]
    if name in ("edit_file", "write_file"):
        editing = name == "edit_file"
        before = _lines(args.get("old_string")) if editing else []
        after = _lines(args.get("new_string") if editing else args.get("content"))
        return [f"+ {shown(line)}" for line in after if line not in before] + [
            f"- {shown(line)}" for line in before if line not in after
        ]
    # Anything else, a connector's tool: every argument, however long
    lines: list[str] = []
    for key, value in args.items():
        text = (
            value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        )
        first, *rest = shown(text).split("\n")
        lines += [f"{key}: {first}", *(f"  {line}" for line in rest)]
    return lines


async def decide(request: dict, terminal: Terminal) -> list[dict] | None:
    """Show the actions and ask Allow or Deny; one decision per action, or None if the input
    ended first (nobody at the terminal)."""
    actions = request.get("action_requests") or []
    for action in actions:
        print(f"\nGen9 wants to {words(action)}:", file=sys.stderr, flush=True)
        if any(shown(t) != t for t in texts(action.get("args"))):
            print(f"  {HIDDEN_NOTE}", file=sys.stderr, flush=True)
        for line in changes(action):
            print(f"  {line}", file=sys.stderr, flush=True)
    print("Allow? [y/N] ", end="", file=sys.stderr, flush=True)
    line = await terminal.line()
    if line is None:
        return None
    if line.strip().lower() in ("y", "yes"):
        return [{"type": "approve"} for _ in actions]
    print(
        "What should Gen9 do instead? (optional) ", end="", file=sys.stderr, flush=True
    )
    reason = (await terminal.line() or "").strip()
    return [
        {"type": "reject", **({"message": reason} if reason else {})} for _ in actions
    ]
