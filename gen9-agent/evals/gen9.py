"""Gen9 over its API, as the seeded user's client: the routes the web app and gen9-cli call. A
trial opens a chat, sends each message as a run, follows the run's events until it ends, and
answers what the run asks the person as its task says.

The tokens come from `e2e/token.mjs`, which signs the seeded user in to gen9-cli in a private
config directory (`GEN9_CONFIG_DIR`). They're refreshed here as gen9-cli refreshes them.
"""

import asyncio
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from itertools import takewhile
from pathlib import Path
from typing import Any, Self

import httpx
from httpx_sse import aconnect_sse

API = os.environ.get("GEN9_API", "http://localhost:17000").rstrip("/")
ISSUER = os.environ.get("GEN9_ISSUER", "http://localhost:15000/realms/gen9")
APP = os.environ.get("APP_URL", "http://localhost:14000").rstrip("/")
CLIENT_ID = "gen9-cli"
# A turn may think, search and run code for minutes before its next event
TURN_S = 900
# A chat's background tasks, from its turn to their notices' answers
SETTLE_S = 900
# Text files a grader may read, up to this size
TEXT_FILE_BYTES = 64_000

# What the harness answers to a run's request (an `input.requested` event's data); None stops the
# run
Answerer = Callable[[dict[str, Any]], dict[str, Any] | None]


class Credentials:
    """The seeded user's tokens in `credentials.json`, as gen9-cli keeps them. Keycloak rotates
    refresh tokens and refuses a reused one (`revokeRefreshToken`), so refreshes happen one at a
    time and each new pair is saved for whoever reads the file next. Use one instance per event
    loop: Langfuse's runner has a loop of its own."""

    def __init__(self, config_dir: Path) -> None:
        self.path = config_dir / "credentials.json"
        self.lock = asyncio.Lock()
        self.tokens: dict[str, Any] | None = None

    async def access_token(self, http: httpx.AsyncClient) -> str:
        async with self.lock:
            if self.tokens is None:
                self.tokens = json.loads(await asyncio.to_thread(self.path.read_text))
            if self.tokens["expires_at"] - time.time() < 60:
                self.tokens = await self._refresh(http, self.tokens["refresh_token"])
                await asyncio.to_thread(self._save, self.tokens)
            return self.tokens["access_token"]

    async def _refresh(
        self, http: httpx.AsyncClient, refresh_token: str
    ) -> dict[str, Any]:
        config = await http.get(f"{ISSUER}/.well-known/openid-configuration")
        config.raise_for_status()
        response = await http.post(
            config.json()["token_endpoint"],
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLIENT_ID,
            },
        )
        if response.status_code in (400, 401):
            raise RuntimeError("The sign-in ended: run `make evals` again to sign in")
        response.raise_for_status()
        body = response.json()
        return {
            "access_token": body["access_token"],
            "refresh_token": body["refresh_token"],
            "expires_at": time.time() + body["expires_in"],
        }

    def _save(self, tokens: dict[str, Any]) -> None:
        # Mode 600 from the start, and for a file that existed, as gen9-cli writes it
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)  # before the tokens are written
        with os.fdopen(fd, "w") as file:
            json.dump(tokens, file)


@dataclass
class Turn:
    """One message and the run that answered it, as the person's client saw them."""

    message: str
    run_id: str | None
    status: str  # how the run ended: success, error, cancelled or expired
    error: str | None = None
    # The turn's last answer
    answer: str = ""
    # The tools the agent used: name, status, and the pages or chats it consulted
    steps: list[dict[str, Any]] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    # What the run asked the person, and what the harness answered
    requests: list[dict[str, Any]] = field(default_factory=list)
    # Earlier messages were summarized to make room
    summarized: bool = False
    # The run's own time, from a worker starting it to its end: queueing is left out
    seconds: float | None = None


