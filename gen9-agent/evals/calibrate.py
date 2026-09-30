"""Calibrating the rubric judge against people (docs/plans/harness.md, Evals; Langfuse's
"Calibrate your LLM-as-a-judge" and Score Analytics).

Each rubric criterion's verdict is a `criterion` observation that the judge scored `criterion met`
(graders.criterion). A sample of them goes to Langfuse's "Judge calibration" annotation queue,
where people score the same observations with the same score. The report compares the two:
agreement, TPR, TNR and Cohen's kappa, and each disagreement. Langfuse's guide trusts a judge
whose TPR and TNR both reach 0.90.

    make evals-calibrate              # a sample of recent verdicts, half met and half not, to the queue
    make evals-calibrate REPORT=1     # how people's scores compare with the judge's

Labels from a reviewer who isn't a person (the developing agent, say, reading each answer and the
pages it consulted) are kept apart: `--label FILE --by WHO` records them under their own score,
`criterion met (reference)`, with who gave them and why, and the report compares them with the
judge in a section of their own. People's scores, when there are any, decide whether the judge
is trusted.
"""

import argparse
import asyncio
import json
import os
import random
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from langfuse import get_client
from langfuse.api import AnnotationQueueObjectType, ScoreConfigDataType

from .graders import CRITERION_SCORE

QUEUE = "Judge calibration"
# Labels that aren't people's (see above), under a score of their own
REFERENCE_SCORE = f"{CRITERION_SCORE} (reference)"
# Langfuse's calibration guide: a judge is trusted when TPR and TNR both reach this
BAR = 0.90
INSTRUCTIONS = f"""Score `{CRITERION_SCORE}` for each item: true if the answer meets the \
criterion, as a careful expert would judge it from the answer and the pages the assistant \
consulted (the item's input); false if it doesn't, or if you can't tell. Judge only that \
criterion, not the rest of the answer. The judge's verdict and reasoning are the item's output: \
score what you think, and say in a comment why you disagree when you do."""


@dataclass(frozen=True)
class Agreement:
    """People's scores (the truth) against the judge's, "met" being the positive class."""

    tp: int  # both met
    fn: int  # people met, judge not
    tn: int  # both not met
    fp: int  # people not, judge met

    @property
    def pairs(self) -> int:
        return self.tp + self.fn + self.tn + self.fp

    @property
    def accuracy(self) -> float | None:
        return (self.tp + self.tn) / self.pairs if self.pairs else None

    @property
    def tpr(self) -> float | None:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None

    @property
    def tnr(self) -> float | None:
        return self.tn / (self.tn + self.fp) if self.tn + self.fp else None

    @property
    def kappa(self) -> float | None:
        """Cohen's kappa: agreement beyond what chance would give."""
        n = self.pairs
        if not n:
            return None
        chance = (
            (self.tp + self.fp) * (self.tp + self.fn)
            + (self.tn + self.fn) * (self.tn + self.fp)
        ) / (n * n)
        observed = (self.tp + self.tn) / n
        return 1.0 if chance == 1 else (observed - chance) / (1 - chance)

    @property
    def trusted(self) -> bool:
        return (self.tpr or 0) >= BAR and (self.tnr or 0) >= BAR


def agreement(pairs: list[tuple[bool, bool]]) -> Agreement:
    """From (person, judge) verdicts on the same observations."""
    return Agreement(
        tp=sum(p and j for p, j in pairs),
        fn=sum(p and not j for p, j in pairs),
        tn=sum(not p and not j for p, j in pairs),
        fp=sum(not p and j for p, j in pairs),
    )


def balanced(met: list[Any], unmet: list[Any], n: int, seed: int) -> list[Any]:
    """Up to `n`, half met and half not, so TPR and TNR both get cases; the other side fills in
    when one side is short."""
    rng = random.Random(seed)
    half = n // 2
    pick_unmet = rng.sample(unmet, min(n - half, len(unmet)))
    pick_met = rng.sample(met, min(n - len(pick_unmet), len(met)))
    if len(pick_met) + len(pick_unmet) < n:
        rest = [u for u in unmet if u not in pick_unmet]
        pick_unmet += rng.sample(
            rest, min(n - len(pick_met) - len(pick_unmet), len(rest))
        )
    return pick_met + pick_unmet


