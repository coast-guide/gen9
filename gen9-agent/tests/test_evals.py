"""The evals harness (gen9-agent/evals): graders pass and fail on what they should, trials become
the rates Langfuse keeps, tokens are refreshed one at a time, and a run is followed to its end
with what it asks answered. No stack or model: Gen9's API is a mock transport."""

import asyncio
import json
import os
import stat
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from langfuse import Evaluation

from evals import calibrate, gen9, graders
from evals.gen9 import Credentials, Gen9, Trial, Turn
from evals.run import Experiment, overall, summary, with_names
from evals.tasks import ANY_ANSWER, REGRESSION, SUITES, Task

pytestmark = pytest.mark.asyncio


def trial(answer: str = "", **fields: Any) -> Trial:
    turn = Turn(
        "hi",
        "run-1",
        fields.pop("status", "success"),
        answer=answer,
        steps=fields.pop("steps", []),
        citations=fields.pop("citations", []),
        requests=fields.pop("requests", []),
        seconds=fields.pop("seconds", 2.0),
    )
    return Trial(tag="ab12cd", chat="c1", turns=[turn], **fields)


async def test_only_takes_the_bare_answer_and_nothing_more() -> None:
    assert (await graders.only("noted")(trial("Noted."))).passed
    assert (await graders.only("noted")(trial("**noted**"))).passed
    missed = await graders.only("noted")(trial("Noted, thanks!"))
    assert not missed.passed and "Noted, thanks!" in missed.why


async def test_expected_text_takes_the_trials_tag_or_a_function_of_it() -> None:
    assert (await graders.says("FRUIT-{tag}")(trial("It's fruit-AB12CD."))).passed
    assert not (await graders.says("FRUIT-{tag}")(trial("It's FRUIT."))).passed
    assert (await graders.only(lambda tag: tag[::-1])(trial("dc21ba"))).passed


async def test_a_table_needs_its_header_and_a_rule() -> None:
    answer = "Here:\n\n| **Drink** | Caffeine | Origin |\n|---|:---:|---|\n| Tea | some | China |"
    assert (await graders.table("Drink", "Caffeine", "Origin")(trial(answer))).passed
    assert not (await graders.table("Drink", "Price")(trial(answer))).passed
    assert not (
        await graders.table("Drink")(trial("Drink | Caffeine\nTea | some"))
    ).passed


async def test_sources_and_the_earlier_chat() -> None:
    searched = trial(
        "The Marram",
        steps=[
            {
                "name": "search_past_chats",
                "status": "success",
                "sources": ["http://x/chat/e1"],
            }
        ],
        earlier=["e1"],
    )
    assert (await graders.cites_earlier_chat()(searched)).passed
    assert (await graders.cites(1)(searched)).passed
    elsewhere = trial(
        steps=[
            {
                "name": "search_past_chats",
                "status": "success",
                "sources": ["http://x/chat/e2"],
            }
        ],
        earlier=["e1"],
    )
    assert not (await graders.cites_earlier_chat()(elsewhere)).passed
    assert not (await graders.cites(1)(trial("no pages"))).passed
    assert (await graders.cites(2)(trial(citations=["http://a", "http://b"]))).passed


async def test_tools_questions_memory_and_files() -> None:
    used = trial(steps=[{"name": "web_search", "status": "success", "sources": []}])
    assert (await graders.used("web_search")(used)).passed
    assert not (await graders.did_not_use("web_search")(used)).passed
    assert (await graders.did_not_use("search_past_chats")(used)).passed
    asked = trial(requests=[{"kind": "approval", "asked": {}, "reply": {}}])
    assert (await graders.asked("approval")(asked)).passed
    assert not (await graders.asked("question")(asked)).passed
    remembered = trial(memory="- Favourite colour: teal\n")
    assert (await graders.remembers("TEAL")(remembered)).passed
    assert (await graders.forgets("diabetes")(remembered)).passed
    assert not (await graders.forgets("teal")(remembered)).passed
    saved = trial(files={"greeting.txt": "hello ab12cd\n"})
    assert (await graders.file_has("greeting.txt", "hello {tag}")(saved)).passed
    assert not (await graders.file_has("other.txt", "hello")(saved)).passed


