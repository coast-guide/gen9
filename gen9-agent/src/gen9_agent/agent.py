"""The Deep Agent behind /v1/threads, with conversation state persisted in Postgres."""

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from typing import Any

import httpx
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend
from deepagents.backends.protocol import BackendProtocol
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT, SubAgent
from langchain.agents.middleware import AgentMiddleware, TodoListMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from . import approvals, grounding, memory, plugin_skills, questions, skills, summaries
from .background import BackgroundTasks
from .connectors import ConnectorApprovals, ConnectorTools
from .definition import AgentDefinition
from .environments import EnvironmentBackend, Environments
from .langfuse_erasure import langfuse_configured
from .model_router import chat_model, served_model, web_search_tool
from .past_chats import PastChats
from .settings import DatabaseSettings, Settings
from .standing import Standing, StillActive
from .subagent_sources import SubagentSources

# Checkpoint tables live in their own schema, away from the Alembic-managed app tables
CHECKPOINT_SCHEMA = "langgraph"


def checkpoint_pool(settings: DatabaseSettings) -> AsyncConnectionPool:
    """psycopg pool configured as AsyncPostgresSaver requires (autocommit, dict rows, no prepared statements)."""
    url = settings.database_url.set(drivername="postgresql").render_as_string(
        hide_password=False
    )
    return AsyncConnectionPool(
        conninfo=url,
        min_size=1,
        max_size=5,
        open=False,
        # A connection is checked before a run gets it, so a Postgres restart costs no run attempt
        # (seen: "AdminShutdown: terminating connection" on the first run after one)
        check=AsyncConnectionPool.check_connection,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": f"-c search_path={CHECKPOINT_SCHEMA}",
        },
    )


# The compiled agent; each run passes whose it is as its context (memory.py)
Agent = CompiledStateGraph[Any, memory.Gen9Context, Any, Any]
# The plan the user sees while the agent works (Deep Agents leaves it out by default)
MIDDLEWARE: list[AgentMiddleware[Any, Any]] = [TodoListMiddleware()]


def subagents(
    definition: AgentDefinition,
    model_for: Callable[[str], BaseChatModel],
    summarize: Callable[[BaseChatModel | None], AgentMiddleware] | None = None,
    guard: AgentMiddleware | None = None,
) -> list[SubAgent]:
    """The subagents the agent can hand tasks to: its declared ones, each with its own
    instructions (and model, if it names one), and Deep Agents' general-purpose one.

    Subagents don't inherit the main agent's middleware, so each gets the grounding here, and
    `summarize(its model, or None for the agent's)` when given (summaries.py), and `guard`
    (standing.StillActive) when given. The
    general-purpose one is declared for that, as Deep Agents' docs describe replacing it, with the
    main agent's skills as Deep Agents gives it. A definition may declare its own."""

    def middleware(model: BaseChatModel | None) -> list[AgentMiddleware]:
        own = [summarize(model)] if summarize else []
        return [
            *grounding.middleware(),
            memory.MemoryRules(),
            # What its searches find, kept for its `task` step (subagent_sources.py)
            SubagentSources(),
            *own,
            *([guard] if guard else []),
        ]

    specs: list[SubAgent] = []
    for sub in definition.subagents:
        model = model_for(sub.model) if sub.model else None
        specs.append(
            {
                "name": sub.name,
                "description": sub.description,
                "system_prompt": sub.instructions,
                "middleware": middleware(model),
                **({"model": model} if model else {}),
            }
        )
    if all(spec["name"] != GENERAL_PURPOSE_SUBAGENT["name"] for spec in specs):
        general: SubAgent = {
            **GENERAL_PURPOSE_SUBAGENT,
            "skills": SKILL_SOURCES,
            "middleware": middleware(None),
        }
        specs.insert(0, general)
    return specs


# Where the agent's skills come from: the person's plugins' (plugin_skills.py), then its own, so
# its own win on a shared name (later sources win in Deep Agents)
SKILL_SOURCES = [plugin_skills.ROUTE, skills.ROUTE]


# What the agent is told about the chat's environment when it has one (environments.py,
# chat_files.py)
ENVIRONMENT_NOTE = """

## Your environment

This chat has a Linux machine of its own (Python 3.12, no internet unless the person set up
access): `execute` runs commands there, and your files live there. It lasts while the chat uses it;
after half an hour unused, a later command starts a fresh one.
- To give the person a file (a spreadsheet, a chart, a report), save it in /work/out: it appears
  under your answer for them to download.
- Files the person attaches are in /work/in.
- Skills' files (/skills/, /plugins/) aren't on that machine. To run a script a skill comes with,
  read it, then run it with its code in the command (`python3 - <<'EOF'` … `EOF`), so what runs
  is what the person sees when they're asked to allow it."""

# Where Deep Agents puts what it offloads from the conversation (large tool results): in the
# chat's state, so a chat that never runs code never gets an environment for them
ARTIFACTS_ROUTE = "/gen9/"


def backend(
    skills_backend: BackendProtocol,
    plugin_skills_backend: BackendProtocol,
    environments: Environments | None,
) -> CompositeBackend:
    """The agent's files: memory and skills on their routes; everything else in the chat's
    environment when environments are on (environments.py; the agent then gets `execute`), or
    in the chat's state."""
    routes: dict[str, BackendProtocol] = {
        memory.ROUTE: memory.store_backend(),
        skills.ROUTE: skills_backend,
        plugin_skills.ROUTE: plugin_skills_backend,
    }
    if environments is None:
        return CompositeBackend(default=StateBackend(), routes=routes)
    return CompositeBackend(
        default=EnvironmentBackend(environments),
        routes={**routes, ARTIFACTS_ROUTE: StateBackend()},
        artifacts_root=ARTIFACTS_ROUTE.rstrip("/"),
    )


