"""Gen9's questions in the terminal (questions.py): how they're shown, what a typed line answers,
and reading lines from a real pipe without a thread."""

import os

import pytest

from gen9_cli.questions import Terminal, ask, parse, show

pytestmark = pytest.mark.asyncio

CITY = {
    "question": "Which city?",
    "type": "multiple_choice",
    "choices": ["Paris", "Rome"],
    "required": True,
}
NOTE = {"question": "Anything else?", "type": "text", "choices": [], "required": False}


async def test_a_choice_question_is_a_numbered_list_ending_with_other() -> None:
    assert (
        show(CITY, None)
        == "Which city?\n  1. Paris\n  2. Rome\n  3. Other (type your answer)"
    )
    assert show(NOTE, 2) == "2. Anything else? (optional)"


async def test_a_number_picks_a_choice_and_other_text_is_the_answer() -> None:
    assert parse(CITY, " 2 ") == "Rome"
    assert parse(CITY, "3") is None  # Other: the answer comes next
    assert parse(CITY, "Lisbon") == "Lisbon"
    assert parse(CITY, "9") == "9"
    assert parse(NOTE, "1") == "1"


def piped(text: str) -> Terminal:
    read, write = os.pipe()
    os.write(write, text.encode())
    os.close(write)
    return Terminal(read)


async def test_lines_arriving_together_are_read_one_by_one_then_the_end() -> None:
    terminal = piped("first\r\nsecond\nlast")
    assert [await terminal.line() for _ in range(4)] == [
        "first",
        "second",
        "last",
        None,
    ]


async def test_asks_again_until_a_required_question_has_an_answer(capsys) -> None:
    answers = await ask({"questions": [CITY, NOTE]}, piped("\n3\nLisbon\n\n"))
    assert answers == ["Lisbon", ""]
    assert "This one needs an answer." in capsys.readouterr().err


async def test_no_answers_when_nobody_is_at_the_terminal() -> None:
    assert await ask({"questions": [CITY]}, piped("")) is None


async def test_retry_is_the_default_and_n_gives_up(capsys) -> None:
    from gen9_cli.questions import retry_or_stop

    failed = {"kind": "retry", "error": "The model provider didn't answer."}
    assert await retry_or_stop(failed, piped("\n")) is True
    assert await retry_or_stop(failed, piped("n\n")) is False
    assert await retry_or_stop(failed, piped("")) is None
    assert (
        "Gen9 couldn't finish: The model provider didn't answer."
        in capsys.readouterr().err
    )


async def test_answers_from_a_file_or_dev_null(tmp_path) -> None:
    """`gen9 ask … < answers.txt`, or a script's empty stdin: the event loop can't watch a file or
    /dev/null (kqueue refused it with EINVAL, P2-H1), so they're read directly."""
    answers = tmp_path / "answers.txt"
    answers.write_text("n\n")
    from gen9_cli.questions import retry_or_stop

    failed = {"kind": "retry", "error": "Retry in a minute."}
    fd = os.open(answers, os.O_RDONLY)
    try:
        assert await retry_or_stop(failed, Terminal(fd)) is False
    finally:
        os.close(fd)
    fd = os.open(os.devnull, os.O_RDONLY)
    try:
        assert await retry_or_stop(failed, Terminal(fd)) is None
    finally:
        os.close(fd)