@dataclass
class Trial:
    """One attempt at a task: its chat, its turns, and what it left behind."""

    tag: str
    chat: str = ""
    turns: list[Turn] = field(default_factory=list)
    # The chat's messages at the end, a background task's notice and answer included
    messages: list[dict[str, Any]] = field(default_factory=list)
    # Chats the task set up before the trial (past chats to find)
    earlier: list[str] = field(default_factory=list)
    # What Gen9 remembers about the person afterwards
    memory: str = ""
    # The chat's text files: name to content
    files: dict[str, str] = field(default_factory=dict)
    # Why the trial couldn't finish (the harness, not the agent), if it couldn't
    error: str | None = None

    @property
    def answer(self) -> str:
        """The chat's last answer: after a background task, the answer to its notice."""
        said = [m["content"] for m in self.messages if m["role"] == "assistant"]
        return said[-1] if said else (self.turns[-1].answer if self.turns else "")

    @property
    def steps(self) -> list[dict[str, Any]]:
        return [s for t in self.turns for s in t.steps]

    @property
    def requests(self) -> list[dict[str, Any]]:
        return [r for t in self.turns for r in t.requests]


def _seconds(run: dict[str, Any]) -> float | None:
    if not run.get("started_at") or not run.get("finished_at"):
        return None
    started = datetime.fromisoformat(run["started_at"])
    return (datetime.fromisoformat(run["finished_at"]) - started).total_seconds()


def _step(step: dict[str, Any], outputs: dict[str, str]) -> dict[str, Any]:
    """A tool the agent used, with what it returned (from its `tool.completed` event: the API
    keeps up to 2,000 characters), so a trial can be read after its chat is gone."""
    return {
        "name": step["name"],
        "status": step.get("status"),
        "sources": [s["url"] for s in step.get("sources") or []],
        **({"task": step["task"]} if step.get("task") else {}),
        **({"output": outputs[step["id"]]} if step.get("id") in outputs else {}),
    }


