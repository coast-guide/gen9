"""What both processes need, built in one place: the API (`app.py`) and workers (`worker.py`)."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from . import plugin_skills, skills
from .agent import Agent, build_agent, checkpoint_pool
from .background import BackgroundTasks
from .connector_net import reach
from .connectors import ConnectorTools
from .db import create_engine, create_sessionmaker
from .definition import GEN9, AgentDefinition
from .definition import load as load_definition
from .environments import Environments
from .model_router import http_client
from .past_chats import PastChats
from .settings import Settings
from .standing import Standing
from .vault import Vault


@dataclass(frozen=True)
class Runtime:
    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    checkpointer: AsyncPostgresSaver
    agent: Agent
    # The HTTP client for every call to the model router (model_router.py)
    models: httpx.AsyncClient
    # The LangGraph store: each person's memory (memory.py), beside the checkpoints
    store: AsyncPostgresStore
    # What the agent is (definition.py): its version is recorded on every run
    definition: AgentDefinition
    # Seals and opens secrets people give Gen9 (vault.py); None without GEN9_SECRET_KEYS
    vault: Vault | None
    # People's connectors (connectors.py): the agent's tools, and what their apps ask the API
    connectors: ConnectorTools
    # Chats' environments (environments.py); None without SANDBOX_URL. The worker gives it its
    # Temporal client
    environments: Environments | None = None
    # Work the agent starts in the background (background.py); the worker gives it its Temporal
    # client
    background: BackgroundTasks | None = None
    # Whether work may still be done for a person (standing.py); the API and the worker give it
    # their Keycloak admin client
    standing: Standing = field(default_factory=Standing)


@asynccontextmanager
async def open_runtime(settings: Settings) -> AsyncIterator[Runtime]:
    engine = create_engine(settings)
    pool = checkpoint_pool(settings)
    await pool.open(wait=True)
    models = http_client()
    try:
        checkpointer = AsyncPostgresSaver(pool)  # ty: ignore[invalid-argument-type]
        store = AsyncPostgresStore(pool)  # ty: ignore[invalid-argument-type]
        # Reading the agent's folder and resolving its skills touch the disk: in threads
        definition = await asyncio.to_thread(load_definition, GEN9)
        skills_backend = await asyncio.to_thread(skills.backend, definition.skills_dir)
        vault = (
            Vault(settings.gen9_secret_keys.get_secret_value())
            if settings.gen9_secret_keys
            else None
        )
        connectors = ConnectorTools(engine, vault, reach(settings))
        environments = Environments(settings) if settings.sandbox_url else None
        background = BackgroundTasks(engine, settings.background_tasks_per_chat)
        sessionmaker = create_sessionmaker(engine)
        # One for the runtime and the agent's check before each model call: the API and the
        # worker give it their Keycloak admin client once started
        standing = Standing()
        yield Runtime(
            settings=settings,
            engine=engine,
            sessionmaker=sessionmaker,
            checkpointer=checkpointer,
            agent=build_agent(
                settings,
                checkpointer,
                models,
                store,
                definition,
                skills_backend,
                # The person's plugins' skills, read from the database when used
                plugin_skills.PluginSkillsBackend(plugin_skills.contents(engine)),
                connectors,
                environments,
                background,
                PastChats(sessionmaker, settings, models),
                standing,
            ),
            standing=standing,
            models=models,
            store=store,
            definition=definition,
            vault=vault,
            connectors=connectors,
            environments=environments,
            background=background,
        )
    finally:
        await models.aclose()
        await pool.close()
        await engine.dispose()
