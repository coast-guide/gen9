"""Gen9 over AG-UI (docs/plans/harness.md, milestone 6; Decision Log): `POST /v1/agui`
takes AG-UI's `RunAgentInput` and answers with its events over SSE, for AG-UI frontends (CopilotKit
and others) holding a token for this API.

- **The run** is a Gen9 run like any other, executed by the workers. This endpoint translates its
  durable event log (runs/log.py) into AG-UI events with the protocol's own SDK
  (`ag-ui-protocol` 1.0): text messages, tool calls with their results, the plan as state, and
  the run's end.
- **The thread** is a Gen9 chat: its id, made on first use as the caller's (another person's is
  404). The last user message is the new message; Gen9 keeps the history. `forwardedProps`
  carries Gen9's own options: `permissionMode` (`ask` or `auto`), as in a chat.
- **Human in the loop:** a run that waits for the person ends the AG-UI run with an interrupt per
  request (its id, its kind as `reason`, the body its answer takes as `response_schema`). The next
  run's `resume` answers them (`resolved` with that body) or stops the run (`cancelled`), and the
  stream follows the run from where it was.
"""

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from ag_ui.core import (
    BaseEvent,
    Interrupt,
    RunAgentInput,
    RunErrorEvent,
    RunFinishedCancelledOutcome,
    RunFinishedEvent,
    RunFinishedInterruptOutcome,
    RunFinishedSuccessOutcome,
    RunStartedEvent,
    StateSnapshotEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    TextPart,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
    UserMessage,
)
from ag_ui.encoder import EventEncoder
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from ..deps import Session
from ..models import InputRequest, Run, RunEvent, Thread
from ..runs import control, log, store
from ..users import CurrentUser

router = APIRouter(prefix="/v1/agui", tags=["agui"])
# How long the stream waits for the run's log to grow before checking on it again
WAIT_S = 15

# The body each kind of request's answer takes (POST …/inputs/{input}), as JSON Schema
ANSWERS: dict[str, dict[str, Any]] = {
    "question": {
        "type": "object",
        "required": ["answers"],
        "properties": {"answers": {"type": "array", "items": {"type": "string"}}},
    },
    "approval": {
        "type": "object",
        "required": ["decisions"],
        "properties": {
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["type"],
                    "properties": {
                        "type": {"enum": ["approve", "reject"]},
                        "message": {"type": "string"},
                    },
                },
            }
        },
    },
    "elicitation": {
        "type": "object",
        "required": ["responses"],
        "properties": {"responses": {"type": "object"}},
    },
    "retry": {
        "type": "object",
        "required": ["retry"],
        "properties": {"retry": {"const": True}},
    },
}