async def test_a_background_task_counts_once_told_and_answered() -> None:
    told = trial(
        messages=[
            {"role": "user", "content": "start it", "notice": False},
            {"role": "assistant", "content": "Started.", "notice": False},
            {"role": "user", "content": "[Background task …", "notice": True},
            {"role": "assistant", "content": "It's 391.", "notice": False},
        ]
    )
    assert (await graders.notified()(told)).passed
    assert told.answer == "It's 391."
    assert not (await graders.notified()(trial(messages=told.messages[:3]))).passed


async def test_finished_fails_a_run_that_didnt_succeed_or_a_harness_error() -> None:
    assert (await graders.finished()(trial("ok"))).passed
    assert not (await graders.finished()(trial("", status="error"))).passed
    broken = trial(
        "ok", error="TimeoutError: the earlier chat wasn't searchable in time"
    )
    assert "searchable" in (await graders.finished()(broken)).why


async def test_the_answerer_approves_and_answers_in_order_and_stops_the_rest() -> None:
    task = Task("t", ("hi",), (), "why", answers=("vegetarian",))
    answer = task.answerer()
    approval = {"kind": "approval", "action_requests": [{}, {}]}
    assert answer(approval) == {"decisions": [{"type": "approve"}, {"type": "approve"}]}
    two = {"kind": "question", "questions": [{}, {}]}
    assert answer(two) == {"answers": ["vegetarian", ANY_ANSWER]}
    assert answer({"kind": "retry", "error": "The model failed"}) is None


async def test_tasks_are_unique_and_parallel_only_when_they_share_nothing() -> None:
    for suite in SUITES.values():
        assert len({t.id for t in suite}) == len(suite)
    assert any(t.parallel for t in REGRESSION) and any(
        not t.parallel for t in REGRESSION
    )
    with pytest.raises(ValueError, match="runs alone"):
        Task("t", ("hi",), (), "why", memory="x", parallel=True)


async def test_a_kind_used_twice_is_numbered() -> None:
    names = [
        n
        for n, _ in with_names(
            [graders.says("a"), graders.used("x"), graders.says("b")]
        )
    ]
    assert names == ["says", "used", "says 2"]


def experiment(task: Task) -> Experiment:
    return Experiment(
        langfuse=None,
        suite="regression",
        tasks={"item": task},
        credentials=Credentials(Path("/nonexistent")),
        trials=3,
        keep=False,
    )


async def test_trials_become_rates_pass_at_k_and_pass_to_the_k() -> None:
    task = Task("t", ("hi",), (graders.says("yes"),), "why", max_tool_calls=1)
    trials = [
        trial("yes"),
        trial("no"),
        trial("yes", steps=[{"name": "a", "sources": []}] * 2),
    ]
    scores = {
        e.name: e
        for e in await experiment(task).grade(
            input={}, output=trials, expected_output=[], metadata={"task": "t"}
        )
    }
    assert scores["passed"].value == pytest.approx(2 / 3)
    assert scores["pass@3"].value == 1.0 and scores["pass^3"].value == 0.0
    assert scores["finished"].value == 1.0
    assert scores["says"].value == pytest.approx(2 / 3)
    assert "trial 2: answer “no”" in (scores["says"].comment or "")
    assert scores["tool calls"].value == 0 and scores["seconds"].value == 2.0
    assert scores["within tool calls"].value == pytest.approx(2 / 3)

    results = [
        SimpleNamespace(
            item=SimpleNamespace(metadata={"task": "t"}),
            output=trials,
            evaluations=list(scores.values()),
        ),
        SimpleNamespace(
            item=SimpleNamespace(metadata={"task": "u"}),
            output=[trial("yes")] * 3,
            evaluations=[
                Evaluation(name="passed", value=1.0),
                Evaluation(name="pass@3", value=1.0),
                Evaluation(name="pass^3", value=1.0),
            ],
        ),
    ]
    suite = {e.name: e.value for e in overall(item_results=results)}
    assert suite["pass rate"] == pytest.approx((2 / 3 + 1) / 2, abs=1e-3)
    assert suite["pass@3"] == 1.0 and suite["pass^3"] == 0.5
    assert suite["median seconds"] == 2.0

    text, ok = summary(
        SimpleNamespace(
            item_results=results, run_evaluations=[], dataset_run_url="http://lf/run"
        )
    )
    assert not ok
    assert (
        "FAIL  t: 2 of 3 trials passed every grader (median 0 tool calls, 2 s)" in text
    )
    assert "ok    u:" in text and "Langfuse: http://lf/run" in text


