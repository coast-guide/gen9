"""Relational schema owned by gen9-agent (migrated by Alembic, schema `public`).

Agent conversation state lives in LangGraph's checkpoint tables in schema `langgraph`,
created by the checkpointer's own migrations (see migrate.py).
"""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Deterministic constraint names, so Alembic migrations are reproducible
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class User(Base):
    """A Keycloak user, provisioned just-in-time from access-token claims.

    Keycloak is the source of truth for identity; this row only anchors app data to `sub`.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=func.gen_random_uuid()
    )
    sub: Mapped[str] = mapped_column(
        String(255), unique=True, comment="Keycloak subject (user id)"
    )
    email: Mapped[str | None] = mapped_column(String(320))
    name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Which emails a person gets about background runs (notices.py): all (done, needs you, didn't
    # finish), needs_you, or never
    notify: Mapped[str] = mapped_column(
        String(16), server_default="all", comment="all, needs_you or never"
    )
    # Whether a chat's agent may search and cite the person's past chats (past_chats.py)
    search_past_chats: Mapped[bool] = mapped_column(Boolean, server_default=true())
    # Whether Gen9 remembers things about them: off, memory is neither loaded nor written (memory.py)
    remember: Mapped[bool] = mapped_column(Boolean, server_default=true())

    threads: Mapped[list["Thread"]] = relationship(
        back_populates="user", passive_deletes=True
    )


class Thread(Base):
    """A conversation with the agent. Its messages live in the LangGraph checkpoint (thread_id = id)."""

    __tablename__ = "threads"
    __table_args__ = (Index("ix_threads_user_id_updated_at", "user_id", "updated_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(Text, server_default="New chat")
    # "ask" (Ask before acting) or "auto" (Act, ask when unsure): set by the chat's last
    # message, carried by each run (approvals.py)
    permission_mode: Mapped[str] = mapped_column(String(8), server_default="auto")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Set when deletion starts (DeleteThreadWorkflow): the chat is gone for its owner at once,
    # while the workflow erases its traces, history and row
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The scheduled task that made it (tasks.py), if one did
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), index=True
    )
    # The chat whose agent started it as a background task (background.py), if one did: it
    # runs in that chat's environment, and isn't listed among the person's chats. Deleting that
    # chat deletes this one through a deletion of its own (api/threads.py)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("threads.id", ondelete="SET NULL"), index=True
    )
    # The A2A client (its token's `azp`) that started it, if one did: that client reaches only
    # the chats it started (a2a_server.py)
    a2a_client: Mapped[str | None] = mapped_column(String(255))

    user: Mapped[User] = relationship(back_populates="threads")


# A run is active while queued, running or waiting for the person (a question, an approval); a
# thread has at most one active run. `expired`: it waited for the person until its timeout.
ACTIVE_RUN_STATUSES = ("queued", "running", "waiting")
FINAL_RUN_STATUSES = ("success", "error", "cancelled", "expired")


class Run(Base):
    """One turn of a thread: the user's input, executed by a worker outside any HTTP request.

    Temporal executes it (`RunWorkflow`, workflow ID `run-<id>`); this row is what people see of
    it (`runs/store.py`). A run whose worker died is retried on another worker and resumes from its
    checkpoint; `attempts` counts the tries.
    """

    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_thread_id_created_at", "thread_id", "created_at"),
        Index(
            "uq_runs_one_active_per_thread",
            "thread_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running', 'waiting')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=func.gen_random_uuid()
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(
        String(16),
        server_default="queued",
        comment="queued, running, waiting, success, error, cancelled or expired",
    )
    input: Mapped[dict[str, Any]] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    # The version of the agent's definition that answered (definition.py): a hash of its folder
    agent_version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunEvent(Base):
    """What happened during a run, in order: the log clients stream and replay (`runs/events.py`)."""

    __tablename__ = "run_events"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class InputRequest(Base):
    """Something a run asked the person for mid-task (a LangGraph interrupt), and their response.

    The worker records it when a turn pauses (`runs/store.py`, `wait`); the API stores the response,
    first one wins, before telling the run's workflow (`runs/control.py`); the next turn resumes
    with it. `id` is the interrupt's id. Questions today (questions.py); approvals next.
    """

    __tablename__ = "run_inputs"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), comment="question")
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Connector(Base):
    """A remote MCP server a person connected (connectors.py): its tools join their chats. The
    token, if any, is sealed (vault.py) and bound to its owner and this row."""

    __tablename__ = "connectors"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_connectors_user_name"),
        UniqueConstraint(
            "user_id", "plugin_id", "plugin_server", name="uq_connectors_user_plugin"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Shown to the person and the prefix of its tools' names: lowercase letters, digits, hyphens
    name: Mapped[str] = mapped_column(String(32))
    url: Mapped[str] = mapped_column(Text)
    # A header sent with each call, and its sealed value (a token), if any
    header: Mapped[str | None] = mapped_column(String(64))
    sealed_token: Mapped[str | None] = mapped_column(Text)
    policy: Mapped[str] = mapped_column(
        String(16), server_default="ask", comment="ask, changes or never"
    )
    # Its tools as the person kept them, when they connected it or last looked: name, description,
    # read_only and `pin` (connectors.py). Runs list them live and use only these, unchanged
    tools: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    # Tools the server added or changed since, held back until the person looks: name,
    # description, was (the description before, or null for a new one)
    changed: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    # Whether its tools can be used: ready, sign_in (waits for the person to sign in) or reconnect
    # (its sign-in lapsed)
    status: Mapped[str] = mapped_column(
        String(16), server_default="ready", comment="ready, sign_in or reconnect"
    )
    # For a server that needs sign-in (connector_auth.py): what its authorization server said and
    # Gen9's client id, none of it secret; the client secret and the tokens are sealed
    sign_in: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    sealed_client_secret: Mapped[str | None] = mapped_column(Text)
    sealed_tokens: Mapped[str | None] = mapped_column(Text)
    # Brought by a plugin the person has (plugin_connectors.py): which one, and which of its
    # servers. It goes with the plugin
    plugin_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("plugins.id", ondelete="CASCADE"), index=True
    )
    plugin_server: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ChatFile(Base):
    """A file of a chat (chat_files.py): one its environment shared (`/work/out`, after a turn) or
    one the person attached. Gen9 keeps it, so it outlives the environment; it goes with the chat
    or the account."""

    __tablename__ = "chat_files"
    __table_args__ = (
        UniqueConstraint(
            "thread_id", "origin", "path", name="uq_chat_files_thread_path"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=func.gen_random_uuid()
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE"), index=True
    )
    # The run that last shared it (its answer lists it)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL")
    )
    # output: the environment shared it; upload: the person attached it
    origin: Mapped[str] = mapped_column(String(8), comment="output or upload")
    # Where it is in the environment, and its name as shown
    path: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(String(128))
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    # When the environment last changed it: a capture skips a file it already holds
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EnvironmentSecret(Base):
    """A person's secret for a host, which OpenSandbox's credential vault adds to requests from
    their chats' environments to it (environments.py): code there never sees it. The value is
    sealed (vault.py), bound to its owner and this row, and never returned."""

    __tablename__ = "environment_secrets"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_environment_secrets_user_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Shown to the person, and its name in the vault: lowercase letters, digits, hyphens
    name: Mapped[str] = mapped_column(String(32))
    # Where it's sent: an https host (a leading *. for its subdomains) and a path pattern
    host: Mapped[str] = mapped_column(String(253))
    path: Mapped[str] = mapped_column(String(256), server_default="/*")
    # How: Authorization Bearer, a named header, or Basic (the value is user:password)
    auth: Mapped[str] = mapped_column(String(16), comment="bearer, header or basic")
    header: Mapped[str | None] = mapped_column(String(64))
    # Which requests it goes with: "read" (GET, HEAD, OPTIONS) or "all" (environments.METHODS)
    methods: Mapped[str] = mapped_column(
        String(8), server_default="read", comment="read or all"
    )
    sealed_value: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ConnectorSignIn(Base):
    """A sign-in under way (connector_auth.py), keyed by the hash of its `state`: the PKCE
    verifier and the redirect URI, sealed. Used once, and ignored after ten minutes."""

    __tablename__ = "connector_sign_ins"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    connector_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("connectors.id", ondelete="CASCADE"), index=True
    )
    sealed: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ChatSearch(Base):
    """A finished run's question and answer, searchable (`api/search.py`): BM25 on `body`
    (pg_textsearch), vector on `embedding`, filled after the text through the model router
    (`runs/indexing.py`). Goes with its run, thread and user. `embedding` is an untyped vector,
    so any embedding model fits; each model has its own HNSW index (`search_index.py`). Indexes
    are in migrations and there: BM25 and HNSW need options SQLAlchemy doesn't model."""

    __tablename__ = "chat_search"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    body: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector())
    # The model that made `embedding` (as the router named it), for re-embedding after a change
    embed_model: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RegistryServer(Base):
    """A server in Gen9's copy of an MCP registry (directory.py): one a connector can use, as the
    registry described it. Not reviewed by Gen9."""

    __tablename__ = "registry_servers"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    # The one header the person gives, sent with each call, if the server needs one
    header: Mapped[str | None] = mapped_column(Text)
    header_description: Mapped[str | None] = mapped_column(Text)
    repository_url: Mapped[str | None] = mapped_column(Text)
    website_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), comment="active or deprecated")
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RegistrySync(Base):
    """How far Gen9's copy of the registry got: when the last finished pass began (less a little),
    the `updated_since` of the next one, and a pass under way's cursor and start, so any later
    attempt or workflow resumes it."""

    __tablename__ = "registry_sync"

    registry_url: Mapped[str] = mapped_column(Text, primary_key=True)
    synced_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cursor: Mapped[str | None] = mapped_column(Text)
    pass_started: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PluginSource(Base):
    """A git repository holding a plugin marketplace, added by an admin (plugin_sources.py): in
    Codex's format or Claude Code's. The worker syncs it; its plugins go with it."""

    __tablename__ = "plugin_sources"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=func.gen_random_uuid()
    )
    url: Mapped[str] = mapped_column(Text, unique=True)
    # A branch or tag; none: the repository's default branch
    ref: Mapped[str | None] = mapped_column(Text)
    # From its marketplace file, once synced
    name: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    format: Mapped[str | None] = mapped_column(String(16), comment="codex or claude")
    # The commit last synced
    commit: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(
        String(16), server_default="pending", comment="pending, synced or failed"
    )
    # Why the last sync failed, in words; what it had synced before stays
    error: Mapped[str | None] = mapped_column(Text)
    # The last sync, whatever its outcome ("Sync now" waits for it to change)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The last sync that succeeded: what a failed source still offers dates from it
    succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    added_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Plugin(Base):
    """A plugin a source lists (plugins.py loads it): what it brings, what was skipped and why,
    and whether people may have it, as an admin chose."""

    __tablename__ = "plugins"
    __table_args__ = (
        UniqueConstraint("source_id", "name", name="uq_plugins_source_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=func.gen_random_uuid()
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plugin_sources.id", ondelete="CASCADE"), index=True
    )
    # Its name in the marketplace
    name: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    version: Mapped[str | None] = mapped_column(Text)
    # agent-plugins, codex or claude, once loaded
    format: Mapped[str | None] = mapped_column(String(16))
    # loaded; rejected (not a plugin Gen9 can load); unsupported (a source Gen9 doesn't fetch);
    # failed (fetching it failed)
    status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(Text)
    # off (nobody), available (people install it) or installed (everyone has it)
    availability: Mapped[str] = mapped_column(
        String(16), server_default="off", comment="off, available or installed"
    )
    # Where the marketplace says it is, as written
    source: Mapped[dict[str, Any] | str] = mapped_column(JSONB)
    # The commit its files came from
    commit: Mapped[str | None] = mapped_column(String(40))
    # plugins.report(): manifest, skills, MCP servers, what was skipped and reported, notes
    report: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    # SHA-256 over its kept files: a sync that changes nothing leaves them
    digest: Mapped[str | None] = mapped_column(String(64))
    # What an admin agreed to when they last chose who may have it (plugin_skills.fingerprint):
    # it reaches people only while it still is that
    reviewed: Mapped[str | None] = mapped_column(String(64))
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PluginFile(Base):
    """A file of a plugin's skills, kept so chats can read them (a SKILL.md and what it refers
    to), bounded in size (plugin_sources.py)."""

    __tablename__ = "plugin_files"

    plugin_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plugins.id", ondelete="CASCADE"), primary_key=True
    )
    # Relative to the plugin's root, as the report's skill paths are
    path: Mapped[str] = mapped_column(Text, primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary)


