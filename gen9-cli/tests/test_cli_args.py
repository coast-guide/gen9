"""The command line (main.parse): words need no quotes, --limit is checked before any call, and a
refusal from Gen9 is reported in its own words (main.refusal), not as a failure to reach it."""

import httpx
import pytest

from gen9_cli.main import parse, refusal

pytestmark = pytest.mark.asyncio


async def test_several_words_are_one_message_or_query() -> None:
    assert parse(["search", "Porto", "weekend"]).query == "Porto weekend"
    assert parse(["search", "Porto weekend"]).query == "Porto weekend"
    assert parse(["ask", "What", "changed?"]).message == "What changed?"
    assert parse(["ask", "--thread", "t1", "Go", "on"]).thread == "t1"
    # A task's own two positionals are left alone
    added = parse(["tasks", "add", "Brief", "Write a brief", "--every", "day"])
    assert (added.name, added.message) == ("Brief", "Write a brief")


async def test_limit_is_checked_first(capsys: pytest.CaptureFixture[str]) -> None:
    assert parse(["search", "x", "--limit", "50"]).limit == 50
    for bad in ("0", "99", "many"):
        with pytest.raises(SystemExit):
            parse(["search", "x", "--limit", bad])
        assert "give a whole number from 1 to 50" in capsys.readouterr().err


async def test_a_refusal_says_why() -> None:
    plain = httpx.Response(
        409, json={"detail": "This chat is answering: wait or stop it."}
    )
    assert refusal(plain) == "This chat is answering: wait or stop it."
    invalid = httpx.Response(
        422,
        json={
            "detail": [
                {
                    "loc": ["query", "limit"],
                    "msg": "Input should be less than or equal to 50",
                }
            ]
        },
    )
    assert refusal(invalid) == "limit: Input should be less than or equal to 50"
    assert (
        refusal(httpx.Response(502, text="<html>bad gateway</html>")) == "Bad Gateway"
    )


async def test_more_attachments_than_a_message_takes_are_refused_before_any_call(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """gen9-agent takes at most 10 files a message; the CLI says so before uploading any, or
    making a chat (P2-H2: it uploaded twelve, then crashed reading the refusal)."""
    from gen9_cli.main import cmd_ask

    args = parse(["ask", *[f"--attach=f{i}.txt" for i in range(11)], "Hi"])
    assert await cmd_ask(None, None, args) == 1  # ty: ignore[invalid-argument-type]
    assert "at most 10 files" in capsys.readouterr().err


async def test_an_empty_thread_is_refused_not_a_new_chat(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--thread "$T"` with T unset started a new chat each time (P2-H4: a script made twelve)."""
    from gen9_cli.main import cmd_ask

    assert await cmd_ask(None, None, parse(["ask", "--thread", "", "Hi"])) == 1  # ty: ignore[invalid-argument-type]
    assert "--thread needs a chat's id" in capsys.readouterr().err