def text_of(message: UserMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return "\n".join(
        part.text for part in message.content if isinstance(part, TextPart)
    )


def interrupt(request: dict[str, Any]) -> Interrupt:
    """A Gen9 request (an `input.requested` event's data) as an AG-UI interrupt."""
    kind = request.get("kind", "")
    said = {
        "question": next(
            (q.get("question") for q in request.get("questions") or []), None
        ),
        "approval": "Gen9 wants to act, and waits for Allow or Deny",
        "elicitation": "A connector asks for something",
        "retry": request.get("error"),
    }.get(kind)
    return Interrupt(
        id=request["id"],
        reason=kind,
        message=said,
        response_schema=ANSWERS.get(kind),
        metadata={
            "gen9": {k: v for k, v in request.items() if k not in ("id", "kind")}
        },
    )


class Translation:
    """Gen9's run events, one at a time, as AG-UI events: a text message opened on its first
    delta and closed before a tool call or at its end, each tool call as start, arguments and
    end, and its result."""

    def __init__(self) -> None:
        self.open: str | None = None  # the text message being written
        self.written: set[str] = set()  # messages that got their text as deltas

    def close(self) -> list[BaseEvent]:
        if self.open is None:
            return []
        done, self.open = self.open, None
        return [TextMessageEndEvent(message_id=done)]

    def events(self, kind: str, data: dict[str, Any]) -> list[BaseEvent]:
        if kind == "message.delta":
            message = data.get("id") or "answer"
            opened: list[BaseEvent] = []
            if self.open != message:
                opened = [
                    *self.close(),
                    TextMessageStartEvent(message_id=message, role="assistant"),
                ]
                self.open = message
            self.written.add(message)
            return [
                *opened,
                TextMessageContentEvent(message_id=message, delta=data.get("text", "")),
            ]
        if kind == "message.completed":
            message = data.get("id") or "answer"
            if message in self.written or not data.get("text"):
                return self.close()
            # A message that came whole (no deltas): written at once
            return [
                *self.close(),
                TextMessageStartEvent(message_id=message, role="assistant"),
                TextMessageContentEvent(message_id=message, delta=data["text"]),
                TextMessageEndEvent(message_id=message),
            ]
        if kind == "tool.started":
            call = data.get("id") or str(uuid.uuid4())
            return [
                *self.close(),
                ToolCallStartEvent(
                    tool_call_id=call, tool_call_name=data.get("name", "")
                ),
                ToolCallArgsEvent(
                    tool_call_id=call, delta=json.dumps(data.get("args") or {})
                ),
                ToolCallEndEvent(tool_call_id=call),
            ]
        if kind == "tool.completed":
            return [
                ToolCallResultEvent(
                    message_id=str(uuid.uuid4()),
                    tool_call_id=data.get("id") or "",
                    content=data.get("output") or data.get("status", ""),
                    role="tool",
                )
            ]
        if kind == "todos.updated":
            return [StateSnapshotEvent(snapshot={"todos": data.get("todos") or []})]
        if kind == "input.requested":
            return self.close()
        return []


async def follow(
    request: Request,
    run_id: uuid.UUID,
    after: int,
    thread: str,
    agui_run: str,
    stopping: bool = False,
) -> AsyncIterator[BaseEvent]:
    """The run's events after `after`, translated, until it ends or waits for the person. A run
    being stopped (`stopping`) is followed to its end: until its workflow records the stop, it
    still reads as waiting, which would hand the client back the interrupt it just cancelled."""
    engine, hub = request.app.state.engine, request.app.state.event_hub
    translation = Translation()
    yield RunStartedEvent(thread_id=thread, run_id=agui_run)
    while True:
        rows = await log.read_after(engine, run_id, after)
        for seq, kind, data in rows:
            after = seq
            if kind == "run.completed":
                for event in translation.close():
                    yield event
                yield end(data, thread, agui_run)
                return
            for event in translation.events(kind, data):
                yield event
        if rows:
            continue
        open_requests = [] if stopping else await waiting_on(engine, run_id)
        if open_requests:
            for event in translation.close():
                yield event
            yield RunFinishedEvent(
                thread_id=thread,
                run_id=agui_run,
                outcome=RunFinishedInterruptOutcome(
                    interrupts=[interrupt(r) for r in open_requests]
                ),
            )
            return
        if await request.is_disconnected():
            return
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(WAIT_S):
                await hub.wait(run_id)


async def waiting_on(engine: Any, run_id: uuid.UUID) -> list[dict[str, Any]]:
    """What a waiting run asks and nobody answered yet, as its `input.requested` events have
    it; none while it isn't waiting."""
    async with engine.connect() as conn:
        if await conn.scalar(select(Run.status).where(Run.id == run_id)) != "waiting":
            return []
        rows = await conn.execute(
            select(InputRequest.id, InputRequest.kind, InputRequest.request)
            .where(InputRequest.run_id == run_id, InputRequest.response.is_(None))
            .order_by(InputRequest.created_at)
        )
        return [
            store.requested_event(store.Pending(i, k, r)).data
            for i, k, r in rows.tuples()
        ]


def end(completed: dict[str, Any], thread: str, agui_run: str) -> BaseEvent:
    """How the run ended (`run.completed`), as AG-UI says it."""
    ended = completed.get("status")
    if ended == "success":
        return RunFinishedEvent(
            thread_id=thread, run_id=agui_run, outcome=RunFinishedSuccessOutcome()
        )
    if ended == "cancelled":
        return RunFinishedEvent(
            thread_id=thread, run_id=agui_run, outcome=RunFinishedCancelledOutcome()
        )
    return RunErrorEvent(
        message=completed.get("error")
        or (
            "It waited for you too long."
            if ended == "expired"
            else "Gen9 couldn't answer."
        ),
        code=ended,
    )


async def _thread(session: Session, user: CurrentUser, thread_id: str) -> Thread:
    """The caller's chat by the AG-UI thread's id, made on its first use."""
    try:
        wanted = uuid.UUID(thread_id)
    except ValueError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "threadId must be a UUID"
        ) from None
    thread = await session.scalar(
        select(Thread).where(Thread.id == wanted, Thread.deleted_at.is_(None))
    )
    if thread is None:
        if await session.scalar(select(func.count()).where(Thread.id == wanted)):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")
        thread = Thread(id=wanted, user_id=user.id)
        session.add(thread)
        await session.commit()
        await session.refresh(thread)
    if thread.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")
    if thread.parent_id is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This chat is a background task. Talk to it through the chat that started it.",
        )
    return thread


