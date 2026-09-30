"""A chat's summary keeps what a page or tool asked as what it said, never as the person's request
(summaries.py; P5-C6)."""

from pathlib import Path

import deepagents.graph
import deepagents.middleware.subagents
import pytest
from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.middleware.summarization import _DeepAgentsSummarizationMiddleware
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from gen9_agent import summaries
from gen9_agent.agent import subagents
from gen9_agent.definition import AgentDefinition, SubAgentSpec

pytestmark = pytest.mark.asyncio


def fake() -> GenericFakeChatModel:
    return GenericFakeChatModel(messages=iter([AIMessage("ok")]))


def prompt_of(middleware) -> str:
    return middleware._lc_helper.summary_prompt


async def test_the_prompt_says_who_asks_just_before_the_messages() -> None:
    before, _, after = summaries.PROMPT.partition(summaries.SOURCES)
    assert "## SESSION INTENT" in before and "<media_reference_information>" in before
    assert after.startswith("\n\n<messages>\n") and after.count("{messages}") == 1


async def test_the_agent_and_its_subagents_summarize_with_gen9s_prompt(
    monkeypatch,
) -> None:
    graphs = []
    real = deepagents.graph.create_agent

    def spy(*args, **kwargs):
        graphs.append(kwargs.get("middleware") or [])
        return real(*args, **kwargs)

    # The agent's graph, and its subagents' (made by the subagent middleware)
    monkeypatch.setattr(deepagents.graph, "create_agent", spy)
    monkeypatch.setattr(deepagents.middleware.subagents, "create_agent", spy)
    backend = StateBackend()
    model = fake()
    researcher = SubAgentSpec(
        name="researcher",
        description="Researches.",
        instructions="Research.",
        model=None,
    )
    definition = AgentDefinition(
        name="gen9",
        description="",
        model="chat",
        instructions="",
        skills_dir=Path("."),
        skills=(),
        subagents=(researcher,),
        version="0",
    )
    specs = subagents(
        definition,
        lambda _: fake(),
        lambda m: summaries.summarization(m or model, backend),
    )
    create_deep_agent(
        model=model,
        middleware=[summaries.summarization(model, backend)],
        subagents=specs,
    )
    # The agent, the general-purpose subagent and the researcher (Deep Agents builds each
    # subagent's graph twice): each has one summarization, Gen9's
    assert len(graphs) >= 3
    for middleware in graphs:
        found = [
            m for m in middleware if isinstance(m, _DeepAgentsSummarizationMiddleware)
        ]
        assert [prompt_of(m) == summaries.PROMPT for m in found] == [True]