async def _scores(api: Any, name: str = CRITERION_SCORE, **filters: Any) -> list[Any]:
    """Every score of this name matching the filters (v3 scores, paged by cursor)."""
    found: list[Any] = []
    cursor = None
    while True:
        page = await api.scores_v3.get_many_v3(
            name=name,
            # `details` carries the comment: why a person or reviewer said what they did
            fields="core,subject,details",
            limit=100,
            cursor=cursor,
            **filters,
        )
        found += page.data
        cursor = getattr(page.meta, "cursor", None)
        if not cursor or not page.data:
            return found


async def _queue(api: Any) -> str:
    """The queue's id, made with its score config when missing."""
    queues = (await api.annotation_queues.list_queues(limit=100)).data
    existing = next((q for q in queues if q.name == QUEUE), None)
    if existing:
        return existing.id
    configs = (await api.score_configs.get(limit=100)).data
    config = next(
        (
            c
            for c in configs
            if c.name == CRITERION_SCORE and c.data_type == ScoreConfigDataType.BOOLEAN
        ),
        None,
    ) or await api.score_configs.create(
        name=CRITERION_SCORE,
        data_type=ScoreConfigDataType.BOOLEAN,
        description="Does the answer meet this rubric criterion? (gen9-agent/evals)",
    )
    made = await api.annotation_queues.create_queue(
        name=QUEUE, score_config_ids=[config.id], description=INSTRUCTIONS
    )
    return made.id


async def _queued(api: Any, queue: str) -> set[str]:
    ids: set[str] = set()
    page = 1
    while True:
        items = await api.annotation_queues.list_queue_items(
            queue, page=page, limit=100
        )
        ids |= {i.object_id for i in items.data}
        if page >= items.meta.total_pages:
            return ids
        page += 1


async def _link(api: Any, queue: str) -> str:
    projects = (await api.projects.get()).data
    base = os.environ["LANGFUSE_BASE_URL"].rstrip("/")
    return f"{base}/project/{projects[0].id}/annotation-queues/{queue}"


async def sample(n: int, days: int, seed: int) -> str:
    api = get_client().async_api
    queue = await _queue(api)
    queued = await _queued(api, queue)
    since = datetime.now(UTC) - timedelta(days=days)
    verdicts = [
        s
        for s in await _scores(api, source="API", from_timestamp=since)
        if s.subject is not None and s.subject.id not in queued
    ]
    picked = balanced(
        [s for s in verdicts if s.value], [s for s in verdicts if not s.value], n, seed
    )
    for s in picked:
        await api.annotation_queues.create_queue_item(
            queue,
            object_id=s.subject.id,
            object_type=AnnotationQueueObjectType.OBSERVATION,
        )
    met = sum(bool(s.value) for s in picked)
    return (
        f"Added {len(picked)} of the judge's verdicts from the last {days} day(s) to "
        f"“{QUEUE}” ({met} met, {len(picked) - met} not met; {len(queued)} were there "
        f"already). Score them in Langfuse: {await _link(api, queue)}"
    )


async def _against_judge(
    api: Any, truths: list[Any], who: str
) -> tuple[Agreement, list[str]]:
    """Each labelled verdict against the judge's own score of the same observation."""
    pairs: list[tuple[bool, bool]] = []
    disagreements = []
    for truth in truths:
        if truth.subject is None:
            continue
        # Langfuse scopes an observation's id to its trace: the v3 API wants both
        judged = await _scores(
            api,
            source="API",
            observation_id=truth.subject.id,
            trace_id=truth.subject.trace_id,
        )
        if not judged:
            continue
        judge = judged[0]
        pairs.append((bool(truth.value), bool(judge.value)))
        if bool(truth.value) != bool(judge.value):
            disagreements.append(
                f"- {truth.subject.id}: {who} {'met' if truth.value else 'not met'}, "
                f"judge {'met' if judge.value else 'not met'}: {judge.comment or ''}"
                + (f" ({who}: {truth.comment})" if truth.comment else "")
            )
    return agreement(pairs), disagreements


