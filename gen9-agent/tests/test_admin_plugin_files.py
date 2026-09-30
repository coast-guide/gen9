"""What an admin reads of a plugin's kept files before choosing who may have it (P5-C4)."""

import pytest

from gen9_agent.api.plugins import shown

pytestmark = pytest.mark.asyncio


async def test_a_skills_instructions_read_as_written() -> None:
    assert shown(b"---\nname: greet\n---\nSay hello.\n") == (
        "---\nname: greet\n---\nSay hello.\n"
    )


async def test_a_long_file_is_cut_at_the_limit_never_inside_a_character() -> None:
    # "é" is two bytes: a limit of 4 falls inside the second one
    assert shown("aéé".encode(), limit=4) == "aé"
    assert shown(b"abcdef", limit=4) == "abcd"


async def test_what_isnt_text_isnt_shown_as_text() -> None:
    assert shown(b"\x89PNG\r\n\x1a\n\x00\x00\xff\xfe" * 10) is None
