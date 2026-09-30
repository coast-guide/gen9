"""A suite as a Langfuse experiment: its tasks synced to a dataset, each task run k times in fresh
chats, every trial graded, and the scores kept with the dataset run (Langfuse keeps run-level
scores only for a dataset run: explore/evals/NOTES.md)."""

import asyncio
import secrets
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx
from langfuse import Evaluation, Langfuse
from langfuse.api import DatasetItem, DatasetStatus, NotFoundError
from langfuse.experiment import ExperimentItem, ExperimentItemResult, ExperimentResult

from .gen9 import APP, Credentials, Gen9, Trial
from .graders import Grader, finished
from .tasks import Task

# Settings > Memory switches, as a trial starts unless its task names them
CONTROLS = {"remember": True, "search_past_chats": True}
# Item scores that aren't a grader's
NOT_GRADERS = frozenset({"passed", "tool calls", "seconds", "within tool calls"})


def dataset(suite: str) -> str:
    return f"gen9-evals-{suite}"


def item_id(suite: str, task: Task) -> str:
    return f"gen9-evals-{suite}-{task.id}"


def sync(
    langfuse: Langfuse, suite: str, tasks: Sequence[Task]
) -> dict[str, DatasetItem]:
    """Bring the suite's dataset in line with the code: an item per task (updated when the task
    changed), and items whose task left the code archived. Returns the items by id. Blocking
    (the SDK's dataset helpers are sync): call it in a thread."""
    name = dataset(suite)
    try:
        items = {i.id: i for i in langfuse.get_dataset(name).items}
    except NotFoundError:
        langfuse.create_dataset(
            name=name, description=f"Gen9's {suite} evals (gen9-agent/evals/tasks.py)"
        )
        items = {}
    wanted = {item_id(suite, t): t for t in tasks}
    for id_, item in list(items.items()):
        if id_ not in wanted and item.status == DatasetStatus.ACTIVE:
            langfuse.create_dataset_item(
                dataset_name=name,
                id=id_,
                input=item.input,
                expected_output=item.expected_output,
                metadata=item.metadata,
                status=DatasetStatus.ARCHIVED,
            )
            del items[id_]
    for id_, task in wanted.items():
        input_ = task_input(task)
        expected = [g.says for g in task.graders]
        metadata = {"suite": suite, "task": task.id, "why": task.why}
        have = items.get(id_)
        if (
            have is None
            or have.status != DatasetStatus.ACTIVE
            or (have.input, have.expected_output, have.metadata)
            != (input_, expected, metadata)
        ):
            items[id_] = langfuse.create_dataset_item(
                dataset_name=name,
                id=id_,
                input=input_,
                expected_output=expected,
                metadata=metadata,
                status=DatasetStatus.ACTIVE,
            )
    return {i: items[i] for i in wanted}


def task_input(task: Task) -> dict[str, Any]:
    return {
        "messages": list(task.messages),
        "mode": task.mode,
        **({"memory": task.memory} if task.memory else {}),
        **({"controls": task.controls} if task.controls else {}),
        **({"earlier": list(task.earlier)} if task.earlier else {}),
        **({"answers": list(task.answers)} if task.answers else {}),
    }