class PluginInstall(Base):
    """A person added a plugin an admin made available (plugin_skills.py): its skills join their
    chats. It goes with the person or the plugin."""

    __tablename__ = "plugin_installs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    plugin_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plugins.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Task(Base):
    """A task Gen9 runs on its own (tasks.py): a saved message, when to send it, and the
    permission mode its runs keep. Each firing makes a new chat. Its Temporal Schedule (or a
    delayed start, for a one-off) goes with it; the chats it made stay."""

    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(Text)
    prompt: Mapped[str] = mapped_column(Text)
    # {"kind": once | hourly | daily | weekdays | weekly, "time": "HH:MM", "weekday": 0-6
    # (Monday first), "date": "YYYY-MM-DD" for once}
    schedule: Mapped[dict[str, Any]] = mapped_column(JSONB)
    time_zone: Mapped[str] = mapped_column(Text)
    permission_mode: Mapped[str] = mapped_column(String(8), server_default="auto")
    status: Mapped[str] = mapped_column(
        String(8), server_default="active", comment="active, paused or done"
    )
    # What done looks like (outcomes.py): a Markdown rubric of explicit criteria, graded after
    # each run; none: runs aren't graded. And how many runs a firing may take to meet it
    rubric: Mapped[str | None] = mapped_column(Text)
    max_iterations: Mapped[int] = mapped_column(Integer, server_default="3")
    # Its API trigger (api/tasks.py): the SHA-256 of its token (the token itself is shown once),
    # and when it was made
    trigger_hash: Mapped[str | None] = mapped_column(String(64))
    trigger_made_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TaskFire(Base):
    """A task fired outside its schedule (its API trigger, or Run now), recorded in the request
    that fired it (api/tasks.py): the hourly limits count these, so a burst of calls can't slip
    past them before the worker makes their chats."""

    __tablename__ = "task_fires"
    __table_args__ = (Index("ix_task_fires_task_at", "task_id", "at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=func.gen_random_uuid()
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RunNotice(Base):
    """A notice about a background run (notices.py): one per run and kind, so a retried Activity
    sends it once. `sent_at` is set once the email went."""

    __tablename__ = "run_notices"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(
        String(16), primary_key=True, comment="done, waiting, failed or unmet"
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutcomeEvaluation(Base):
    """A grader's verdict on one run of a task with a rubric (outcomes.py): each criterion met or
    not and why, and the result. `iteration` counts the firing's tries from 0."""

    __tablename__ = "outcome_evaluations"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    iteration: Mapped[int] = mapped_column(Integer)
    result: Mapped[str] = mapped_column(
        String(24), comment="satisfied, needs_revision or failed"
    )
    explanation: Mapped[str] = mapped_column(Text)
    criteria: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AuditEvent(Base):
    """Who did what (audit.py): admin actions, people's security actions, and access refused.
    Append-only: a trigger refuses to change or delete a row. It names people by `sub` alone and
    holds no secrets, so it outlives an account's deletion."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    actor: Mapped[str] = mapped_column(
        String(255),
        index=True,
        comment="The person's sub, or who else acted (gen9-agent)",
    )
    action: Mapped[str] = mapped_column(String(64), index=True)
    outcome: Mapped[str] = mapped_column(String(16), comment="success or denied")
    target: Mapped[str | None] = mapped_column(String(255))
    where: Mapped[str | None] = mapped_column(
        String(255),
        comment="The route, as its template: PATCH /v1/admin/users/{user_id}",
    )
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
