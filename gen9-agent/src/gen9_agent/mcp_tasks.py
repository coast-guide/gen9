"""MCP Tasks for `ask` (SEP-2663, `io.modelcontextprotocol/tasks`, MCP 2026-07-28; docs/plans/
harness.md, "MCP Tasks for `ask`").

A client that declares the extension on a `tools/call` of `ask` gets a task at once instead of
an answer within `MCP_ASK_WAIT_S`, and follows it with `tasks/get`, `tasks/update` and
`tasks/cancel`. A task is a Gen9 run: its id is the run's, and the run is already durable on
Temporal, so there is no task store here (FastMCP's `fastmcp-tasks` keeps its own on Docket and
Redis; this adapts the `ServerExtension` API it builds on instead).

- `working`: the run is queued or running.
- `input_required`: the run waits for the person. Each thing it asks is an `elicitation/create`
  form in `inputRequests`, keyed by the run's interrupt id (unique for the task's life): a
  question's fields, an approval's approve or reject per action, a connector server's own
  elicitation passed through, and a failed turn's "Try again". `tasks/update` answers through
  `control.answer`, exactly as the web app does.
- `completed`: the run ended, done or not; `result` is what `ask` returns. `cancelled`: stopped.

Every `tasks/*` call acts as the token's person: another person's run, or a deleted chat's, is
"not found". A client that didn't declare the extension gets -32021, as SEP-2663 requires.
"""

import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from fastmcp.server.dependencies import get_http_request
from fastmcp.server.extensions import (
    MethodBinding,
    ServerExtension,
    read_client_extension_settings,
)
from fastmcp.utilities.tasks import TASKS_EXTENSION_ID
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp.shared.inbound import MCP_NAME_HEADER, decode_header_value
from mcp_types import INVALID_PARAMS, RequestParams, Result
from mcp_types.jsonrpc import HEADER_MISMATCH, MISSING_REQUIRED_CLIENT_CAPABILITY
from mcp_types.version import MODERN_PROTOCOL_VERSIONS
from pydantic import BaseModel, ConfigDict, Field

from . import approvals, elicitation, questions
from .runs import store

if TYPE_CHECKING:
    from fastmcp.server.context import Context
    from fastmcp.server.extensions import ToolCallContinuation, ToolCallOutcome
    from mcp_types import CallToolRequestParams

TaskStatus = Literal["working", "input_required", "completed", "failed", "cancelled"]
# A run's status as a task's (every other status a run can have ends it: completed)
STATUS: dict[str, TaskStatus] = {
    "queued": "working",
    "running": "working",
    "waiting": "input_required",
    "cancelled": "cancelled",
}
POLL_MS = 2000
# How long a task is promised to stay readable: a waiting run's own limit (runs.WAIT_FOR_PERSON).
# It stays readable after, as long as its chat; SEP-2663 makes ttlMs required, a number here
TTL_MS = 7 * 24 * 3600 * 1000
# What a declined question is answered with, so the run goes on knowing
DECLINED = "(The person declined to answer.)"


class _Task(BaseModel):
    """SEP-2663's Task, flat, in the wire's camelCase."""

    model_config = ConfigDict(serialize_by_alias=True)

    task_id: str = Field(serialization_alias="taskId")
    status: TaskStatus
    status_message: str | None = Field(
        default=None, serialization_alias="statusMessage"
    )
    created_at: str = Field(serialization_alias="createdAt")
    last_updated_at: str = Field(serialization_alias="lastUpdatedAt")
    ttl_ms: int = Field(default=TTL_MS, serialization_alias="ttlMs")
    poll_interval_ms: int = Field(default=POLL_MS, serialization_alias="pollIntervalMs")


class CreateTaskResult(_Task):
    result_type: Literal["task"] = Field(
        default="task", serialization_alias="resultType"
    )


class GetTaskResult(_Task):
    result_type: Literal["complete"] = Field(
        default="complete", serialization_alias="resultType"
    )
    result: dict[str, Any] | None = None
    input_requests: dict[str, Any] | None = Field(
        default=None, serialization_alias="inputRequests"
    )


class Ack(Result):
    """`tasks/update`'s and `tasks/cancel`'s empty acknowledgement."""

    result_type: Literal["complete"] = Field(
        default="complete", serialization_alias="resultType"
    )


class GetTaskParams(RequestParams):
    task_id: str = Field(alias="taskId")


class UpdateTaskParams(RequestParams):
    task_id: str = Field(alias="taskId")
    input_responses: dict[str, Any] = Field(alias="inputResponses")


class CancelTaskParams(RequestParams):
    task_id: str = Field(alias="taskId")


