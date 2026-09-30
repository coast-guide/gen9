"""Gen9 as an A2A agent (docs/plans/harness.md, milestone 6; Decision Log): other
agents send it tasks over A2A 1.0 (JSON-RPC), at `/a2a` in the agent API, and read its Agent Card at
`/.well-known/agent-card.json`.

- **Protocol surface** from `a2a-sdk`: its types, JSON-RPC dispatch (with SSE for streaming), the
  Agent Card route and version handling.
- **State** from Gen9: a Gen9 `RequestHandler` answers every operation from Gen9's own runs. A task
  is a run and a context is a chat (`contextId` continues one), so a task survives what a run
  survives, and there is no second store to drift. Push notifications are refused.
- **Who:** tokens from Gen9's Keycloak whose audience is `GEN9_A2A_URL` and whose scopes carry
  `gen9-a2a`, checked by an async authentication middleware (the SDK builds each call's context
  from what it sets). The Agent Card declares OAuth 2.0's authorization code flow against Keycloak.
  Every task and context is the token's person's.
- **Human in the loop:** a run that waits for the person is `input-required`, its status message
  saying what it asks (the requests under the message's `metadata.gen9`, with the body each answer
  takes). A reply on that task answers them: a data part with Gen9's answer body, or text for a
  question.
"""

import asyncio
import contextlib
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import jwt
from a2a.helpers.proto_helpers import (
    get_data_parts,
    get_text_parts,
    new_data_part,
    new_message,
    new_text_artifact,
    new_text_message,
    new_text_part,
)
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Artifact,
    AuthorizationCodeOAuthFlow,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTaskPushNotificationConfigsResponse,
    ListTasksRequest,
    ListTasksResponse,
    Message,
    OAuth2SecurityScheme,
    OAuthFlows,
    Role,
    SecurityRequirement,
    SecurityScheme,
    SendMessageRequest,
    StringList,
    SubscribeToTaskRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.errors import (
    ExtendedAgentCardNotConfiguredError,
    InvalidParamsError,
    PushNotificationNotSupportedError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
)
from a2a.utils.task import decode_page_token, encode_page_token
from fastapi import FastAPI
from sqlalchemy import and_, func, or_, select
from starlette.applications import Starlette
from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
    AuthenticationError,
    BaseUser,
)
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.routing import Route

from .api.agui import ANSWERS, waiting_on
from .api.threads import conversation
from .auth import Principal, TokenVerifier
from .models import ACTIVE_RUN_STATUSES, FINAL_RUN_STATUSES, Run, Thread, User
from .runs import control, log, store
from .settings import Settings
from .users import upsert_user

SCOPE = "gen9-a2a"
# How long a stream waits for the run's log to grow before checking on it again
WAIT_S = 15
STATE = {
    "queued": TaskState.TASK_STATE_SUBMITTED,
    "running": TaskState.TASK_STATE_WORKING,
    "waiting": TaskState.TASK_STATE_INPUT_REQUIRED,
    "success": TaskState.TASK_STATE_COMPLETED,
    "error": TaskState.TASK_STATE_FAILED,
    "expired": TaskState.TASK_STATE_FAILED,
    "cancelled": TaskState.TASK_STATE_CANCELED,
}
DONE = frozenset(FINAL_RUN_STATUSES)
# A task's status time: when its run last changed (ended, else started, else was queued)
CHANGED = func.coalesce(Run.finished_at, Run.started_at, Run.created_at)
# ListTasks' page size when none is asked for, and its most (A2A 1.0.1's ListTasksRequest)
PAGE_SIZE, MAX_PAGE_SIZE = 50, 100


def _history_length(params: Any) -> int | None:
    """A request's historyLength: None when unset (Gen9's default), else at least 0."""
    return max(params.history_length, 0) if params.HasField("history_length") else None