@dataclass
class Experiment:
    """What one experiment's trials share: the tasks by item, the sign-in, and how to run."""

    langfuse: Langfuse
    suite: str
    tasks: dict[str, Task]  # by dataset item id
    credentials: Credentials
    trials: int
    keep: bool

    async def run_task(
        self, *, item: ExperimentItem, **_: dict[str, Any]
    ) -> list[Trial]:
        """Langfuse's task function: the item's task, k times, each in fresh chats."""
        task = self.tasks[str(getattr(item, "id", ""))]
        async with Gen9(self.credentials) as gen9:
            await gen9.set_controls({**CONTROLS, **task.controls})
            if task.parallel:
                await gen9.set_memory("")
                return list(
                    await asyncio.gather(
                        *(self.trial(gen9, task, n) for n in range(self.trials))
                    )
                )
            return [await self.trial(gen9, task, n) for n in range(self.trials)]

    async def trial(self, gen9: Gen9, task: Task, n: int) -> Trial:
        trial = Trial(tag=secrets.token_hex(3))
        messages = [m.format(tag=trial.tag) for m in task.messages]
        with self.langfuse.start_as_current_observation(
            name=f"trial {n + 1}", as_type="span", input={"messages": messages}
        ) as span:
            try:
                await self._attempt(gen9, task, trial, messages)
            except Exception as e:  # noqa: BLE001 (any failure fails the trial, graded as not finished)
                trial.error = f"{type(e).__name__}: {e}"
            cleanup = None
            if not self.keep:
                try:
                    for chat in filter(None, [trial.chat, *trial.earlier]):
                        await gen9.delete_chat(chat)
                except httpx.HTTPError as e:  # not the agent's doing: noted, not graded
                    cleanup = f"{type(e).__name__}: {e}"
            # The transcript as the span's output: Langfuse keeps metadata values short
            span.update(
                output={
                    "answer": trial.answer,
                    "turns": [vars(t) for t in trial.turns],
                    "memory": trial.memory,
                    "files": list(trial.files),
                    **({"error": trial.error} if trial.error else {}),
                },
                metadata={
                    "tag": trial.tag,
                    **({"cleanup": cleanup} if cleanup else {}),
                    **(
                        {"chat": f"{APP}/chat/{trial.chat}"}
                        if self.keep and trial.chat
                        else {}
                    ),
                },
            )
        return trial

    async def _attempt(
        self, gen9: Gen9, task: Task, trial: Trial, messages: list[str]
    ) -> None:
        answer = task.answerer()
        if not task.parallel:
            await gen9.set_memory(task.memory.format(tag=trial.tag))
        if task.earlier:
            earlier = await gen9.start_chat()
            trial.earlier.append(earlier)
            for message in task.earlier:
                await gen9.send(earlier, message.format(tag=trial.tag), "auto", answer)
            if task.find and not await gen9.searchable(
                task.find.format(tag=trial.tag), earlier
            ):
                raise TimeoutError("the earlier chat wasn't searchable in time")
            # Only a search may find it: whatever the earlier chat remembered goes
            await gen9.set_memory(task.memory.format(tag=trial.tag))
        trial.chat = await gen9.start_chat()
        for message in messages:
            turn = await gen9.send(trial.chat, message, task.mode, answer)
            trial.turns.append(turn)
            if turn.status != "success":
                break
        thread = await (
            gen9.settle(trial.chat)
            if task.background
            else gen9.call("GET", f"/v1/threads/{trial.chat}")
        )
        trial.messages = [
            {
                "role": m["role"],
                "content": m["content"],
                "notice": m.get("notice", False),
            }
            for m in thread["messages"]
        ]
        trial.memory = await gen9.memory()
        trial.files = await gen9.text_files(trial.chat)

    async def grade(
        self,
        *,
        input: Any,
        output: list[Trial],
        expected_output: Any,
        metadata: dict[str, Any] | None,
        **_: dict[str, Any],
    ) -> list[Evaluation]:
        """Langfuse's item evaluator: every grader on every trial, as rates over the trials."""
        task = next(
            t for t in self.tasks.values() if t.id == (metadata or {}).get("task")
        )
        graders = with_names([finished(), *task.graders])
        verdicts = [[await g(t) for _, g in graders] for t in output]
        passed = [all(v.passed for v in vs) for vs in verdicts]
        k = len(output)
        evaluations = [
            Evaluation(
                name="passed",
                value=rate(passed),
                comment=f"{sum(passed)} of {k} trials passed every grader",
            ),
            Evaluation(name=f"pass@{k}", value=float(any(passed))),
            Evaluation(name=f"pass^{k}", value=float(all(passed))),
        ]
        for i, (name, grader) in enumerate(graders):
            misses = [
                f"trial {n + 1}: {vs[i].why}"
                for n, vs in enumerate(verdicts)
                if not vs[i].passed
            ]
            evaluations.append(
                Evaluation(
                    name=name,
                    value=rate([vs[i].passed for vs in verdicts]),
                    comment="; ".join([grader.says, *misses]),
                )
            )
        # Efficiency: logged, never failing
        calls = [len(t.steps) for t in output]
        evaluations.append(
            Evaluation(name="tool calls", value=statistics.median(calls))
        )
        seconds = [s for t in output if (s := trial_seconds(t)) is not None]
        if seconds:
            evaluations.append(
                Evaluation(name="seconds", value=round(statistics.median(seconds), 1))
            )
        if task.max_tool_calls is not None:
            evaluations.append(
                Evaluation(
                    name="within tool calls",
                    value=rate([c <= task.max_tool_calls for c in calls]),
                    comment=f"at most {task.max_tool_calls}",
                )
            )
        return evaluations


