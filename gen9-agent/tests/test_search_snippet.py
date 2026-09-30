"""A hit's snippet shows where the query's words are, or else the start of the answer."""

import pytest

from gen9_agent.api.search import snippet

pytestmark = pytest.mark.asyncio

BODY = (
    "In one sentence: how do I tune Postgres autovacuum for a table with heavy updates?\n\n"
    "Lower autovacuum_vacuum_scale_factor for that table so vacuum runs earlier, and raise its "
    "cost limit so each run finishes; then watch dead tuples in pg_stat_user_tables to confirm "
    "bloat stays down over a few days of the heavy update load."
)


async def test_the_text_around_the_first_matching_word() -> None:
    out = snippet(BODY, "dead tuples", limit=80)
    assert out.startswith("…") and "dead tuples" in out and out.endswith("…")
    assert not out[1:].startswith(" ")  # begins at a word


async def test_the_answer_when_no_word_matches() -> None:
    # Search by meaning: the question is the chat's title already, so show the answer
    assert snippet(BODY, "my database keeps growing", limit=40).startswith(
        "Lower autovacuum"
    )


async def test_short_words_and_a_body_without_an_answer() -> None:
    assert snippet(BODY, "a to of", limit=30).startswith("Lower")  # too short to match
    assert snippet("Only a question here?", "zebra") == "Only a question here?"
    assert snippet(None, "x") is None


async def test_plain_text_and_answers_first() -> None:
    body = "Which plant for a dark room?\n\nA **snake plant** is a good [first plant](https://x.test)."
    # "plant" is in the question too, but the answer's match is what's worth showing
    assert snippet(body, "plant") == "A snake plant is a good first plant."
    assert snippet("# Title\n\nUse `pg_stat` __now__", "zebra") == "Use pg_stat now"


async def test_scripts_without_spaces_are_matched_as_text() -> None:
    """Postgres's parser takes a run of them as one word (P4-C3): which queries match as text."""
    from gen9_agent.api.search import UNSPACED

    for query in ("海边", "寿司", "カレー", "ชายหาด", "ສະບາຍດີ", "សួស្តី", "မင်္ဂလာ", "𠀀"):
        assert UNSPACED.search(query), query
    # Words spaced apart: BM25 and trigrams do these
    for query in ("sea", "البحر", "שלום", "바다", "море"):
        assert not UNSPACED.search(query), query


async def test_a_short_chinese_word_centres_its_snippet() -> None:
    body = (
        "海边的问题\n\n" + "天气很好。" * 30 + "我喜欢在海边散步。" + "天气很好。" * 30
    )
    shown = snippet(body, "散步") or ""
    assert "散步" in shown
