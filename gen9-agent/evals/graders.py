"""Graders: each reads a trial's outcome (what the person saw, or what Gen9 keeps) and says
whether it passed, and why. Code graders are the default: fast, cheap and objective. `judge` asks
a model about what code can't check, with the criteria written out. `criterion` grades one rubric
criterion of a research answer against the pages the run consulted.

Text a grader expects may hold `{tag}`, the trial's random tag, or be a function of the tag.
"""

import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from .gen9 import Trial

if TYPE_CHECKING:
    from langchain_openai import ChatOpenAI

Text = str | Callable[[str], str]


@dataclass(frozen=True)
class Verdict:
    passed: bool
    why: str = ""


@dataclass(frozen=True)
class Grader:
    # The score's name in Langfuse: the kind of check, the same across tasks
    name: str
    # What it checks, in words (the score's comment)
    says: str
    check: Callable[[Trial], Awaitable[Verdict]]

    async def __call__(self, trial: Trial) -> Verdict:
        return await self.check(trial)


def _text(text: Text, tag: str) -> str:
    return text.format(tag=tag) if isinstance(text, str) else text(tag)


def _described(text: Text) -> str:
    return "the expected text" if callable(text) else f"“{text}”"


def _code(name: str, says: str, check: Callable[[Trial], Verdict]) -> Grader:
    async def run(trial: Trial) -> Verdict:
        return check(trial)

    return Grader(name, says, run)


def _quote(text: str, chars: int = 120) -> str:
    text = " ".join(text.split())
    return f"“{text[:chars]}{'…' if len(text) > chars else ''}”"


def finished() -> Grader:
    """Every run of the trial ended in success. The harness adds it to every task."""

    def check(trial: Trial) -> Verdict:
        if trial.error:
            return Verdict(False, trial.error)
        bad = [t for t in trial.turns if t.status != "success"]
        if not trial.turns or bad:
            return Verdict(False, "; ".join(f"{t.status}: {t.error}" for t in bad))
        return Verdict(True)

    return _code("finished", "every run ended in success", check)


def says(text: Text) -> Grader:
    """The answer contains the text (case aside)."""

    def check(trial: Trial) -> Verdict:
        want = _text(text, trial.tag)
        ok = want.casefold() in trial.answer.casefold()
        return Verdict(ok, "" if ok else f"answer {_quote(trial.answer)}")

    return _code("says", f"the answer contains {_described(text)}", check)


def lacks(text: Text) -> Grader:
    def check(trial: Trial) -> Verdict:
        ok = _text(text, trial.tag).casefold() not in trial.answer.casefold()
        return Verdict(ok, "" if ok else f"answer {_quote(trial.answer)}")

    return _code("lacks", f"the answer doesn't contain {_described(text)}", check)


def _bare(text: str) -> str:
    """An answer without what doesn't change it: spaces, quotes, emphasis, a closing period."""
    return text.strip().strip("`*\"'“”‘’").strip().rstrip(".").strip().casefold()


def only(text: Text) -> Grader:
    """The answer is the text and nothing else ("Reply with only …")."""

    def check(trial: Trial) -> Verdict:
        want = _text(text, trial.tag)
        ok = _bare(trial.answer) == _bare(want)
        return Verdict(ok, "" if ok else f"answer {_quote(trial.answer)}")

    return _code("only", f"the answer is {_described(text)} and nothing else", check)


def used(tool: str) -> Grader:
    def check(trial: Trial) -> Verdict:
        names = [s["name"] for s in trial.steps]
        ok = tool in names
        return Verdict(ok, "" if ok else f"tools: {', '.join(names) or 'none'}")

    return _code("used", f"the agent used {tool}", check)


def did_not_use(*tools: str) -> Grader:
    def check(trial: Trial) -> Verdict:
        names = [s["name"] for s in trial.steps if s["name"] in tools]
        return Verdict(not names, f"used {', '.join(names)}" if names else "")

    return _code("did not use", f"the agent used none of {', '.join(tools)}", check)


def cites(pages: int = 1) -> Grader:
    """The last turn's answer shows where it came from: sources or citations."""

    def check(trial: Trial) -> Verdict:
        last = trial.turns[-1] if trial.turns else None
        urls = (
            {u for s in last.steps for u in s["sources"]} | set(last.citations)
            if last
            else set()
        )
        ok = len(urls) >= pages
        return Verdict(ok, f"{len(urls)} page(s)")

    return _code("cites", f"the answer lists at least {pages} source(s)", check)


