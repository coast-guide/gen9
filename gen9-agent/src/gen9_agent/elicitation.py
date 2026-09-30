"""A connector's server asking the person something mid-call (MCP 2026-07-28, "Elicitation").

The server returns an `InputRequiredResult`; `langchain.mcp` turns it into a LangGraph interrupt
(`mcp_elicitation`: the tool, and its requests), and resuming with `{"responses": {key: answer}}`
calls the tool again with them (explore/connectors/NOTES.md). The run waits like a question
(runs/store.py), and the person answers in the web app or the terminal:
- a **form**: a flat object of primitive fields, which the answer is checked against here, as the
  spec asks clients to validate; the server must not ask for secrets this way;
- a **URL**: the client shows it in full and opens it only with the person's consent; the answer
  carries no content.

Each request is answered `accept` (a form's content), `decline` or `cancel`.
"""

import re
from datetime import date, datetime
from typing import Any

KIND = "elicitation"
# langchain.mcp.elicitation.ELICITATION_INTERRUPT_TYPE (a test holds them equal), not imported here:
# that package is marked beta
INTERRUPT_TYPE = "mcp_elicitation"
ACTIONS = ("accept", "decline", "cancel")
MAX_TEXT = 4000
MAX_FIELDS = 20
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URI = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:\S+$")


def connector_of(tool_name: str) -> str:
    """The connector a tool belongs to (`<connector>__<tool>`), for "<connector> asks"."""
    return tool_name.split("__", 1)[0]


def described(
    value: dict[str, Any], tool_calls: list[dict[str, Any]]
) -> dict[str, Any]:
    """An elicitation as the person is asked it:
    - `connector`: langchain.mcp names the server's own tool (`plan_trip`), so the connector
      comes from the call waiting on it (`travel__plan_trip`);
    - `order` on each form: its fields in the server's order, which the stored request loses
      (Postgres' JSONB keeps an object's keys in its own order)."""
    tool = value.get("tool_name") or ""
    waiting = next(
        (
            c["name"]
            for c in reversed(tool_calls)
            if c.get("name", "").endswith(f"__{tool}")
        ),
        tool,
    )
    return {
        **value,
        "connector": connector_of(waiting),
        "requests": [
            {
                **r,
                "order": list(
                    (r.get("requested_schema") or {}).get("properties") or {}
                ),
            }
            if r.get("mode", "form") == "form"
            else r
            for r in value.get("requests") or []
        ],
    }


def _choices(schema: dict[str, Any]) -> list[Any] | None:
    if "enum" in schema:
        return list(schema["enum"])
    options = schema.get("oneOf") or schema.get("anyOf")
    if options:
        return [o.get("const") for o in options if isinstance(o, dict)]
    return None


def _field(name: str, schema: dict[str, Any], value: Any) -> Any:
    """`value` for one field of a form, or ValueError saying why not."""
    kind = schema.get("type")
    if kind == "string":
        if not isinstance(value, str) or len(value) > MAX_TEXT:
            raise ValueError(f"{name}: text expected")
        choices = _choices(schema)
        if choices is not None and value not in choices:
            raise ValueError(f"{name}: one of the choices expected")
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get(
            "maxLength", MAX_TEXT
        ):
            raise ValueError(f"{name}: wrong length")
        fmt = schema.get("format")
        if fmt == "email" and not _EMAIL.match(value):
            raise ValueError(f"{name}: an email address expected")
        if fmt == "uri" and not _URI.match(value):
            raise ValueError(f"{name}: an address expected")
        if fmt == "date":
            date.fromisoformat(value)
        if fmt == "date-time":
            datetime.fromisoformat(value)
        return value
    if kind in ("number", "integer"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name}: a number expected")
        if kind == "integer" and value != int(value):
            raise ValueError(f"{name}: a whole number expected")
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"{name}: at least {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"{name}: at most {schema['maximum']}")
        return int(value) if kind == "integer" else value
    if kind == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{name}: yes or no expected")
        return value
    if kind == "array":
        items = schema.get("items") or {}
        choices = _choices(items) or []
        if not isinstance(value, list) or any(v not in choices for v in value):
            raise ValueError(f"{name}: some of the choices expected")
        if len(set(value)) != len(value):
            raise ValueError(f"{name}: each choice once")
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get(
            "maxItems", len(choices)
        ):
            raise ValueError(f"{name}: wrong number of choices")
        return value
    raise ValueError(f"{name}: a kind of field Gen9 can't ask for")


def check_form(schema: dict[str, Any], content: Any) -> dict[str, Any]:
    """A form's content, checked against its flat schema: the required fields present, each field
    of its kind, nothing else."""
    properties: dict[str, Any] = schema.get("properties") or {}
    if not isinstance(content, dict) or len(properties) > MAX_FIELDS:
        raise ValueError("the form's answers expected")
    unknown = set(content) - set(properties)
    if unknown:
        raise ValueError(f"not in the form: {', '.join(sorted(unknown))}")
    missing = [n for n in schema.get("required") or [] if content.get(n) in (None, "")]
    if missing:
        raise ValueError(f"required: {', '.join(missing)}")
    return {
        name: _field(name, properties[name], value)
        for name, value in content.items()
        if value is not None
    }


def check_responses(
    request: dict[str, Any], responses: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """The person's answers to one round of a server's requests, checked: one per request, a
    known action, and a form's content valid for its schema. ValueError otherwise."""
    asked = {r["key"]: r for r in request.get("requests") or []}
    if set(responses) != set(asked):
        raise ValueError("answer every request, and only those")
    checked: dict[str, dict[str, Any]] = {}
    for key, answer in responses.items():
        action = answer.get("action")
        if action not in ACTIONS:
            raise ValueError(f"{key}: accept, decline or cancel")
        if action != "accept":
            checked[key] = {"action": action}
        elif asked[key].get("mode") == "url":
            # The person agreed to open it; what happens there never passes through Gen9
            checked[key] = {"action": "accept"}
        else:
            content = check_form(
                asked[key].get("requested_schema") or {}, answer.get("content")
            )
            checked[key] = {"action": "accept", "content": content}
    return checked