def when(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _form(message: str, properties: dict[str, Any], required: list[str]) -> dict:
    return {
        "method": "elicitation/create",
        "params": {
            "mode": "form",
            "message": message,
            "requestedSchema": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def input_requests(
    pending: Sequence[tuple[str, str, dict[str, Any]]],
) -> dict[str, Any]:
    """What a waiting run asks, as SEP-2663's `inputRequests`: `pending` is each unanswered
    input's (id, kind, request)."""
    requests: dict[str, Any] = {}
    for input_id, kind, request in pending:
        if kind == questions.KIND:
            asked = request.get("questions") or []
            properties = {}
            for n, q in enumerate(asked, 1):
                field: dict[str, Any] = {"type": "string", "title": q["question"]}
                if q.get("type") == "multiple_choice":
                    field["enum"] = list(q.get("choices") or [])
                properties[f"q{n}"] = field
            required = [
                f"q{n}" for n, q in enumerate(asked, 1) if q.get("required", True)
            ]
            message = (
                asked[0]["question"]
                if len(asked) == 1
                else "Gen9 has questions for you."
            )
            requests[input_id] = _form(message, properties, required)
        elif kind == approvals.KIND:
            actions = request.get("action_requests") or []
            properties = {
                f"a{n}": {
                    "type": "string",
                    "enum": ["approve", "reject"],
                    "title": a.get("description") or f"Let Gen9 use {a.get('name')}",
                }
                for n, a in enumerate(actions, 1)
            }
            requests[input_id] = _form(
                "Gen9 asks before acting.", properties, list(properties)
            )
        elif kind == elicitation.KIND:
            # A connector's server asking: already MCP's shape, passed through by its key
            for r in request.get("requests") or []:
                key = f"{input_id}.{r['key']}"
                if r.get("mode") == "url":
                    params = {k: r[k] for k in ("message", "url") if k in r}
                    requests[key] = {
                        "method": "elicitation/create",
                        "params": {"mode": "url", "elicitationId": key, **params},
                    }
                else:
                    requests[key] = {
                        "method": "elicitation/create",
                        "params": {
                            "mode": "form",
                            "message": r.get("message") or "",
                            "requestedSchema": r.get("requested_schema") or {},
                        },
                    }
        elif kind == store.RETRY_KIND:
            requests[input_id] = _form(
                f"This turn didn't finish: {request.get('error') or 'it failed'} Try again?",
                {"retry": {"type": "boolean", "title": "Try again"}},
                ["retry"],
            )
    return requests


def gen9_answer(
    input_id: str, kind: str, request: dict[str, Any], responses: dict[str, Any]
) -> dict[str, Any] | None:
    """The answer `control.answer` takes for one input, from a `tasks/update`'s responses, or None
    when they don't answer it (all of it: a connector's round is answered at once)."""
    if kind == elicitation.KIND:
        keys = {
            r["key"]: f"{input_id}.{r['key']}" for r in request.get("requests") or []
        }
        if not keys or not all(k in responses for k in keys.values()):
            return None
        return {"responses": {key: responses[k] for key, k in keys.items()}}
    result = responses.get(input_id)
    if not isinstance(result, dict):
        return None
    accepted = result.get("action") == "accept"
    content = result.get("content") or {}
    if kind == questions.KIND:
        asked = request.get("questions") or []
        return {
            "answers": [
                str(content.get(f"q{n}", "")) if accepted else DECLINED
                for n in range(1, len(asked) + 1)
            ]
        }
    if kind == approvals.KIND:
        actions = request.get("action_requests") or []
        return {
            "decisions": [
                {"type": "approve"}
                if accepted and content.get(f"a{n}") == "approve"
                else {"type": "reject"}
                for n in range(1, len(actions) + 1)
            ]
        }
    if kind == store.RETRY_KIND:
        return {"retry": True} if accepted and content.get("retry") is True else None
    return None


def tool_result(answer: BaseModel) -> dict[str, Any]:
    """A completed `ask`'s CallToolResult, as the tool itself returns it on 2026-07-28, where
    every result says its type (SEP-2322; the client validates it)."""
    return {
        "resultType": "complete",
        "content": [{"type": "text", "text": answer.model_dump_json()}],
        "structuredContent": answer.model_dump(mode="json"),
        "isError": False,
    }


def _not_found() -> MCPError:
    return MCPError(
        code=INVALID_PARAMS, message="Failed to retrieve task: Task not found"
    )


class Run(BaseModel):
    """What a task is read from: the run, as its person may see it."""

    id: uuid.UUID
    thread_id: uuid.UUID
    status: str
    created_at: datetime
    updated_at: datetime
    user_sub: str


class Gen9Tasks(ServerExtension):
    """The tasks extension, over Gen9's runs. The server passes what it does for `ask`:
    `start` (a run for the call's arguments, as the token's person: its chat and id), `find` (a
    run by id if the token's person owns it), `pending` (its unanswered inputs), `answer`
    (`control.answer`), `stop` and `result` (what `ask` returns for a chat whose run ended)."""

    identifier = TASKS_EXTENSION_ID

    def __init__(
        self,
        *,
        start: Callable[[dict[str, Any]], Awaitable[tuple[uuid.UUID, uuid.UUID]]],
        find: Callable[[uuid.UUID], Awaitable[Run | None]],
        pending: Callable[
            [uuid.UUID], Awaitable[list[tuple[str, str, dict[str, Any]]]]
        ],
        answer: Callable[[Run, str, dict[str, Any]], Awaitable[None]],
        stop: Callable[[Run], Awaitable[None]],
        result: Callable[[Run], Awaitable[BaseModel]],
    ) -> None:
        self._start, self._find, self._pending = start, find, pending
        self._answer, self._stop, self._result = answer, stop, result

    def methods(self) -> Sequence[MethodBinding]:
        versions = frozenset(MODERN_PROTOCOL_VERSIONS)
        return [
            MethodBinding("tasks/get", GetTaskParams, self._get, versions),
            MethodBinding("tasks/update", UpdateTaskParams, self._update, versions),
            MethodBinding("tasks/cancel", CancelTaskParams, self._cancel, versions),
        ]

    async def intercept_tool_call(
        self,
        params: "CallToolRequestParams",
        context: "Context",
        call_next: "ToolCallContinuation",
    ) -> "ToolCallOutcome":
        rc = context.request_context
        # Gen9's tools never call each other, so a call of `ask` is always the client's own
        opted_in = (
            params.name == "ask"
            and rc is not None
            and rc.protocol_version in MODERN_PROTOCOL_VERSIONS
            and context.client_extension_settings(TASKS_EXTENSION_ID) is not None
        )
        if not opted_in:
            return await call_next()
        _chat, run_id = await self._start(dict(params.arguments or {}))
        run = await self._find(run_id)
        if run is None:  # it was just made, as this person
            raise _not_found()
        return CreateTaskResult(
            task_id=str(run.id),
            status=STATUS.get(run.status, "completed"),
            status_message="Gen9 is working on it.",
            created_at=when(run.created_at),
            last_updated_at=when(run.updated_at),
        )

    def _check(self, ctx: ServerRequestContext[Any, Any], task_id: str) -> None:
        if read_client_extension_settings(ctx, TASKS_EXTENSION_ID) is None:
            raise MCPError(
                code=MISSING_REQUIRED_CLIENT_CAPABILITY,
                message=f"Declare the {TASKS_EXTENSION_ID} extension for this request.",
                data={"requiredCapabilities": {"extensions": {TASKS_EXTENSION_ID: {}}}},
            )
        try:
            header = get_http_request().headers.get(MCP_NAME_HEADER)
        except RuntimeError:
            header = None
        if header is not None and decode_header_value(header) != task_id:
            raise MCPError(
                code=HEADER_MISMATCH,
                message=f"{MCP_NAME_HEADER} doesn't match the request's taskId",
            )

    async def _run(self, task_id: str) -> Run:
        try:
            run_id = uuid.UUID(task_id)
        except ValueError:
            raise _not_found() from None
        run = await self._find(run_id)
        if run is None:
            raise _not_found()
        return run

    async def _get(
        self, ctx: ServerRequestContext[Any, Any], params: GetTaskParams
    ) -> GetTaskResult:
        self._check(ctx, params.task_id)
        run = await self._run(params.task_id)
        status = STATUS.get(run.status, "completed")
        if status == "input_required":
            message = "Gen9 needs the person's answer."
            requests = input_requests(await self._pending(run.id))
            result = None
        elif status == "completed":
            message, requests = None, None
            result = tool_result(await self._result(run))
        else:
            message, requests, result = None, None, None
        return GetTaskResult(
            task_id=str(run.id),
            status=status,
            status_message=message,
            created_at=when(run.created_at),
            last_updated_at=when(run.updated_at),
            result=result,
            input_requests=requests,
        )

    async def _update(
        self, ctx: ServerRequestContext[Any, Any], params: UpdateTaskParams
    ) -> Ack:
        self._check(ctx, params.task_id)
        run = await self._run(params.task_id)
        # Keys not outstanding are ignored (SEP-2663); an answer that doesn't fit its request
        # is the client's error
        for input_id, kind, request in await self._pending(run.id):
            answer = gen9_answer(input_id, kind, request, params.input_responses)
            if answer is None:
                continue
            try:
                await self._answer(run, input_id, answer)
            except ValueError as e:
                raise MCPError(code=INVALID_PARAMS, message=str(e)) from None
        return Ack()

    async def _cancel(
        self, ctx: ServerRequestContext[Any, Any], params: CancelTaskParams
    ) -> Ack:
        self._check(ctx, params.task_id)
        run = await self._run(params.task_id)
        if STATUS.get(run.status) in ("working", "input_required"):
            await self._stop(run)
        return Ack()
