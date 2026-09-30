"""Probe: Langfuse's experiment runner (SDK 4.15) on Gen9's self-hosted Langfuse, with an async task,
a code grader, openevals' LLM-as-judge on the router's `chat` model, and a run-level evaluator.

    uv run --env-file .env --env-file postgres.local.env --env-file keycloak.local.env \\
      --env-file models.local.env --env-file langfuse.local.env python explore/evals/probe.py
"""

from langfuse import Evaluation, get_client
from openevals.llm import create_async_llm_as_judge

from gen9_agent.model_router import chat_model, http_client
from gen9_agent.settings import get_settings

settings = get_settings()
judge_model = chat_model(settings, http_client(), "chat")
judge = create_async_llm_as_judge(
    prompt="Does the answer satisfy this: {criteria}\n\nAnswer: {outputs}",
    judge=judge_model,
    feedback_key="judge",
)


async def task(*, item, **kwargs):
    # Stands in for a Gen9 run: the answer the task would get
    return item["input"]["pretend_answer"]


def contains(*, output, expected_output, **kwargs):
    ok = expected_output.lower() in output.lower()
    return Evaluation(name="contains", value=1.0 if ok else 0.0)


async def judged(*, output, metadata, **kwargs):
    verdict = await judge(outputs=output, criteria=metadata["criteria"])
    return Evaluation(
        name="judge",
        value=1.0 if verdict["score"] else 0.0,
        comment=verdict.get("comment"),
    )


def pass_rate(*, item_results, **kwargs):
    ok = [e.value for r in item_results for e in r.evaluations if e.name == "contains"]
    return Evaluation(name="pass_rate", value=sum(ok) / len(ok) if ok else None)


data = [
    {
        "input": {"pretend_answer": "Canberra is Australia's capital."},
        "expected_output": "Canberra",
        "metadata": {"criteria": "names Australia's capital correctly"},
    },
    {
        "input": {"pretend_answer": "Sydney."},
        "expected_output": "Canberra",
        "metadata": {"criteria": "names Australia's capital correctly"},
    },
]

langfuse = get_client()
result = langfuse.run_experiment(
    name="gen9-evals-probe",
    description="Probe of the runner",
    data=data,
    task=task,
    evaluators=[contains, judged],
    run_evaluators=[pass_rate],
)
print(result.format())
print(
    "run:",
    getattr(result, "dataset_run_id", None),
    getattr(result, "dataset_run_url", None),
)
langfuse.flush()