def cites_earlier_chat() -> Grader:
    """A search of past chats listed the chat the task set up as a source."""

    def check(trial: Trial) -> Verdict:
        found = [
            u
            for s in trial.steps
            if s["name"] == "search_past_chats"
            for u in s["sources"]
        ]
        ok = any(u.endswith(f"/chat/{c}") for c in trial.earlier for u in found)
        return Verdict(ok, "" if ok else f"sources: {', '.join(found) or 'none'}")

    return _code("cites earlier chat", "the earlier chat is listed as a source", check)


def asked(kind: str) -> Grader:
    """The run asked the person (an approval, a question) before going on."""

    def check(trial: Trial) -> Verdict:
        kinds = [r["kind"] for r in trial.requests]
        ok = kind in kinds
        return Verdict(ok, "" if ok else f"asked: {', '.join(kinds) or 'nothing'}")

    return _code("asked", f"the run asked for {kind}", check)


def notified() -> Grader:
    """A background task's end was told to the chat, and the chat answered after it."""

    def check(trial: Trial) -> Verdict:
        told = [i for i, m in enumerate(trial.messages) if m.get("notice")]
        ok = bool(told) and any(
            m["role"] == "assistant" for m in trial.messages[told[-1] + 1 :]
        )
        return Verdict(ok, "" if ok else f"{len(told)} notice(s), no answer after")

    return _code("notified", "the task's end was told to the chat, and answered", check)


def remembers(text: Text) -> Grader:
    def check(trial: Trial) -> Verdict:
        ok = _text(text, trial.tag).casefold() in trial.memory.casefold()
        return Verdict(ok, "" if ok else f"memory {_quote(trial.memory)}")

    return _code("remembers", f"memory holds {_described(text)}", check)


def forgets(text: Text) -> Grader:
    def check(trial: Trial) -> Verdict:
        ok = _text(text, trial.tag).casefold() not in trial.memory.casefold()
        return Verdict(ok, "" if ok else f"memory {_quote(trial.memory)}")

    return _code("forgets", f"memory doesn't hold {_described(text)}", check)


def file_has(name: str, text: Text) -> Grader:
    """The chat has the file (its environment shared it), and it contains the text."""

    def check(trial: Trial) -> Verdict:
        if name not in trial.files:
            return Verdict(False, f"files: {', '.join(trial.files) or 'none'}")
        ok = _text(text, trial.tag) in trial.files[name]
        return Verdict(ok, "" if ok else f"{name}: {_quote(trial.files[name])}")

    return _code("file", f"the chat's file {name} contains {_described(text)}", check)


def table(*columns: str) -> Grader:
    """The answer has a Markdown table whose header names the columns."""

    def check(trial: Trial) -> Verdict:
        lines = trial.answer.splitlines()
        for header, rule in pairwise(lines):
            cells = [
                c.strip().strip("*").casefold() for c in header.strip("| ").split("|")
            ]
            if re.fullmatch(
                r"\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*", rule
            ) and all(c.casefold() in cells for c in columns):
                return Verdict(True)
        return Verdict(False, f"answer {_quote(trial.answer)}")

    return _code(
        "table", f"the answer has a table with columns {', '.join(columns)}", check
    )


JUDGE_PROMPT = """You grade an assistant's answer against criteria. Judge only whether the \
answer meets every criterion, not its style or length unless a criterion asks.

<criteria>
{criteria}
</criteria>

<conversation>
{inputs}
</conversation>

<answer>
{outputs}
</answer>"""