def with_names(graders: list[Grader]) -> list[tuple[str, Grader]]:
    """Each grader with its score's name; a kind used twice in a task is numbered."""
    seen: dict[str, int] = {}
    named = []
    for g in graders:
        seen[g.name] = seen.get(g.name, 0) + 1
        named.append((g.name if seen[g.name] == 1 else f"{g.name} {seen[g.name]}", g))
    return named


def rate(passed: list[bool]) -> float:
    return sum(passed) / len(passed) if passed else 0.0


def trial_seconds(trial: Trial) -> float | None:
    """The trial's runs' own time, queueing left out."""
    times = [t.seconds for t in trial.turns if t.seconds is not None]
    return sum(times) if times else None


def overall(
    *, item_results: list[ExperimentItemResult], **_: dict[str, Any]
) -> list[Evaluation]:
    """Langfuse's run evaluator: the suite's rates over its tasks."""
    scores = [{e.name: e.value for e in r.evaluations} for r in item_results]
    k = next((len(r.output) for r in item_results if r.output), 0)
    seconds = [
        s
        for r in item_results
        for t in r.output or []
        if (s := trial_seconds(t)) is not None
    ]

    def mean(name: str) -> float:
        values = [float(v) for s in scores if isinstance(v := s.get(name), int | float)]
        return round(statistics.fmean(values), 3) if values else 0.0

    return [
        Evaluation(
            name="pass rate", value=mean("passed"), comment="trials passed, over tasks"
        ),
        Evaluation(
            name=f"pass@{k}",
            value=mean(f"pass@{k}"),
            comment="tasks passed at least once",
        ),
        Evaluation(
            name=f"pass^{k}", value=mean(f"pass^{k}"), comment="tasks passed every time"
        ),
        *(
            [
                Evaluation(
                    name="median seconds", value=round(statistics.median(seconds), 1)
                )
            ]
            if seconds
            else []
        ),
    ]


def summary(result: ExperimentResult) -> tuple[str, bool]:
    """What `make evals` prints: each task's trials and the graders that missed, the suite's
    rates, and the run in Langfuse. True when every task passed every trial."""
    lines = []
    for r in result.item_results:
        scores = {e.name: e for e in r.evaluations}
        passed = scores.get("passed")
        task = (getattr(r.item, "metadata", None) or {}).get("task", "?")
        effort = ", ".join(
            f"{e.value:g} {name}"
            for key, name in (("tool calls", "tool calls"), ("seconds", "s"))
            if (e := scores.get(key)) is not None and isinstance(e.value, int | float)
        )
        lines.append(
            f"{'ok  ' if passed and passed.value == 1 else 'FAIL'}  {task}: "
            f"{passed.comment if passed else 'not graded'}"
            + (f" (median {effort})" if effort else "")
        )
        graded = [
            e
            for e in r.evaluations
            if e.name not in NOT_GRADERS and not e.name.startswith(("pass@", "pass^"))
        ]
        lines += [f"      {e.name}: {e.comment}" for e in graded if e.value != 1]
    lines.append("")
    lines += [f"{e.name}: {e.value}" for e in result.run_evaluations]
    if result.dataset_run_url:
        lines.append(f"Langfuse: {result.dataset_run_url}")
    ok = bool(result.item_results) and all(
        any(e.name == "passed" and e.value == 1 for e in r.evaluations)
        for r in result.item_results
    )
    return "\n".join(lines), ok
