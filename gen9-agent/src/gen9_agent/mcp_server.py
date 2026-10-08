"""Gen9 as an MCP server (docs/plans/harness.md, milestone 6; Decision Log): other
agents and apps (Claude, VS Code, Codex, …) ask Gen9 and read the person's chats, at `/mcp` in the
agent API.

- **Protocol.** FastMCP 4, stateless HTTP: MCP 2026-07-28, and the handshake of the versions
  before it for the clients that still use it.
- **Who.** Tokens from Gen9's Keycloak whose audience is this server's URL (`GEN9_MCP_URL`, from
  the `gen9-mcp` client scope's Audience mapper: Keycloak ignores RFC 8707's `resource`). The
  Protected Resource Metadata (RFC 9728) names Keycloak, and a call without a valid token gets
  `401` with its address. MCP clients sign in as the pre-registered public client `gen9-mcp`
  (authorization code, PKCE, loopback redirects). Tokens for the API itself don't work here, and
  this server's tokens don't work on the API.
- **What.** Each tool acts as the token's person, on Gen9's own chats, runs and search:
  - `ask`: a message in a new chat or one of theirs. It waits up to `MCP_ASK_WAIT_S` for the
    answer, then says how the run stands; `read_chat` has the rest. A client that declares MCP
    Tasks (`io.modelcontextprotocol/tasks`) gets a task instead, and follows the run to its end,
    answering what it asks on the way (`mcp_tasks.py`).
  - `read_chat`, `list_chats`, `search_chats`.
  Every result is structured, and always carries the chat's id (Codex's MCP server lost it, so
  its clients couldn't reply).
"""

import asyncio
import logging
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import FastAPI, HTTPException
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.http import StarletteWithLifespan
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS
from pydantic import AnyHttpUrl, BaseModel, Field, ValidationError
from sqlalchemy import func, select

from .api.search import Mode, find
from .api.threads import conversation
from .mcp_tasks import Gen9Tasks
from .mcp_tasks import Run as TaskRun
from .models import FINAL_RUN_STATUSES, InputRequest, Run, RunEvent, Thread, User
from .not_blank import MessageText
from .runs import control, store
from .settings import Settings
from .users import upsert_user

log = logging.getLogger(__name__)

SCOPE = "gen9-mcp"
# How much of a chat `read_chat` returns: its latest messages, each cut short
LATEST_MESSAGES = 20
MESSAGE_CHARS = 8000
Said = Literal["working", "needs_you", "done", "didnt_finish", "stopped"]
# A run's status, in the words the tools report
SAID: dict[str, Said] = {
    "queued": "working",
    "running": "working",
    "waiting": "needs_you",
    "success": "done",
    "error": "didnt_finish",
    "expired": "didnt_finish",
    "cancelled": "stopped",
}


class Answer(BaseModel):
    chat_id: UUID = Field(description="Pass it to `ask` to continue this chat")
    url: str = Field(description="The chat in Gen9's web app")
    status: Said = Field(
        description="done; working (read_chat later); needs_you (the person must answer "
        "in Gen9, at url); didnt_finish; stopped"
    )
    answer: str | None = None


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class Chat(BaseModel):
    chat_id: UUID
    title: str
    url: str
    status: Said | None = Field(description="Its latest run's")
    messages: list[Message] = Field(description="The latest, oldest first")


class ChatSummary(BaseModel):
    chat_id: UUID
    title: str
    updated_at: datetime
    status: Said | None


class Hit(BaseModel):
    chat_id: UUID
    title: str
    snippet: str | None
    created_at: datetime


def auth(settings: Settings) -> RemoteAuthProvider:
    """Tokens from Gen9's Keycloak for this server (its keys from Keycloak's internal address,
    its issuer as the tokens carry it)."""
    return RemoteAuthProvider(
        token_verifier=JWTVerifier(
            jwks_uri=settings.jwks_url,
            issuer=settings.keycloak_issuer,
            audience=settings.gen9_mcp_url,
            algorithm="RS256",
            required_scopes=[SCOPE],
        ),
        authorization_servers=[AnyHttpUrl(settings.keycloak_issuer)],
        base_url=settings.gen9_api_public_url.rstrip("/"),
        resource_name="Gen9",
    )


def origins(settings: Settings) -> list[str]:
    """The browser origins `/mcp` takes: the API's own, as callers reach it, and the operator's."""
    public = urlsplit(settings.gen9_api_public_url)
    return [f"{public.scheme}://{public.netloc}", *settings.mcp_allowed_origins]


