"""Questions Gen9 asks mid-task (its `input.requested` event), asked in the terminal.

A question with choices is a numbered list whose last entry is "Other"; a number picks a choice,
and any other text is the person's own answer. An empty answer to a required question asks again.
Reading the terminal goes through the event loop (`add_reader`), not a thread, so Ctrl-C stops at
once while Gen9 waits for an answer (docs/design/screens/chat.md, "Questions").
"""

import asyncio
import os
import sys


class Terminal:
    """Lines typed in the terminal (or piped in), read without blocking the event loop."""

    def __init__(self, fd: int | None = None) -> None:
        self.fd = sys.stdin.fileno() if fd is None else fd
        self.buffer = b""

    async def _chunk(self) -> bytes:
        loop = asyncio.get_running_loop()
        ready: asyncio.Future[bytes] = loop.create_future()

        def read() -> None:
            if not ready.done():
                ready.set_result(os.read(self.fd, 4096))

        try:
            loop.add_reader(self.fd, read)
        except (OSError, ValueError):
            # A file or /dev/null (`< answers.txt`, a script's empty stdin): the event loop can't
            # watch it (kqueue: EINVAL, epoll: EPERM), and it never blocks. Read it in a thread,
            # as file reads go in async code
            return await asyncio.to_thread(os.read, self.fd, 4096)
        try:
            return await ready
        finally:
            loop.remove_reader(self.fd)

    async def line(self) -> str | None:
        """The next line, without its newline; None at the end of input."""
        while b"\n" not in self.buffer:
            chunk = await self._chunk()
            if not chunk:
                rest, self.buffer = self.buffer, b""
                return rest.decode() if rest else None
            self.buffer += chunk
        line, _, self.buffer = self.buffer.partition(b"\n")
        return line.decode().rstrip("\r")


def choices_of(question: dict) -> list[str]:
    return (
        list(question.get("choices") or [])
        if question.get("type") == "multiple_choice"
        else []
    )


def show(question: dict, number: int | None) -> str:
    """The question as printed: its text, then its numbered choices and "Other"."""
    head = f"{number}. " if number else ""
    optional = " (optional)" if not question.get("required", True) else ""
    lines = [f"{head}{question['question']}{optional}"]
    choices = choices_of(question)
    lines += [f"  {i}. {choice}" for i, choice in enumerate(choices, 1)]
    if choices:
        lines.append(f"  {len(choices) + 1}. Other (type your answer)")
    return "\n".join(lines)


def parse(question: dict, line: str) -> str | None:
    """The answer a typed line gives, "" for none, or None when the line picked "Other" and the
    answer still has to be typed."""
    text = line.strip()
    choices = choices_of(question)
    if choices and text.isdigit():
        picked = int(text)
        if 1 <= picked <= len(choices):
            return choices[picked - 1]
        if picked == len(choices) + 1:
            return None
    return text


async def ask(request: dict, terminal: Terminal) -> list[str] | None:
    """Ask each question of the request in the terminal; the answers, or None if the input ended
    first (nobody at the terminal)."""
    questions = request.get("questions") or []
    print("\nGen9 needs your answer:", file=sys.stderr, flush=True)
    answers = []
    for i, question in enumerate(questions, 1):
        print(
            show(question, i if len(questions) > 1 else None),
            file=sys.stderr,
            flush=True,
        )
        while True:
            print("> ", end="", file=sys.stderr, flush=True)
            line = await terminal.line()
            if line is None:
                return None
            answer = parse(question, line)
            if answer is None:
                print("Your answer: ", end="", file=sys.stderr, flush=True)
                line = await terminal.line()
                if line is None:
                    return None
                answer = line.strip()
            if answer or not question.get("required", True):
                answers.append(answer)
                break
            print("This one needs an answer.", file=sys.stderr, flush=True)
    return answers


async def retry_or_stop(request: dict, terminal: Terminal) -> bool | None:
    """A run that failed in a way someone can fix: Retry (the default) or not; None if the input
    ended first."""
    print(
        f"\nGen9 couldn't finish: {request.get('error')}", file=sys.stderr, flush=True
    )
    print("Retry? [Y/n] ", end="", file=sys.stderr, flush=True)
    line = await terminal.line()
    if line is None:
        return None
    return line.strip().lower() not in ("n", "no")
