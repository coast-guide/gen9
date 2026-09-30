"""A stand-in for the chat's RunWorkflow in workflow tests (test_tasks.py). In a module of its own:
Temporal's sandbox re-imports the module a workflow is defined in, so it imports only the
workflow's own input."""

from temporalio import workflow

from gen9_agent.workflows.runs import RunInput


@workflow.defn(name="RunWorkflow")
class StandInRun:
    """Records what it was asked to run."""

    @workflow.run
    async def run(self, run: RunInput) -> str:
        return f"ran {run.run_id} for {run.user_sub}"


@workflow.defn(name="RunWorkflow")
class StandInSuccess:
    """A run that succeeds, unless its id says it fails (for the grading loop's tests)."""

    @workflow.run
    async def run(self, run: RunInput) -> str:
        return "error" if run.run_id.startswith("fail") else "success"