def token_response(n: int) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": f"access-{n}",
            "refresh_token": f"refresh-{n}",
            "expires_in": 300,
        },
    )


async def test_tokens_are_refreshed_once_for_concurrent_callers_and_saved(
    tmp_path: Path,
) -> None:
    (tmp_path / "credentials.json").write_text(
        json.dumps(
            {
                "access_token": "old",
                "refresh_token": "refresh-0",
                "expires_at": time.time(),
            }
        )
    )
    used: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(200, json={"token_endpoint": "http://kc/token"})
        form = dict(x.split("=") for x in request.content.decode().split("&"))
        used.append(form["refresh_token"])
        return token_response(len(used))

    credentials = Credentials(tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        tokens = await asyncio.gather(
            *(credentials.access_token(http) for _ in range(5))
        )
    assert tokens == ["access-1"] * 5
    assert used == ["refresh-0"]  # a rotated refresh token is never sent twice
    saved = tmp_path / "credentials.json"
    assert json.loads(saved.read_text())["refresh_token"] == "refresh-1"
    assert stat.S_IMODE(os.stat(saved).st_mode) == 0o600


def sse(*events: tuple[str, dict[str, Any]]) -> bytes:
    return "".join(
        f"id: {i}\nevent: {kind}\ndata: {json.dumps(data)}\n\n"
        for i, (kind, data) in enumerate(events, 1)
    ).encode()


async def test_a_turn_is_followed_to_its_end_with_its_approval_answered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gen9, "API", "http://api")
    (tmp_path / "credentials.json").write_text(
        json.dumps(
            {"access_token": "t", "refresh_token": "r", "expires_at": time.time() + 999}
        )
    )
    answered: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        assert request.headers["Authorization"] == "Bearer t"
        if path == "/v1/threads/c1/runs/stream":
            assert json.loads(request.content) == {
                "message": "hi",
                "permission_mode": "ask",
            }
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse(
                    ("run.queued", {"run_id": "r1"}),
                    (
                        "input.requested",
                        {"id": "i1", "kind": "approval", "action_requests": [{}]},
                    ),
                    (
                        "tool.completed",
                        {"id": "call-1", "name": "edit_file", "output": "Updated"},
                    ),
                    ("message.delta", {"id": "m", "text": "Done"}),
                    ("run.completed", {"status": "success", "error": None}),
                ),
            )
        if path == "/v1/threads/c1/runs/r1/inputs/i1":
            answered.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "i1"})
        if path == "/v1/threads/c1":
            return httpx.Response(
                200,
                json={
                    "messages": [
                        {"role": "user", "content": "hi", "run_id": "r1"},
                        {
                            "role": "assistant",
                            "content": "Done.",
                            "steps": [
                                {
                                    "id": "call-1",
                                    "name": "edit_file",
                                    "status": "success",
                                }
                            ],
                            "summarized": False,
                        },
                    ]
                },
            )
        if path == "/v1/threads/c1/runs/r1":
            return httpx.Response(
                200,
                json={
                    "started_at": "2026-09-26T00:00:01+00:00",
                    "finished_at": "2026-09-26T00:00:04.500000+00:00",
                },
            )
        return httpx.Response(404)

    task = Task("t", ("hi",), (), "why", mode="ask")
    async with Gen9(Credentials(tmp_path)) as client:
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        turn = await client.send("c1", "hi", "ask", task.answerer())
        await client.http.aclose()
    assert answered == [{"decisions": [{"type": "approve"}]}]
    assert turn.status == "success" and turn.answer == "Done." and turn.seconds == 3.5
    # What the tool returned stays with the step, for reading the trial after its chat is gone
    assert turn.steps == [
        {"name": "edit_file", "status": "success", "sources": [], "output": "Updated"}
    ]
    assert turn.requests[0]["kind"] == "approval"