def card(settings: Settings) -> AgentCard:
    """What Gen9 says about itself to other agents."""
    issuer = settings.keycloak_issuer.rstrip("/")
    return AgentCard(
        name="Gen9",
        description=(
            "A research agent: it searches the web, uses the person's connectors and "
            "remembers them, and answers with its sources."
        ),
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(
                url=settings.gen9_a2a_url,
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        security_schemes={
            "keycloak": SecurityScheme(
                oauth2_security_scheme=OAuth2SecurityScheme(
                    description="Sign in to Gen9: Keycloak, authorization code with PKCE",
                    flows=OAuthFlows(
                        authorization_code=AuthorizationCodeOAuthFlow(
                            authorization_url=f"{issuer}/protocol/openid-connect/auth",
                            token_url=f"{issuer}/protocol/openid-connect/token",
                            scopes={SCOPE: "Send Gen9 tasks and read their results"},
                            pkce_required=True,
                        )
                    ),
                    oauth2_metadata_url=f"{issuer}/.well-known/openid-configuration",
                )
            )
        },
        security_requirements=[
            SecurityRequirement(schemes={"keycloak": StringList(list=[SCOPE])})
        ],
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="research",
                name="Research and answer",
                description=(
                    "Answers a question or does a task, searching the web and the person's "
                    "connectors, and says where its answer came from."
                ),
                tags=["research", "web search", "answers"],
                examples=["What changed in Postgres 18's I/O?"],
            )
        ],
    )


class Person(BaseUser):
    """The token's person, for the SDK's call context (its `user_name` is the `sub`)."""

    def __init__(self, principal: Principal) -> None:
        self.principal = principal

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def display_name(self) -> str:
        return self.principal.sub


class Signed(AuthCredentials):
    """The request's credentials, carrying the verified token's person: the SDK puts them in
    the call context's state (`auth`)."""

    def __init__(self, principal: Principal) -> None:
        super().__init__([SCOPE])
        self.principal = principal


class Bearer(AuthenticationBackend):
    """A token for this endpoint, or 401: checked off the event loop (JWKS may be fetched)."""

    def __init__(self, verifier: TokenVerifier) -> None:
        self.verifier = verifier

    async def authenticate(
        self, conn: HTTPConnection
    ) -> tuple[AuthCredentials, BaseUser]:
        scheme, _, token = conn.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise AuthenticationError("Sign in to Gen9 first")
        try:
            principal = await asyncio.to_thread(self.verifier.verify, token)
        except jwt.PyJWTError as e:
            raise AuthenticationError(str(e)) from None
        return Signed(principal), Person(principal)


def _unauthorized(conn: HTTPConnection, exc: Exception) -> JSONResponse:
    return JSONResponse(
        {"detail": str(exc)},
        status_code=401,
        headers={"WWW-Authenticate": f'Bearer scope="{SCOPE}"'},
    )


@dataclass(frozen=True)
class Caller:
    """An A2A call's person, and the client its token was issued to (`azp`). A client reaches
    only the chats it started, as its consent says ("send it tasks and read their results"): not
    the person's own chats, or another agent's (docs/plans/manual-e2e.md, P5-C7)."""

    user: User
    client: str

    def chats(self) -> list[Any]:
        return [
            Thread.user_id == self.user.id,
            Thread.a2a_client == self.client,
            Thread.deleted_at.is_(None),
        ]