def _rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _summary(a: Agreement) -> list[str]:
    return [
        (
            f"agreement {_rate(a.accuracy)}, TPR {_rate(a.tpr)}, TNR {_rate(a.tnr)}, "
            f"Cohen's kappa {_rate(a.kappa)}"
        ),
        f"(TP {a.tp}, FN {a.fn}, TN {a.tn}, FP {a.fp}; trusted when TPR and TNR reach {BAR})",
    ]


async def report() -> tuple[str, bool]:
    api = get_client().async_api
    queue = await _queue(api)
    people, by_people = await _against_judge(
        api, await _scores(api, source="ANNOTATION", queue_id=queue), "people"
    )
    lines = [
        f"{people.pairs} verdict(s) scored by people in “{QUEUE}”: {await _link(api, queue)}",
        *_summary(people),
        *by_people,
    ]
    if not people.pairs:
        lines.append("Nothing scored by people yet.")
    reference, by_reference = await _against_judge(
        api, await _scores(api, name=REFERENCE_SCORE, source="API"), "reference"
    )
    if reference.pairs:
        lines += [
            "",
            (
                f"{reference.pairs} verdict(s) labelled for reference, not by people "
                f"(`{REFERENCE_SCORE}`; each label's comment says who gave it and why):"
            ),
            *_summary(reference),
            *by_reference,
        ]
    # People decide; without them, the reference labels do
    return "\n".join(lines), (people.trusted if people.pairs else reference.trusted)


async def label(path: str, by: str, days: int) -> str:
    """Records reference labels, {observation id: [met, why]}, for verdicts of the last `days`
    days, as `REFERENCE_SCORE` on the same observations. Labelling again replaces them."""
    labels = json.loads(await asyncio.to_thread(Path(path).read_text))
    api = get_client().async_api
    since = datetime.now(UTC) - timedelta(days=days)
    traces = {
        s.subject.id: s.subject.trace_id
        for s in await _scores(api, source="API", from_timestamp=since)
        if s.subject is not None
    }
    client = get_client()
    missing = []
    for observation, (met, why) in labels.items():
        if observation not in traces:
            missing.append(observation)
            continue
        client.create_score(
            name=REFERENCE_SCORE,
            value=1 if met else 0,
            data_type="BOOLEAN",
            trace_id=traces[observation],
            observation_id=observation,
            score_id=f"reference-{observation}",
            comment=f"{why} (labelled by {by}, not a person)",
            metadata={"by": by},
        )
    # The SDK sends scores in the background: flush before exiting (it blocks, so off the loop)
    await asyncio.to_thread(client.flush)
    met = sum(bool(v[0]) for k, v in labels.items() if k not in missing)
    return (
        f"Recorded {len(labels) - len(missing)} reference label(s) by {by} "
        f"({met} met)"
        + (f"; no verdict found for {', '.join(missing)}" if missing else "")
    )


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.calibrate", description=__doc__
    )
    parser.add_argument(
        "--report", action="store_true", help="compare people and the judge"
    )
    parser.add_argument(
        "--sample", type=int, default=20, help="verdicts to add to the queue"
    )
    parser.add_argument(
        "--days", type=int, default=7, help="how recent the verdicts are"
    )
    parser.add_argument("--seed", type=int, default=0, help="for a repeatable sample")
    parser.add_argument(
        "--label",
        metavar="FILE",
        help="record reference labels from FILE: {observation id: [met, why]}",
    )
    parser.add_argument("--by", help="who gave the reference labels (with --label)")
    args = parser.parse_args(argv)
    if args.label:
        if not args.by:
            parser.error("--label needs --by: who gave the labels")
        print(await label(args.label, args.by, days=30))
        return 0
    if args.report:
        text, trusted = await report()
        print(text)
        return 0 if trusted else 1
    print(await sample(args.sample, args.days, args.seed))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