def asgi(app: FastAPI, settings: Settings) -> StarletteWithLifespan:
    """`/mcp` as an ASGI app, stateless. MCP's Streamable HTTP transport (2026-07-28): "Servers
    MUST validate the `Origin` header on all incoming connections to prevent DNS rebinding
    attacks", with 403 for an invalid one. FastMCP's guard does it before any token is read, but
    only when asked (it is off by default): with an explicit list, every Origin present must be
    on it, and a request without one (a client outside a browser) goes on. On a loopback install
    any loopback origin counts as the API's own, so a rebinding page, whose Origin keeps its own
    name, is refused. The Host is left alone: behind a proxy it is the public name."""
    return make(app, settings).http_app(
        path="/mcp",
        stateless_http=True,
        host_origin_protection="auto",
        allowed_origins=origins(settings),
    )


def make(app: FastAPI, settings: Settings) -> FastMCP:
    """The server, its tools reading the API's runtime from `app.state` at call time."""
    mcp = FastMCP(
        "Gen9",
        instructions=(
            "Gen9 is the person's research agent. `ask` sends it a message; continue a chat "
            "with its chat_id. `search_chats`, `list_chats` and `read_chat` read the person's "
            "past chats."
        ),
        auth=auth(settings),
    )

    def url(chat: UUID) -> str:
        return f"{settings.gen9_ui_url.rstrip('/')}/chat/{chat}"

    async def person() -> User:
        token = get_access_token()
        if token is None or not token.claims.get("sub"):
            raise ToolError("Sign in to Gen9 first.")
        claims = token.claims
        async with app.state.sessionmaker() as session:
            return await upsert_user(
                session, claims["sub"], claims.get("email"), claims.get("name")
            )

    async def owned(user: User, chat_id: UUID) -> Thread:
        async with app.state.sessionmaker() as session:
            thread = await session.scalar(
                select(Thread).where(
                    Thread.id == chat_id,
                    Thread.user_id == user.id,
                    Thread.deleted_at.is_(None),
                )
            )
        if thread is None:
            raise ToolError("No such chat.")
        return thread

    async def latest(chat_id: UUID) -> tuple[UUID, str] | None:
        async with app.state.engine.connect() as conn:
            row = (
                await conn.execute(
                    select(Run.id, Run.status)
                    .where(Run.thread_id == chat_id)
                    .order_by(Run.created_at.desc())
                    .limit(1)
                )
            ).first()
        return (row.id, row.status) if row else None

    async def last_answer(chat_id: UUID) -> str | None:
        state = await app.state.agent.aget_state(
            {"configurable": {"thread_id": str(chat_id)}}
        )
        answers = [m for m in conversation(state.values or {}) if m.role == "assistant"]
        return answers[-1].content if answers else None

    async def start_ask(message: str, chat_id: UUID | None) -> tuple[UUID, UUID]:
        """A run of `message`, as the token's person, in a new chat or one of theirs: its chat
        and run."""
        user = await person()
        if chat_id is None:
            async with app.state.sessionmaker() as session:
                thread = Thread(user_id=user.id)
                session.add(thread)
                await session.commit()
                await session.refresh(thread)
        else:
            thread = await owned(user, chat_id)
            if thread.parent_id is not None:
                raise ToolError(
                    "That chat is a background task's; ask its chat instead."
                )
        try:
            run_id = await control.start_run(
                app.state.engine,
                app.state.temporal,
                thread.id,
                user.sub,
                {"message": message, "permission_mode": thread.permission_mode},
                wait_s=settings.run_wait_s,
            )
        except store.ActiveRunExists:
            raise ToolError(
                "That chat is still answering. Read it with read_chat, then ask again."
            ) from None
        return thread.id, run_id

    async def answer_for(chat_id: UUID, status: str | None) -> Answer:
        said = SAID.get(status or "queued", "working")
        return Answer(
            chat_id=chat_id,
            url=url(chat_id),
            status=said,
            answer=await last_answer(chat_id) if said == "done" else None,
        )

    @mcp.tool(annotations={"openWorldHint": True})
    async def ask(
        message: MessageText,
        chat_id: Annotated[
            UUID | None,
            Field(description="A chat to continue; a new one when left out"),
        ] = None,
    ) -> Answer:
        """Ask Gen9, the person's research agent: it searches the web, uses their connectors and
        remembers them. Waits for its answer up to a limit, then says how it stands."""
        chat, run_id = await start_ask(message, chat_id)
        status = "queued"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + settings.mcp_ask_wait_s
        while loop.time() < deadline:
            async with app.state.engine.connect() as conn:
                status = await conn.scalar(select(Run.status).where(Run.id == run_id))
            if status in FINAL_RUN_STATUSES or status == "waiting":
                break
            await asyncio.sleep(1)
        return await answer_for(chat, status)

    # MCP Tasks for `ask` (mcp_tasks.py): tasks are these runs, read as the token's person
    class AskArgs(BaseModel):
        message: MessageText
        chat_id: UUID | None = None

    async def task_start(arguments: dict) -> tuple[UUID, UUID]:
        try:
            args = AskArgs.model_validate(arguments)
        except ValidationError as e:
            raise MCPError(code=INVALID_PARAMS, message=str(e)) from None
        return await start_ask(args.message, args.chat_id)

    async def task_find(run_id: UUID) -> TaskRun | None:
        user = await person()
        latest = (
            select(func.max(RunEvent.created_at))
            .where(RunEvent.run_id == Run.id)
            .scalar_subquery()
        )
        async with app.state.engine.connect() as conn:
            row = (
                await conn.execute(
                    select(
                        Run.id,
                        Run.thread_id,
                        Run.status,
                        Run.created_at,
                        func.coalesce(
                            latest, Run.finished_at, Run.started_at, Run.created_at
                        ).label("updated_at"),
                    )
                    .join(Thread, Thread.id == Run.thread_id)
                    .where(
                        Run.id == run_id,
                        Thread.user_id == user.id,
                        Thread.deleted_at.is_(None),
                    )
                )
            ).first()
        if row is None:
            return None
        return TaskRun(**row._mapping, user_sub=user.sub)

    async def task_pending(run_id: UUID) -> list[tuple[str, str, dict]]:
        async with app.state.engine.connect() as conn:
            rows = await conn.execute(
                select(InputRequest.id, InputRequest.kind, InputRequest.request)
                .where(InputRequest.run_id == run_id, InputRequest.response.is_(None))
                .order_by(InputRequest.created_at)
            )
            return [(r.id, r.kind, r.request) for r in rows]

    async def task_answer(run: TaskRun, input_id: str, response: dict) -> None:
        try:
            await control.answer(
                app.state.engine,
                app.state.temporal,
                run.id,
                input_id,
                run.user_sub,
                response,
            )
        except (control.InputNotFound, control.AlreadyAnswered, control.NotWaiting):
            pass  # answered already, or no longer asked: nothing outstanding to answer

    async def task_stop(run: TaskRun) -> None:
        await control.stop_run(app.state.engine, app.state.temporal, run.id)

    async def task_result(run: TaskRun) -> Answer:
        return await answer_for(run.thread_id, run.status)

    mcp.add_extension(
        Gen9Tasks(
            start=task_start,
            find=task_find,
            pending=task_pending,
            answer=task_answer,
            stop=task_stop,
            result=task_result,
        )
    )

    @mcp.tool(annotations={"readOnlyHint": True})
    async def read_chat(chat_id: UUID) -> Chat:
        """One of the person's chats: its latest messages and how its latest run stands."""
        user = await person()
        thread = await owned(user, chat_id)
        state = await app.state.agent.aget_state(
            {"configurable": {"thread_id": str(thread.id)}}
        )
        messages = [
            Message(role=m.role, content=m.content[:MESSAGE_CHARS])
            for m in conversation(state.values or {})
            if m.content
        ][-LATEST_MESSAGES:]
        run = await latest(thread.id)
        return Chat(
            chat_id=thread.id,
            title=thread.title,
            url=url(thread.id),
            status=SAID.get(run[1], "working") if run else None,
            messages=messages,
        )

    @mcp.tool(annotations={"readOnlyHint": True})
    async def list_chats(
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> list[ChatSummary]:
        """The person's chats, most recent first."""
        user = await person()
        newest = (
            select(Run.status)
            .where(Run.thread_id == Thread.id)
            .order_by(Run.created_at.desc())
            .limit(1)
            .scalar_subquery()
        )
        async with app.state.sessionmaker() as session:
            rows = (
                await session.execute(
                    select(Thread.id, Thread.title, Thread.updated_at, newest)
                    .where(
                        Thread.user_id == user.id,
                        Thread.deleted_at.is_(None),
                        Thread.parent_id.is_(None),
                    )
                    .order_by(Thread.updated_at.desc())
                    .limit(limit)
                )
            ).tuples()
            return [
                ChatSummary(
                    chat_id=i,
                    title=t,
                    updated_at=u,
                    status=SAID.get(s, "working") if s else None,
                )
                for i, t, u, s in rows
            ]

    @mcp.tool(annotations={"readOnlyHint": True})
    async def search_chats(
        query: Annotated[str, Field(min_length=1, max_length=500)],
        mode: Mode = "hybrid",
        limit: Annotated[int, Field(ge=1, le=50)] = 5,
    ) -> list[Hit]:
        """Search the person's past chats by words and meaning (hybrid), words (keyword),
        meaning (semantic) or a misspelled title (fuzzy)."""
        user = await person()
        async with app.state.sessionmaker() as session:
            try:
                runtime = app.state.runtime
                hits = await find(
                    runtime.settings, runtime.models, session, user, query, mode, limit
                )
            except HTTPException as e:
                raise ToolError(str(e.detail)) from None
        return [
            Hit(
                chat_id=h.thread_id,
                title=h.title,
                snippet=h.snippet,
                created_at=h.created_at,
            )
            for h in hits
        ]

    return mcp