def _judge_model(http: httpx.AsyncClient) -> "ChatOpenAI":
    """The router's alias `GEN9_EVALS_JUDGE` (default `chat`), with the client given: one per
    call, in the event loop that awaits it (Langfuse's runner has its own)."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=os.environ.get("GEN9_EVALS_JUDGE", "chat"),
        base_url=f"{os.environ['GEN9_MODELS_URL'].rstrip('/')}/v1",
        api_key=os.environ["GEN9_MODELS_KEY"],
        http_async_client=http,
    )


def judge(criteria: str) -> Grader:
    """A model reads the conversation and the answer and says whether the criteria hold
    (openevals' judge)."""

    async def check(trial: Trial) -> Verdict:
        # Imported here: the graders' tests need no judge
        from openevals.llm import create_async_llm_as_judge

        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as http:
            grade = create_async_llm_as_judge(
                prompt=JUDGE_PROMPT, judge=_judge_model(http), feedback_key="judge"
            )
            verdict = await grade(
                inputs="\n\n".join(t.message for t in trial.turns),
                outputs=trial.answer,
                criteria=criteria.format(tag=trial.tag),
            )
        # One criteria set, one result (a list comes only from several choices)
        result = verdict[0] if isinstance(verdict, list) else verdict
        return Verdict(bool(result["score"]), str(result.get("comment") or ""))

    return Grader("judge", criteria, check)


CRITERION_PROMPT = """You check one criterion of a research assistant's answer. Judge only this \
criterion, not the rest of the answer, its style or its length. Answer "unknown" when the answer \
and the pages don't let you tell.

A criterion about what the answer says (it says, names or identifies something) is met only when \
the answer itself says it, with every specific the criterion names: a version, a date or an \
identifier, in parentheses too. Don't credit what only the pages, or a page the answer cites, say.

<question>
{inputs}
</question>

<pages the assistant consulted>
{sources}
</pages the assistant consulted>

<answer>
{outputs}
</answer>

<criterion>
{criterion}
</criterion>"""

# The pages a judge reads with a criterion, at most (the tools' outputs, 2,000 characters each)
SOURCES_CHARS = 24_000


class CriterionVerdict(BaseModel):
    reasoning: str = Field(description="Why, pointing to the answer and the pages")
    verdict: Literal["met", "not met", "unknown"]


def consulted(trial: Trial) -> str:
    """What the trial's tools returned, each under its tool's name: the pages it read."""
    pages = [f"[{s['name']}]\n{s['output']}" for s in trial.steps if s.get("output")]
    return "\n\n".join(pages)[:SOURCES_CHARS] or "(none)"


async def _criterion_verdict(
    question: str, answer: str, sources: str, criterion: str
) -> dict[str, Any]:
    from openevals.llm import create_async_llm_as_judge

    async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as http:
        grade = create_async_llm_as_judge(
            prompt=CRITERION_PROMPT,
            judge=_judge_model(http),
            output_schema=CriterionVerdict,
        )
        verdict = await grade(
            inputs=question, outputs=answer, sources=sources, criterion=criterion
        )
    return as_verdict(verdict)


def as_verdict(result: Any) -> dict[str, Any]:
    """The judge's verdict as a dict: openevals returns an output schema's instance when the
    schema is a Pydantic model (0.2.0), and a dict for a JSON schema."""
    if isinstance(result, BaseModel):
        return result.model_dump()
    if isinstance(result, dict):
        return {str(k): v for k, v in result.items()}
    raise TypeError(f"the judge returned {type(result).__name__}, not a verdict")


def criterion(text: str, name: str = "criterion") -> Grader:
    """One rubric criterion, graded by a judge call of its own that sees the question, the
    answer and the pages the run consulted, and may answer "unknown" (not met). Each verdict is a
    Langfuse observation scored `criterion met`, which people score too when they calibrate the
    judge (`python -m evals calibrate`)."""

    async def check(trial: Trial) -> Verdict:
        from langfuse import get_client
        from langfuse.api import ScoreDataType

        question = "\n\n".join(t.message for t in trial.turns)
        sources = consulted(trial)
        with get_client().start_as_current_observation(
            name="criterion",
            as_type="evaluator",
            input={
                "criterion": text,
                "question": question,
                "answer": trial.answer,
                "pages": sources,
            },
            metadata={"grader": name, "tag": trial.tag},
        ) as observation:
            verdict = await _criterion_verdict(question, trial.answer, sources, text)
            met = verdict.get("verdict") == "met"
            observation.update(output=verdict)
            observation.score(
                name=CRITERION_SCORE,
                value=1.0 if met else 0.0,
                data_type=ScoreDataType.BOOLEAN,
                comment=str(verdict.get("reasoning") or ""),
            )
        return Verdict(met, f"{verdict.get('verdict')}: {verdict.get('reasoning')}")

    return Grader(name, text, check)


# The score each criterion's verdict gets, and people give it when calibrating
CRITERION_SCORE = "criterion met"


def cites_site(*sites: str) -> Grader:
    """The last turn consulted or cited a page from one of these sites: a primary source."""

    def check(trial: Trial) -> Verdict:
        last = trial.turns[-1] if trial.turns else None
        urls = (
            {u for s in last.steps for u in s["sources"]} | set(last.citations)
            if last
            else set()
        )
        hosts = {urlparse(u).hostname or "" for u in urls}
        ok = any(h == d or h.endswith(f".{d}") for h in hosts for d in sites)
        return Verdict(ok, "" if ok else f"sites: {', '.join(sorted(hosts)) or 'none'}")

    return _code(
        "source", f"a page from {' or '.join(sites)} is among its sources", check
    )


def matches(pattern: str, says: str) -> Grader:
    """The answer matches a regular expression (case aside)."""

    def check(trial: Trial) -> Verdict:
        ok = re.search(pattern, trial.answer, re.IGNORECASE) is not None
        return Verdict(ok, "" if ok else f"answer {_quote(trial.answer)}")

    return _code("matches", says, check)