class Gen9:
    """Gen9's API as the seeded user. Open it in the event loop that uses it."""

    def __init__(self, credentials: Credentials) -> None:
        self.credentials = credentials
        self.http: httpx.AsyncClient

    async def __aenter__(self) -> Self:
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10))
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.http.aclose()

    async def _headers(self) -> dict[str, str]:
        token = await self.credentials.access_token(self.http)
        return {"Authorization": f"Bearer {token}"}

    async def call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self.http.request(
            method, f"{API}{path}", headers=await self._headers(), **kwargs
        )
        response.raise_for_status()
        return response.json() if response.content else None

    async def start_chat(self) -> str:
        return (await self.call("POST", "/v1/threads"))["id"]

    async def delete_chat(self, chat: str) -> None:
        await self.call("DELETE", f"/v1/threads/{chat}")

    async def send(self, chat: str, message: str, mode: str, answer: Answerer) -> Turn:
        """Send `message` as a run and follow its events until it ends, answering what it asks.
        A dropped stream is resumed after the last event seen."""
        run_id: str | None = None
        seq = 0
        ended: dict[str, Any] | None = None
        requests: list[dict[str, Any]] = []
        outputs: dict[str, str] = {}
        for _ in range(5):
            headers = await self._headers()
            if run_id is None:
                opened = aconnect_sse(
                    self.http,
                    "POST",
                    f"{API}/v1/threads/{chat}/runs/stream",
                    headers=headers,
                    json={"message": message, "permission_mode": mode},
                    timeout=httpx.Timeout(TURN_S, connect=10),
                )
            else:
                opened = aconnect_sse(
                    self.http,
                    "GET",
                    f"{API}/v1/threads/{chat}/runs/{run_id}/stream",
                    headers={**headers, "Last-Event-ID": str(seq)},
                    timeout=httpx.Timeout(TURN_S, connect=10),
                )
            try:
                async with opened as source:
                    source.response.raise_for_status()
                    async for event in source.aiter_sse():
                        seq = int(event.id) if event.id.isdigit() else seq
                        data = json.loads(event.data) if event.data else {}
                        if event.event == "run.queued":
                            run_id = data.get("run_id")
                        elif event.event == "tool.completed" and data.get("id"):
                            outputs[data["id"]] = str(data.get("output") or "")
                        elif event.event == "input.requested" and run_id:
                            requests.append(
                                await self._answer(chat, run_id, data, answer)
                            )
                        elif event.event == "run.completed":
                            ended = data
                            break
            except httpx.TransportError:
                pass  # resumed below, if the run was accepted
            if ended is not None or run_id is None:
                break
        if ended is None or run_id is None:
            return Turn(message, run_id, "lost", "the run's events stopped")
        return await self._turn(chat, message, run_id, ended, requests, outputs)

    async def _answer(
        self, chat: str, run_id: str, request: dict[str, Any], answer: Answerer
    ) -> dict[str, Any]:
        reply = answer(request)
        if reply is None:
            await self.call("POST", f"/v1/threads/{chat}/runs/{run_id}/cancel")
        else:
            await self.call(
                "POST",
                f"/v1/threads/{chat}/runs/{run_id}/inputs/{request['id']}",
                json=reply,
            )
        asked = {k: v for k, v in request.items() if k != "id"}
        return {"kind": request.get("kind"), "asked": asked, "reply": reply}

    async def _turn(
        self,
        chat: str,
        message: str,
        run_id: str,
        ended: dict[str, Any],
        requests: list[dict[str, Any]],
        outputs: dict[str, str],
    ) -> Turn:
        thread = await self.call("GET", f"/v1/threads/{chat}")
        run = await self.call("GET", f"/v1/threads/{chat}/runs/{run_id}")
        messages = thread["messages"]
        # The person's message names the run that answered it; its answers follow it
        start = next(
            (
                i
                for i, m in enumerate(messages)
                if m["role"] == "user" and m.get("run_id") == run_id
            ),
            None,
        )
        answers = (
            list(takewhile(lambda m: m["role"] == "assistant", messages[start + 1 :]))
            if start is not None
            else []
        )
        return Turn(
            message=message,
            run_id=run_id,
            status=ended.get("status") or "error",
            error=ended.get("error"),
            answer=answers[-1]["content"] if answers else "",
            steps=[_step(s, outputs) for a in answers for s in a.get("steps") or []],
            citations=[c["url"] for a in answers for c in a.get("citations") or []],
            requests=requests,
            summarized=any(a.get("summarized") for a in answers),
            seconds=_seconds(run),
        )

    async def settle(self, chat: str) -> dict[str, Any]:
        """The chat once its background tasks ended, their ends were told, and nothing runs in
        it any more."""
        deadline = time.monotonic() + SETTLE_S
        while True:
            thread = await self.call("GET", f"/v1/threads/{chat}")
            tasks = thread.get("tasks") or []
            if all(t.get("told") for t in tasks) and not thread.get("active_run"):
                return thread
            if time.monotonic() > deadline:
                raise TimeoutError("the chat's background tasks didn't settle")
            await asyncio.sleep(3)

    async def text_files(self, chat: str) -> dict[str, str]:
        files: dict[str, str] = {}
        for f in await self.call("GET", f"/v1/threads/{chat}/files"):
            text = (f.get("media_type") or "").startswith("text/")
            if text and (f.get("size") or 0) <= TEXT_FILE_BYTES:
                response = await self.http.get(
                    f"{API}/v1/threads/{chat}/files/{f['id']}",
                    headers=await self._headers(),
                )
                response.raise_for_status()
                files[f["name"]] = response.text
        return files

    async def searchable(self, words: str, chat: str, wait_s: float = 90) -> bool:
        """Whether a keyword search finds `words` in `chat`, waiting for it to be indexed."""
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            hits = await self.call(
                "GET", "/v1/search", params={"q": words, "mode": "keyword"}
            )
            if any(h.get("thread_id") == chat for h in hits or []):
                return True
            await asyncio.sleep(2)
        return False

    async def memory(self) -> str:
        return (await self.call("GET", "/v1/me/memory")).get("content") or ""

    async def set_memory(self, content: str) -> None:
        await self.call("PUT", "/v1/me/memory", json={"content": content})

    async def controls(self) -> dict[str, bool]:
        return await self.call("GET", "/v1/me/controls")

    async def set_controls(self, controls: dict[str, bool]) -> None:
        await self.call("PUT", "/v1/me/controls", json=controls)