def build_agent(
    settings: Settings,
    checkpointer: AsyncPostgresSaver,
    models: httpx.AsyncClient,
    store: BaseStore,
    definition: AgentDefinition,
    skills_backend: BackendProtocol,
    plugin_skills_backend: BackendProtocol,
    connectors: ConnectorTools | None = None,
    environments: Environments | None = None,
    background: BackgroundTasks | None = None,
    past_chats: PastChats | None = None,
    standing: Standing | None = None,
) -> Agent:
    """The agent its definition describes (definition.py): instructions, model, skills and
    subagents, with Gen9's tools, memory and storage around it."""
    # Today's date on every call, and a bound on searches per turn (grounding.py)
    middleware: list[AgentMiddleware[Any, Any]] = [
        *MIDDLEWARE,
        *grounding.middleware(),
        # The person's memory as it is now, each run, not as the chat first read it (memory.py)
        memory.FreshMemory(),
        # What may be remembered, and nothing while the person has memory off (memory.py)
        memory.MemoryRules(),
        # A subagent's pages on its `task` step, so the answer shows them (subagent_sources.py)
        SubagentSources(),
    ]
    if connectors:
        # Each person's connectors, added per run, and approvals for their calls (connectors.py)
        middleware += [connectors, ConnectorApprovals(connectors)]
    if background:
        # Work the agent starts in the background, each task a chat of its own (background.py)
        middleware.append(background)
    if past_chats:
        # The person's past chats, searched when they refer to one (past_chats.py)
        middleware.append(past_chats)
    # A person disabled or deleted mid-turn stops within a minute (standing.py)
    guard = StillActive(standing) if standing else None
    if guard:
        middleware.append(guard)
    # Through gen9-models, by alias (model_router.py); AGENT_MODEL overrides the definition's
    model = chat_model(
        settings,
        models,
        settings.agent_model or definition.model,
        response_headers=langfuse_configured(),
    )
    files = backend(skills_backend, plugin_skills_backend, environments)
    # A summary that doesn't turn what a page or tool asked into the person's request, in place
    # of Deep Agents' own (summaries.py)
    middleware.append(summaries.summarization(model, files))
    agent = create_deep_agent(
        model=model,
        # The model's built-in web search (OpenAI's Responses API, passed through by the router),
        # or the router's search API for any model (WEB_SEARCH); and questions to the person,
        # which pause the run until they answer (questions.py)
        tools=[
            {"type": "web_search"}
            if settings.web_search == "model"
            else web_search_tool(settings, models),
            questions.ask_user,
        ],
        system_prompt=definition.instructions
        + (ENVIRONMENT_NOTE if environments else ""),
        subagents=subagents(
            definition,
            lambda alias: chat_model(
                settings, models, alias, response_headers=langfuse_configured()
            ),
            lambda sub_model: summaries.summarization(sub_model or model, files),
            guard,
        ),
        middleware=middleware,
        checkpointer=checkpointer,
        # Each person's memory, loaded into every prompt and edited by the agent (memory.py), and
        # the definition's skills, read when a task matches (skills.py)
        memory=[memory.MEMORY_PATH],
        skills=SKILL_SOURCES,
        backend=files,
        permissions=memory.PERMISSIONS + skills.PERMISSIONS + plugin_skills.PERMISSIONS,
        # In a chat set to "Ask before acting", memory writes wait for Allow or Deny; the run's
        # context says which mode (approvals.py). Declared subagents inherit it
        interrupt_on=approvals.INTERRUPT_ON,
        store=store,
        context_schema=memory.Gen9Context,
        name=definition.name,
    )
    if background:
        # Its tasks' answers are read from their chats' state, through the agent's graph
        background.agent = agent
    return agent


def tracing_attributes(user_sub: str, session_id: str) -> AbstractContextManager:
    """The user and chat on every observation of the run, not only its root: Langfuse v4 keeps
    them per observation and propagates them to those created inside `propagate_attributes`
    (langfuse.com/docs/observability/sdk/upgrade-path/python-v3-to-v4)."""
    if not langfuse_configured():
        return nullcontext()
    from langfuse import propagate_attributes

    return propagate_attributes(user_id=user_sub, session_id=session_id)


def name_served_model(response: LLMResult) -> None:
    """Put the model that served a call where Langfuse's handler reads it (`llm_output`), and drop
    the router's headers from the answer, so they don't reach the checkpoints."""
    generation = response.generations[-1][-1] if response.generations else None
    message = getattr(generation, "message", None)
    if not isinstance(message, BaseMessage):
        return
    served = served_model(message)
    if served:
        response.llm_output = {**(response.llm_output or {}), "model_name": served}
    message.response_metadata.pop("headers", None)


def langfuse_callbacks() -> list:
    """Trace to Langfuse when it is configured; otherwise no callbacks."""
    if not langfuse_configured():
        return []
    from langfuse.langchain import CallbackHandler

    class RoutedModelHandler(CallbackHandler):
        """Names the model that served each call, not the alias it was asked by, so Langfuse
        prices it from its own table (`model_router.served_model`: the router's header, else the
        body). Langfuse's handler reads only `llm_output`, which streaming leaves empty. The
        router's headers were asked for this alone (`chat_model(response_headers=…)`), so they
        are dropped here, before the answer reaches the checkpoints."""

        def on_llm_end(self, response: LLMResult, **kwargs: Any) -> Any:
            name_served_model(response)
            return super().on_llm_end(response, **kwargs)

    return [RoutedModelHandler()]
