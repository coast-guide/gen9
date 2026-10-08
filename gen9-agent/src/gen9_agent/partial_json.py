"""langchain-core's `parse_partial_json`, in linear time (docs/plans/deploy.md, Z1).

When a streamed answer ends, langchain-core parses each tool call's arguments with
`parse_partial_json` (AIMessageChunk's `init_tool_calls`), on the event loop. Arguments that don't
parse are repaired: it closes what is open, then retries `json.loads` once for each character it
drops from the end, each retry reading the whole text again. A model that ran away inside a `task`
call (32,000 tokens of rambling in a JSON key it never closed, on k3d) held gen9-agent's worker in
it for minutes: no other run moved, the worker's Temporal token expired unrenewed, and Temporal
stopped the worker (explore/harness/NOTES.md, "A runaway tool call stalls the worker"). Upstream:
langchain-ai/langchain#40826, open; langchain-core 1.6.6 has the same function.

This is langchain-core 1.6.4's function (MIT) with one change: where it drops a character and
retries, it drops straight to the first one that can matter, so a call parses a few times at most.
The results are the same, since what it skips can't parse:

- `json.loads` failed inside the text itself, at `pos`: every longer candidate has the same text up
  to `pos` and fails there too (an unterminated string is reported where it starts, and no
  shorter cut of it closes it), so it cuts back to `pos`;
- it failed at the closing brackets: it drops a character, as langchain-core, and the whitespace
  then before the closing with it: outside a string whitespace reads as none, and inside one the
  string stays open, which no closing bracket closes.

`install` puts it where langchain-core looks it up. Remove both once a release fixes #40826.
"""

import json
from typing import Any

# JSON's whitespace (RFC 8259, section 2)
_WHITESPACE = frozenset(" \t\n\r")


def parse_partial_json(s: str, *, strict: bool = False) -> Any:
    """Parse a JSON string that may be cut short, as langchain-core does: None when a closing
    bracket matches nothing, the text's own JSONDecodeError when no cut of it parses."""
    try:
        return json.loads(s, strict=strict)
    except json.JSONDecodeError:
        pass

    # As langchain-core: close an open string, escape the newlines in strings, and note the
    # brackets still open (a closing one that doesn't match makes the text malformed)
    new_chars: list[str] = []
    stack: list[str] = []
    is_inside_string = False
    escaped = False
    for char in s:
        new_char = char
        if is_inside_string:
            if char == '"' and not escaped:
                is_inside_string = False
            elif char == "\n" and not escaped:
                new_char = "\\n"
            elif char == "\\":
                escaped = not escaped
            else:
                escaped = False
        elif char == '"':
            is_inside_string = True
            escaped = False
        elif char == "{":
            stack.append("}")
        elif char == "[":
            stack.append("]")
        elif char in {"}", "]"}:
            if stack and stack[-1] == char:
                stack.pop()
            else:
                return None
        new_chars.append(new_char)
    if is_inside_string:
        if escaped:
            new_chars.pop()
        new_chars.append('"')
    closing = "".join(reversed(stack))

    # The text's length: an escaped newline is two characters
    length = sum(len(c) for c in new_chars)
    while new_chars:
        try:
            return json.loads("".join(new_chars) + closing, strict=strict)
        except json.JSONDecodeError as e:
            if e.pos < length:
                while new_chars and length > e.pos:
                    length -= len(new_chars.pop())
            else:
                length -= len(new_chars.pop())
                while new_chars and new_chars[-1] in _WHITESPACE:
                    length -= len(new_chars.pop())
    # As langchain-core: nothing parsed, so the original text's error
    return json.loads(s, strict=strict)


# Each module that imported langchain-core's function by name
_USERS = (
    "langchain_core.utils.json",
    "langchain_core.messages.ai",
    "langchain_core.output_parsers.json",
    "langchain_core.output_parsers.openai_tools",
    "langchain_core.output_parsers.openai_functions",
)


def install() -> None:
    """Use this function wherever langchain-core uses its own."""
    import importlib

    for name in _USERS:
        module = importlib.import_module(name)
        if not hasattr(module, "parse_partial_json"):
            raise RuntimeError(f"{name} no longer has parse_partial_json: check #40826")
        module.parse_partial_json = parse_partial_json  # ty: ignore[unresolved-attribute]