async def test_a_primary_source_and_a_pattern() -> None:
    cited = trial(
        "Python 3.14.4 came out on 7 July 2026.",
        steps=[
            {"name": "web_search", "sources": ["https://docs.python.org/3/whatsnew/"]},
        ],
        citations=["https://www.python.org/downloads/"],
    )
    assert (await graders.cites_site("python.org")(cited)).passed
    assert not (await graders.cites_site("postgresql.org")(cited)).passed
    lookalike = trial(citations=["https://python.org.example.com/"])
    assert not (await graders.cites_site("python.org")(lookalike)).passed
    version = graders.matches(r"\b3\.\d+\.\d+\b", "a full version")
    assert (await version(cited)).passed
    assert not (await version(trial("Python 3.14 is the latest."))).passed


async def test_a_criterion_is_judged_with_the_pages_read_and_unknown_is_not_met(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, str, str, str]] = []

    async def verdict(question: str, answer: str, sources: str, criterion: str) -> dict:
        seen.append((question, answer, sources, criterion))
        return {
            "verdict": "unknown" if "date" in criterion else "met",
            "reasoning": "r",
        }

    monkeypatch.setattr(graders, "_criterion_verdict", verdict)
    read = trial(
        "PostgreSQL 17.",
        steps=[
            {"name": "web_search", "sources": [], "output": "PostgreSQL 17 released"},
            {"name": "ls", "sources": []},
        ],
    )
    named = await graders.criterion("Names PostgreSQL 17.", "coverage")(read)
    assert named.passed and named.why == "met: r"
    dated = await graders.criterion("Gives the release date.", "coverage")(read)
    assert not dated.passed and dated.why.startswith("unknown")
    question, answer, sources, _ = seen[0]
    assert question == "hi" and answer == "PostgreSQL 17."
    assert sources == "[web_search]\nPostgreSQL 17 released"
    assert graders.consulted(trial("no tools")) == "(none)"


async def test_the_judges_verdict_is_read_from_a_model_or_a_dict() -> None:
    # openevals 0.2.0 returns a Pydantic output schema's instance, not a dict
    model = graders.CriterionVerdict(reasoning="the page says so", verdict="met")
    assert graders.as_verdict(model) == {
        "reasoning": "the page says so",
        "verdict": "met",
    }
    assert (
        graders.as_verdict({"verdict": "unknown", "reasoning": ""})["verdict"]
        == "unknown"
    )
    with pytest.raises(TypeError):
        graders.as_verdict(["met"])


async def test_agreement_with_people_tpr_tnr_and_kappa() -> None:
    # 50 verdicts: people met 25 (judge met 20 of them), not met 25 (judge met 10 of them)
    pairs = [(True, True)] * 20 + [(True, False)] * 5 + [(False, True)] * 10
    pairs += [(False, False)] * 15
    a = calibrate.agreement(pairs)
    assert (a.tp, a.fn, a.fp, a.tn, a.pairs) == (20, 5, 10, 15, 50)
    assert a.accuracy == 0.7 and a.tpr == 0.8 and a.tnr == 0.6
    # observed 0.7, chance ((30·25) + (20·25)) / 50² = 0.5, so (0.7 − 0.5) / 0.5
    assert a.kappa == pytest.approx(0.4)
    assert not a.trusted
    close = calibrate.agreement([(True, True)] * 10 + [(False, False)] * 10)
    assert close.trusted and close.kappa == 1.0
    assert calibrate.agreement([]).accuracy is None


async def test_a_sample_is_half_met_and_half_not_filled_from_the_other_side() -> None:
    met, unmet = [f"m{i}" for i in range(10)], ["u0", "u1", "u2"]
    picked = calibrate.balanced(met, unmet, 8, seed=1)
    assert len(picked) == 8 and set(unmet) <= set(picked)
    few_met = calibrate.balanced(["m0", "m1"], [f"u{i}" for i in range(10)], 8, seed=1)
    assert len(few_met) == 8 and {"m0", "m1"} <= set(few_met)
    assert calibrate.balanced(["m0"], [], 8, seed=1) == ["m0"]
    assert calibrate.balanced(met, unmet, 8, seed=1) == picked  # repeatable