class Gen9(RequestHandler):
    """A2A's operations on Gen9's runs, as the call's person."""

    def __init__(self, app: FastAPI, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    # -- the person, their chats and runs --

    async def person(self, context: ServerCallContext) -> Caller:
        """The token's person, and the client (`azp`) it was issued to."""
        signed = context.state.get("auth")
        if not isinstance(signed, Signed):
            raise InvalidParamsError(message="Sign in to Gen9 first")
        principal = signed.principal
        async with self.app.state.sessionmaker() as session:
            user = await upsert_user(
                session, principal.sub, principal.email, principal.name
            )
        return Caller(user, principal.client_id)

    async def chat(self, caller: Caller, context_id: str) -> Thread:
        """One of the chats this client started for the person; any other is "no such context",
        as A2A asks a server not to reveal what a client can't reach."""
        try:
            wanted = uuid.UUID(context_id)
        except ValueError:
            raise InvalidParamsError(message="No such context") from None
        async with self.app.state.sessionmaker() as session:
            thread = await session.scalar(
                select(Thread).where(
                    Thread.id == wanted,
                    *caller.chats(),
                    Thread.parent_id.is_(None),
                )
            )
        if thread is None:
            raise InvalidParamsError(message="No such context")
        return thread

    async def run_of(self, caller: Caller, task_id: str) -> Run:
        try:
            wanted = uuid.UUID(task_id)
        except ValueError:
            raise TaskNotFoundError() from None
        async with self.app.state.sessionmaker() as session:
            run = await session.scalar(
                select(Run)
                .join(Thread, Thread.id == Run.thread_id)
                .where(Run.id == wanted, *caller.chats())
            )
        if run is None:
            raise TaskNotFoundError()
        return run

    async def task(
        self, run_id: uuid.UUID, history: int | None = None, artifacts: bool = True
    ) -> Task:
        """A run as an A2A task: its state and when it last changed, what it waits for, its
        answer as an artifact (unless `artifacts` is off), and its question and answer as
        history, at most `history` of them (A2A's historyLength: None for all, 0 for none)."""
        async with self.app.state.sessionmaker() as session:
            run = await session.get(Run, run_id)
        # Deleted since its owner was checked (its chat deleted meanwhile): not found, not a 500
        if run is None:
            raise TaskNotFoundError()
        context = str(run.thread_id)
        status = TaskStatus(state=STATE.get(run.status, TaskState.TASK_STATE_WORKING))
        status.timestamp.FromDatetime(
            run.finished_at or run.started_at or run.created_at
        )
        if run.status == "waiting":
            asks = await waiting_on(self.app.state.engine, run.id)
            said = "; ".join(
                str(
                    next((q.get("question") for q in a.get("questions") or []), None)
                    or a["kind"]
                )
                for a in asks
            )
            asked = new_message(
                [
                    new_text_part(f"Gen9 needs you: {said}"),
                    new_data_part(
                        {
                            "gen9": [
                                {**a, "answer_schema": ANSWERS.get(a.get("kind", ""))}
                                for a in asks
                            ]
                        }
                    ),
                ],
                context_id=context,
                task_id=str(run.id),
            )
            status.message.CopyFrom(asked)
        elif run.status in ("error", "expired"):
            status.message.CopyFrom(
                new_text_message(
                    run.error or "Gen9 couldn't answer.",
                    context_id=context,
                    task_id=str(run.id),
                )
            )
        answer = (
            await self.answer(run.thread_id, run.id)
            if run.status == "success"
            else None
        )
        task = Task(id=str(run.id), context_id=context, status=status)
        if answer and artifacts:
            task.artifacts.append(
                new_text_artifact("answer", answer, artifact_id="answer")
            )
        said = [
            new_text_message(
                str(run.input.get("message", "")),
                context_id=context,
                task_id=str(run.id),
                role=Role.ROLE_USER,
            )
        ]
        if answer:
            said.append(
                new_text_message(answer, context_id=context, task_id=str(run.id))
            )
        if history != 0:
            task.history.extend(said if history is None else said[-history:])
        return task

    async def answer(self, thread_id: uuid.UUID, run_id: uuid.UUID) -> str | None:
        """The run's own answer: the assistant's words after its question."""
        state = await self.app.state.agent.aget_state(
            {"configurable": {"thread_id": str(thread_id)}}
        )
        messages = conversation(state.values or {})
        asked = next(
            (
                i
                for i, m in enumerate(messages)
                if m.role == "user" and m.run_id == str(run_id)
            ),
            None,
        )
        if asked is None:
            return None
        words = []
        for message in messages[asked + 1 :]:
            if message.role == "user":
                break
            if message.content:
                words.append(message.content)
        return "\n\n".join(words) or None

    # -- sending --

    async def start(
        self, params: SendMessageRequest, context: ServerCallContext
    ) -> Run:
        """A new task from the message, or the answer to the one it replies to. A message this
        client already sent (its `messageId`) is the task it made then: A2A lets an agent use
        the id to tell a repeat, and a replayed message would otherwise run, and cost, again."""
        caller = await self.person(context)
        user = caller.user
        message = params.message
        if message.task_id:
            run = await self.run_of(caller, message.task_id)
            if run.status != "waiting":
                raise InvalidParamsError(message="That task doesn't wait for input")
            await self.reply(user, run, message)
            return run
        if message.message_id:
            async with self.app.state.sessionmaker() as session:
                sent = await session.scalar(
                    select(Run)
                    .join(Thread, Thread.id == Run.thread_id)
                    .where(
                        *caller.chats(),
                        Run.input["a2a_message_id"].astext == message.message_id,
                    )
                    .limit(1)
                )
            if sent is not None:
                return sent
        if message.context_id:
            thread = await self.chat(caller, message.context_id)
        else:
            async with self.app.state.sessionmaker() as session:
                thread = Thread(user_id=user.id, a2a_client=caller.client)
                session.add(thread)
                await session.commit()
                await session.refresh(thread)
        text = "\n".join(get_text_parts(message.parts)).strip()
        if not text:
            raise InvalidParamsError(message="Send a text part")
        metadata = dict(message.metadata) if message.metadata else {}
        mode = metadata.get("permissionMode")
        try:
            run_id = await control.start_run(
                self.app.state.engine,
                self.app.state.temporal,
                thread.id,
                user.sub,
                {
                    "message": text,
                    "permission_mode": mode
                    if mode in ("ask", "auto")
                    else thread.permission_mode,
                    **(
                        {"a2a_message_id": message.message_id}
                        if message.message_id
                        else {}
                    ),
                },
                wait_s=self.settings.run_wait_s,
            )
        except store.ActiveRunExists:
            raise InvalidParamsError(
                message="That context is still working on a task"
            ) from None
        async with self.app.state.sessionmaker() as session:
            run = await session.get(Run, run_id)
        assert run is not None
        return run

    async def reply(self, user: User, run: Run, message: Message) -> None:
        """A reply to an input-required task: Gen9's answer body in a data part, or text for
        a question."""
        asks = await waiting_on(self.app.state.engine, run.id)
        data = [d for d in get_data_parts(message.parts) if isinstance(d, dict)]
        text = "\n".join(get_text_parts(message.parts)).strip()
        for ask in asks:
            body = next(iter(data), None)
            if body is None and ask.get("kind") == "question" and text:
                body = {"answers": [text] * len(ask.get("questions") or [None])}
            if body is None:
                raise InvalidParamsError(
                    message=f"Answer {ask['kind']} {ask['id']} with a data part: "
                    f"{ANSWERS.get(ask['kind'])}"
                )
            try:
                await control.answer(
                    self.app.state.engine,
                    self.app.state.temporal,
                    run.id,
                    ask["id"],
                    user.sub,
                    body,
                )
            except control.AlreadyAnswered:
                continue
            except (control.InputNotFound, control.NotWaiting, ValueError) as e:
                raise InvalidParamsError(message=str(e) or type(e).__name__) from None

    async def settled(self, run_id: uuid.UUID, seconds: float) -> None:
        """Until the run ends or waits for the person, at most `seconds`."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds
        while loop.time() < deadline:
            async with self.app.state.engine.connect() as conn:
                status = await conn.scalar(select(Run.status).where(Run.id == run_id))
            if status in DONE or status == "waiting":
                return
            await asyncio.sleep(1)

    async def on_message_send(
        self, params: SendMessageRequest, context: ServerCallContext
    ) -> Task | Message:
        run = await self.start(params, context)
        if not (params.configuration and params.configuration.return_immediately):
            # A reply lets the run go on: give it a moment to leave `waiting`
            await asyncio.sleep(0.5 if params.message.task_id else 0)
            await self.settled(run.id, self.settings.a2a_wait_s)
        return await self.task(run.id)

    async def on_message_send_stream(
        self, params: SendMessageRequest, context: ServerCallContext
    ) -> AsyncGenerator[Any]:
        run = await self.start(params, context)
        async for event in self.follow(run.id):
            yield event

    async def on_subscribe_to_task(
        self, params: SubscribeToTaskRequest, context: ServerCallContext
    ) -> AsyncGenerator[Any]:
        """A task's updates again, for a client whose stream dropped: the task as it is, then
        what happens next. A task that has ended has nothing more to say (A2A 1.0.1, "Subscribe
        to Task": UnsupportedOperationError for a terminal state); GetTask reads it."""
        run = await self.run_of(await self.person(context), params.id)
        if run.status in DONE:
            raise UnsupportedOperationError(
                message="That task has ended: read it with GetTask"
            )
        async for event in self.follow(run.id):
            yield event

    async def follow(self, run_id: uuid.UUID) -> AsyncGenerator[Any]:
        """The task, then its updates from the run's log: the answer as artifact chunks, and
        a status at each change, until it ends or waits for the person."""
        engine, hub = self.app.state.engine, self.app.state.event_hub
        task = await self.task(run_id, history=0)
        yield task
        context, task_id = task.context_id, task.id
        after, streamed = 0, False
        while True:
            rows = await log.read_after(engine, run_id, after)
            for seq, kind, data in rows:
                after = seq
                if kind == "run.started":
                    yield TaskStatusUpdateEvent(
                        task_id=task_id,
                        context_id=context,
                        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                    )
                elif kind == "message.delta":
                    yield TaskArtifactUpdateEvent(
                        task_id=task_id,
                        context_id=context,
                        artifact=Artifact(
                            artifact_id="answer",
                            name="answer",
                            parts=[new_text_part(data.get("text", ""))],
                        ),
                        append=streamed,
                    )
                    streamed = True
                elif kind == "run.completed":
                    final = await self.task(run_id, history=0)
                    if streamed:
                        yield TaskArtifactUpdateEvent(
                            task_id=task_id,
                            context_id=context,
                            artifact=Artifact(artifact_id="answer", name="answer"),
                            append=True,
                            last_chunk=True,
                        )
                    elif final.artifacts:
                        yield TaskArtifactUpdateEvent(
                            task_id=task_id,
                            context_id=context,
                            artifact=final.artifacts[0],
                            last_chunk=True,
                        )
                    yield TaskStatusUpdateEvent(
                        task_id=task_id, context_id=context, status=final.status
                    )
                    return
            if rows:
                continue
            if await waiting_on(engine, run_id):
                final = await self.task(run_id, history=0)
                yield TaskStatusUpdateEvent(
                    task_id=task_id, context_id=context, status=final.status
                )
                return
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(WAIT_S):
                    await hub.wait(run_id)

    # -- reading and stopping --

    async def on_get_task(
        self, params: GetTaskRequest, context: ServerCallContext
    ) -> Task | None:
        run = await self.run_of(await self.person(context), params.id)
        return await self.task(run.id, history=_history_length(params))

    async def on_list_tasks(
        self, params: ListTasksRequest, context: ServerCallContext
    ) -> ListTasksResponse:
        """The person's tasks as A2A 1.0.1's "List Tasks" has them: the last changed first,
        in pages behind a cursor (the task after the page, as the SDK's database task store
        keeps it), filtered by context, state and time, without artifacts unless asked, and
        always a `next_page_token`, empty on the last page."""
        caller = await self.person(context)
        size = params.page_size if params.HasField("page_size") else PAGE_SIZE
        if not 1 <= size <= MAX_PAGE_SIZE:
            raise InvalidParamsError(message=f"pageSize is from 1 to {MAX_PAGE_SIZE}")
        where = caller.chats()
        if params.context_id:
            where.append(Thread.id == (await self.chat(caller, params.context_id)).id)
        if params.status:
            where.append(
                Run.status.in_([k for k, v in STATE.items() if v == params.status])
            )
        if params.HasField("status_timestamp_after"):
            where.append(
                CHANGED >= params.status_timestamp_after.ToDatetime(tzinfo=UTC)
            )
        async with self.app.state.sessionmaker() as session:
            total = await session.scalar(
                select(func.count()).select_from(Run).join(Thread).where(*where)
            )
            page = select(Run.id).join(Thread).where(*where)
            if params.page_token:
                after = await self.cursor(session, caller, params.page_token)
                page = page.where(
                    or_(
                        CHANGED < after[1],
                        and_(CHANGED == after[1], Run.id <= after[0]),
                    )
                )
            ids = list(
                await session.scalars(
                    page.order_by(CHANGED.desc(), Run.id.desc()).limit(size + 1)
                )
            )
        history = _history_length(params)
        return ListTasksResponse(
            tasks=[
                await self.task(
                    i,
                    history=0 if history is None else history,
                    artifacts=params.include_artifacts,
                )
                for i in ids[:size]
            ],
            next_page_token=encode_page_token(str(ids[size]))
            if len(ids) > size
            else "",
            page_size=size,
            total_size=total or 0,
        )

    async def cursor(
        self, session: Any, caller: Caller, token: str
    ) -> tuple[uuid.UUID, datetime]:
        """The task a page token names, the first of its page, and when it last changed."""
        try:
            first = uuid.UUID(decode_page_token(token))
        except ValueError:
            raise InvalidParamsError(
                message="That pageToken isn't one Gen9 gave"
            ) from None
        row = (
            await session.execute(
                select(Run.id, CHANGED)
                .join(Thread)
                .where(Run.id == first, *caller.chats())
            )
        ).first()
        if row is None:
            raise InvalidParamsError(message="That pageToken isn't one Gen9 gave")
        return row[0], row[1]

    async def on_cancel_task(
        self, params: CancelTaskRequest, context: ServerCallContext
    ) -> Task | None:
        run = await self.run_of(await self.person(context), params.id)
        if run.status not in ACTIVE_RUN_STATUSES:
            raise TaskNotCancelableError()
        await control.stop_run(self.app.state.engine, self.app.state.temporal, run.id)
        await self.settled(run.id, 10)
        return await self.task(run.id)

    # -- not offered --

    async def on_create_task_push_notification_config(
        self, params: TaskPushNotificationConfig, context: ServerCallContext
    ) -> TaskPushNotificationConfig:
        raise PushNotificationNotSupportedError()

    async def on_get_task_push_notification_config(
        self, params: GetTaskPushNotificationConfigRequest, context: ServerCallContext
    ) -> TaskPushNotificationConfig:
        raise PushNotificationNotSupportedError()

    async def on_list_task_push_notification_configs(
        self, params: ListTaskPushNotificationConfigsRequest, context: ServerCallContext
    ) -> ListTaskPushNotificationConfigsResponse:
        raise PushNotificationNotSupportedError()

    async def on_delete_task_push_notification_config(
        self,
        params: DeleteTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> None:
        raise PushNotificationNotSupportedError()

    async def on_get_extended_agent_card(
        self, params: GetExtendedAgentCardRequest, context: ServerCallContext
    ) -> AgentCard:
        raise ExtendedAgentCardNotConfiguredError()


def mount(app: FastAPI, settings: Settings) -> None:
    """The Agent Card (public) and the JSON-RPC endpoint (signed in) on the API."""
    verifier = TokenVerifier.from_jwks_url(
        settings.jwks_url,
        issuer=settings.keycloak_issuer,
        audience=settings.gen9_a2a_url,
        allowed_clients=None,
        required_scope=SCOPE,
    )
    for route in create_agent_card_routes(card(settings)):
        app.router.routes.append(route)
    # The JSON-RPC route behind the authentication middleware, answering at exactly `/a2a` (a
    # mount would redirect it to `/a2a/`): a Route whose endpoint is that small app
    endpoint = Starlette(
        routes=create_jsonrpc_routes(Gen9(app, settings), rpc_url="/a2a"),
        middleware=[
            Middleware(
                AuthenticationMiddleware,
                backend=Bearer(verifier),
                on_error=_unauthorized,
            )
        ],
    )
    app.router.routes.append(Route("/a2a", endpoint=endpoint, methods=["POST"]))
