"""`gen9 search` prints each chat found with how to continue it."""

import pytest

from gen9_cli.main import format_hits

pytestmark = pytest.mark.asyncio


async def test_hits_show_title_snippet_and_how_to_continue() -> None:
    out = format_hits(
        [
            {
                "thread_id": "t1",
                "title": "Sourdough starter",
                "snippet": "Feed it 1:5:5",
            },
            {"thread_id": "t2", "title": "Tune autovacuum", "snippet": None},
        ]
    )
    assert out.splitlines() == [
        "Sourdough starter",
        "  Feed it 1:5:5",
        '  gen9 ask --thread t1 "…"',
        "Tune autovacuum",
        '  gen9 ask --thread t2 "…"',
    ]


async def test_nothing_found_says_so() -> None:
    assert format_hits([]) == "No chats found."
