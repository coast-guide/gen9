"""`make evals`: a suite run against the running stacks as the seeded user, kept in Langfuse as an
experiment (gen9-agent/README.md, "Evals").

    GEN9_CONFIG_DIR=<signed in by e2e/token.mjs> uv run --group evals \\
      --env-file langfuse.local.env --env-file models.local.env \\
      python -m evals [--suite regression] [--trials 3] [--task ID …] [--keep]

It spends model calls, so nothing runs it on its own. It replaces the seeded user's memory and
Settings > Memory switches while it runs, and puts them back at the end.
"""

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from langfuse import get_client
from langfuse.experiment import EvaluatorFunction, RunEvaluatorFunction

from .gen9 import Credentials, Gen9
from .run import Experiment, dataset, item_id, overall, summary, sync
from .tasks import SUITES


def arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m evals", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--suite", choices=sorted(SUITES), default="regression")
    parser.add_argument("--trials", type=int, default=3, help="attempts per task (k)")
    parser.add_argument("--task", action="append", help="only this task (repeatable)")
    parser.add_argument(
        "--keep",
        action="store_true",
        help="keep the chats, and their run traces, to read",
    )
    return parser.parse_args(argv)


async def version() -> str:
    """The commit the harness ran from, `-dirty` with uncommitted changes."""
    git = await asyncio.create_subprocess_exec(
        "git",
        "describe",
        "--always",
        "--dirty",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await git.communicate()
    return out.decode().strip() or "unknown"


async def main(argv: list[str]) -> int:
    args = arguments(argv)
    suite = SUITES[args.suite]
    unknown = set(args.task or ()) - {t.id for t in suite}
    if unknown:
        print(
            f"No such task in {args.suite}: {', '.join(sorted(unknown))}",
            file=sys.stderr,
        )
        return 2
    tasks = [t for t in suite if not args.task or t.id in args.task]
    config_dir = Path(os.environ["GEN9_CONFIG_DIR"])
    langfuse = get_client()

    # The seeded user's memory and switches, put back at the end
    async with Gen9(Credentials(config_dir)) as gen9:
        memory, controls = await gen9.memory(), await gen9.controls()
    # The SDK's dataset helpers are sync: in a thread
    items = await asyncio.to_thread(sync, langfuse, args.suite, suite)
    commit = await version()
    experiment = Experiment(
        langfuse=langfuse,
        suite=args.suite,
        tasks={item_id(args.suite, t): t for t in tasks},
        credentials=Credentials(config_dir),
        trials=args.trials,
        keep=args.keep,
    )
    evaluators: list[EvaluatorFunction] = [experiment.grade]
    run_evaluators: list[RunEvaluatorFunction] = [overall]
    try:
        # Langfuse's runner is sync and starts an event loop of its own: in a thread, where
        # each trial opens its clients
        result = await asyncio.to_thread(
            langfuse.run_experiment,
            name=dataset(args.suite),
            run_name=f"{args.suite} {commit} {datetime.now(UTC):%Y-%m-%d %H:%M}",
            description=f"{len(tasks)} task(s), {args.trials} trial(s) each, at {commit}",
            data=[items[i] for i in experiment.tasks],
            task=experiment.run_task,
            evaluators=evaluators,
            run_evaluators=run_evaluators,
            # Tasks share the seeded user's memory and switches: one at a time
            max_concurrency=1,
            metadata={
                "suite": args.suite,
                "trials": str(args.trials),
                "commit": commit,
                "judge": os.environ.get("GEN9_EVALS_JUDGE", "chat"),
            },
        )
    finally:
        async with Gen9(Credentials(config_dir)) as gen9:
            await gen9.set_memory(memory)
            await gen9.set_controls(controls)
        await asyncio.to_thread(langfuse.flush)
    text, ok = summary(result)
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