@router.post(
    "",
    summary="Run Gen9 as an AG-UI agent: a RunAgentInput in, its events out (SSE)",
    responses={
        404: {"description": "Another person's thread"},
        409: {"description": "The thread is still answering, or nothing to resume"},
        422: {"description": "No message, or a threadId that isn't a UUID"},
    },
)
async def run_agent(
    body: RunAgentInput, user: CurrentUser, session: Session, request: Request
) -> StreamingResponse:
    thread = await _thread(session, user, body.thread_id)
    engine, temporal = request.app.state.engine, request.app.state.temporal
    stopping = any(entry.status == "cancelled" for entry in body.resume or [])
    if body.resume:
        # The run that waits: its answers, or a stop
        run_id = await session.scalar(
            select(Run.id).where(Run.thread_id == thread.id, Run.status == "waiting")
        )
        if run_id is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Nothing waits to be resumed")
        after = await session.scalar(
            select(func.coalesce(func.max(RunEvent.seq), 0)).where(
                RunEvent.run_id == run_id
            )
        )
        for entry in body.resume:
            if entry.status == "cancelled":
                await control.stop_run(engine, temporal, run_id)
                continue
            try:
                await control.answer(
                    engine,
                    temporal,
                    run_id,
                    entry.interrupt_id,
                    user.sub,
                    entry.payload if isinstance(entry.payload, dict) else {},
                )
            except control.AlreadyAnswered:
                continue
            except (control.InputNotFound, control.NotWaiting, ValueError) as e:
                raise HTTPException(
                    status.HTTP_409_CONFLICT
                    if isinstance(e, control.NotWaiting)
                    else status.HTTP_422_UNPROCESSABLE_CONTENT,
                    str(e) or type(e).__name__,
                ) from None
    else:
        asked = next(
            (m for m in reversed(body.messages) if isinstance(m, UserMessage)), None
        )
        message = text_of(asked).strip() if asked else ""
        if not message:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Send a user message to run"
            )
        # Gen9's own options travel in forwardedProps: permissionMode, as the chat's mode from
        # this message on (approvals.py)
        props = body.forwarded_props if isinstance(body.forwarded_props, dict) else {}
        mode = props.get("permissionMode")
        if mode not in ("ask", "auto"):
            mode = thread.permission_mode
        try:
            run_id = await control.start_run(
                engine,
                temporal,
                thread.id,
                user.sub,
                {"message": message, "permission_mode": mode},
                wait_s=request.app.state.runtime.settings.run_wait_s,
            )
        except store.ActiveRunExists:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This chat is still answering. Wait or stop it first.",
            ) from None
        after = 0
    encoder = EventEncoder(accept=request.headers.get("accept", "text/event-stream"))
    # No connection held while the run streams: the request's session outlives the stream
    # (threads.owned_thread, P4-E3)
    await session.commit()

    async def stream() -> AsyncIterator[str]:
        async for event in follow(
            request, run_id, after or 0, body.thread_id, body.run_id, stopping
        ):
            yield encoder.encode(event)

    return StreamingResponse(stream(), media_type=encoder.get_content_type())
