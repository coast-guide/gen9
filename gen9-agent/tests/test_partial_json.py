"""A tool call's arguments parsed in linear time, with langchain-core's results (partial_json.py):
a model that runs away inside one no longer holds the worker's event loop for minutes."""

import importlib.util
import json
import random
import time
from collections.abc import Callable
from typing import Any

import langchain_core.utils.json
import pytest
from langchain_core.messages import AIMessageChunk, ai

from gen9_agent import partial_json
from gen9_agent.partial_json import install, parse_partial_json

pytestmark = pytest.mark.asyncio

# langchain-core's own function, from its source file: install() replaces the one imported
_spec = importlib.util.spec_from_file_location(
    "langchain_core_json_as_released", langchain_core.utils.json.__file__
)
assert _spec and _spec.loader
_released = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_released)
released = _released.parse_partial_json

DOCUMENTS = [
    {"subagent_type": "fact-checker", "description": "Verify this claim.\nOne line."},
    {"path": "/out/a.py", "content": 'print("hi")\n\tx = "\\\\"\n', "n": -12.5e3},
    {
        "todos": [
            {"content": "Read", "status": "done"},
            {"content": "Write", "status": None},
        ]
    },
    {
        "query": "postgres 18 release",
        "max": 10,
        "exact": True,
        "lang": "fr",
        "x": False,
    },
    [1, [2, [3, {"a": [], "b": {}}]], "é   😀", 0, -0.5, 1e-7],
    {"quote": 'he said "no" \\ then left', "unicode": "é中", "empty": ""},
]
# The shape a real model produced on k3d: a good start, then rambling in a key it never closed
RUNAWAY = (
    '{"subagent_type":"fact-checker","description":"Verify this claim.","}  to=functions.x  ? '
    + "Maybe the call did not register? Need invoke again. Let's call. Hmm.\n" * 40
    + '"\n'
)


def cases() -> list[str]:
    texts = [RUNAWAY]
    for document in DOCUMENTS:
        for text in (json.dumps(document), json.dumps(document, indent=2)):
            texts += [text[:n] for n in range(len(text) + 1)]
    # Cut and spliced at random, for malformed text the prefixes don't reach
    rng = random.Random(40826)
    pool = [json.dumps(d, indent=rng.choice([None, 1])) for d in DOCUMENTS] + [RUNAWAY]
    noise = list(' \t\n\r"\\{}[]:,-.0123456789eEtrufalsn') + [
        '"a"',
        "\\u00",
        "\\n",
        "é",
    ]
    for _ in range(4000):
        text = rng.choice(pool)
        text = text[: rng.randrange(len(text) + 1)]
        for _ in range(rng.randrange(4)):
            at = rng.randrange(len(text) + 1)
            text = text[:at] + rng.choice(noise) + text[at:]
        texts.append(text)
    return texts


def outcome(parse: Callable[..., Any], text: str, strict: bool) -> str:
    """By `repr`: a NaN (`NaN` is JSON to Python) is never equal to itself."""
    try:
        return repr(parse(text, strict=strict))
    except json.JSONDecodeError as e:
        return repr(("raised", e.msg, e.pos))


async def test_it_returns_what_langchain_core_returns() -> None:
    for text in cases():
        for strict in (False, True):
            assert outcome(parse_partial_json, text, strict) == outcome(
                released, text, strict
            ), (
                text,
                strict,
            )


async def test_a_runaway_tool_call_parses_in_linear_time() -> None:
    # 200,000 characters: about the 32,000 tokens the model ran away with. langchain-core 1.6.4's
    # function takes about 90 seconds on it, quadratic (explore/harness/runaway_tool_call_probe.py)
    text = RUNAWAY[: RUNAWAY.index("Maybe")] + RUNAWAY[RUNAWAY.index("Maybe") :] * 70
    assert len(text) > 190_000
    start = time.perf_counter()
    parsed = parse_partial_json(text)
    assert time.perf_counter() - start < 2
    assert parsed == {
        "subagent_type": "fact-checker",
        "description": "Verify this claim.",
    }


async def test_installed_where_langchain_core_parses_a_streamed_tool_call() -> None:
    install()
    for name in partial_json._USERS:
        module = importlib.import_module(name)
        assert module.parse_partial_json is parse_partial_json, name
    chunk = AIMessageChunk(
        content="",
        tool_call_chunks=[
            {
                "name": "task",
                "args": RUNAWAY,
                "id": "call_1",
                "index": 0,
                "type": "tool_call_chunk",
            }
        ],
    )
    assert ai.parse_partial_json is parse_partial_json
    assert chunk.tool_calls[0]["args"] == released(RUNAWAY)
