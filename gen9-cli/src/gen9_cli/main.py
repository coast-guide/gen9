"""`gen9`: sign in and ask Gen9 from the terminal.

gen9 login          sign in on a browser with a one-time code (Device Authorization Grant)
gen9 whoami         who you're signed in as
gen9 ask "..."      ask Gen9; the answer streams in (--thread ID to continue a chat); a question
                    Gen9 asks mid-task is asked here, and answered from the terminal;
                    --attach PATH: a file for the chat's environment (/work/in);
                    --ask-first: actions wait for your Allow (the chat keeps it); after a
                    failure someone can fix (the model provider down), it offers Retry; if
                    the connection drops, it reconnects and goes on where it was
gen9 search "..."   search your past chats (--mode hybrid|keyword|semantic|fuzzy, --limit N)
gen9 files CHAT     the files a chat's environment shared; with a name, download it (-o PATH)
gen9 logout         sign this terminal out (revokes its token; the browser stays signed in)

Async throughout (AGENTS.md): one `asyncio.run` at the entry point, `httpx2.AsyncClient` for every
request. Ctrl-C cancels the main task (asyncio.run's SIGINT handling), which `gen9 ask` turns
into a cancel of the run on the server.
"""

import argparse
import asyncio
import json
import os
import stat
import sys
import traceback
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx2

from . import approvals, elicitation, questions
from .auth import (
    Keycloak,
    SignInFailed,
    SignInRequired,
    access_token,
    config_dir,
    forget_tokens,
    load_tokens,
    save_tokens,
)

ISSUER = os.environ.get("GEN9_ISSUER", "http://localhost:15000/realms/gen9")
API = os.environ.get("GEN9_API", "http://localhost:17000")
# Files one message may name (gen9-agent's RunIn.files)
MAX_ATTACHMENTS = 10
# What a person is told on signing in, before their first question (the web app says the same)
AI_NOTICE = (
    "Gen9 is an AI system and can be wrong. Check its work before you rely on it."
)
# Tries to reconnect to a run's events in a row, 1, 2, 4, 8 and 16 s apart (Codex retries a
# stream 5 times: its `stream_max_retries`)
RECONNECTS = 5
# How often Ctrl-C looks for a run it hasn't heard of yet, half a second apart
STOP_LOOKUPS = 6


def show_code(grant: dict) -> None:
    print("To sign in, open this page on any device and confirm the code:\n")
    print(f"  {grant['verification_uri_complete']}\n")
    print(f"Or go to {grant['verification_uri']} and enter {grant['user_code']}.")
    print(
        f"The code works for {grant['expires_in'] // 60} minutes. Waiting…", flush=True
    )


async def api_request(
    keycloak: Keycloak, http: httpx2.AsyncClient, method: str, path: str, **kwargs
) -> httpx2.Response:
    """Calls gen9-agent with the user's token; refreshes once if the API says it expired."""
    extra = kwargs.pop("headers", {})
    for attempt in range(2):
        headers = {**extra, "Authorization": f"Bearer {await access_token(keycloak)}"}
        response = await http.request(method, f"{API}{path}", headers=headers, **kwargs)
        if response.status_code != 401 or attempt:
            return response
        await keycloak.refresh(await load_tokens())
    return response


async def sse_events(
    response: httpx2.Response,
) -> AsyncIterator[tuple[str, dict, str | None]]:
    """Server-sent events (event, JSON data, id), per the WHATWG spec's line format."""
    event, data, event_id = "message", [], None
    async for line in response.aiter_lines():
        if line == "":
            if data:
                yield event, json.loads("\n".join(data)), event_id
            event, data, event_id = "message", [], None
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif line.startswith("id:"):
            event_id = line[3:].strip()


async def cmd_login(keycloak: Keycloak, http: httpx2.AsyncClient, args) -> int:
    await save_tokens(await keycloak.device_sign_in(show_code))
    signed_in = await cmd_whoami(keycloak, http, args)
    # Told before the first question, as the web app does (AI Act Art. 50(1) and (5))
    print(AI_NOTICE)
    return signed_in


async def cmd_whoami(keycloak: Keycloak, http: httpx2.AsyncClient, _args) -> int:
    me = await api_request(keycloak, http, "GET", "/v1/me")
    me.raise_for_status()
    body = me.json()
    print(
        f"Signed in as {body.get('name') or body.get('email')} ({body.get('email')})."
    )
    return 0


async def answer_here(
    keycloak: Keycloak,
    http: httpx2.AsyncClient,
    run_path: str,
    request: dict,
    terminal: questions.Terminal,
) -> bool:
    """Ask Gen9's question, its request to act, a connector's request, or whether to retry after a failure, in the
    terminal, and send the answer; False if nobody answered (the input ended). Not retrying
    stops the run."""
    path = f"{run_path}/inputs/{request['id']}"
    while True:
        if request.get("kind") == "retry":
            again = await questions.retry_or_stop(request, terminal)
            if again is None:
                return False
            if not again:
                await api_request(keycloak, http, "POST", f"{run_path}/cancel")
                return True
            body = {"retry": True}
        elif request.get("kind") == "approval":
            decisions = await approvals.decide(request, terminal)
            body = None if decisions is None else {"decisions": decisions}
        elif request.get("kind") == "elicitation":
            responses = await elicitation.respond(request, terminal)
            body = None if responses is None else {"responses": responses}
        else:
            answers = await questions.ask(request, terminal)
            body = None if answers is None else {"answers": answers}
        if body is None:
            return False
        response = await api_request(keycloak, http, "POST", path, json=body)
        if response.status_code == 422:
            print(response.json().get("detail"), file=sys.stderr)
            continue
        if response.status_code == 409:
            print(f"({response.json().get('detail')})", file=sys.stderr)
        else:
            response.raise_for_status()
        return True


async def cmd_ask(keycloak: Keycloak, http: httpx2.AsyncClient, args) -> int:
    # At most MAX_ATTACHMENTS files, as gen9-agent's runs take: said before any call
    if len(args.attach or []) > MAX_ATTACHMENTS:
        print(
            f"A message can carry at most {MAX_ATTACHMENTS} files; attach the rest to the next one.",
            file=sys.stderr,
        )
        return 1
    thread_id = args.thread
    if thread_id is not None and not thread_id.strip():
        # An empty --thread (a script's variable unset) mustn't start a new chat silently
        print(
            "--thread needs a chat's id: `gen9 ask` prints it after each answer.",
            file=sys.stderr,
        )
        return 1
    # What the chat's environment shared this turn (its files.shared events)
    shared: list[dict] = []
    if not thread_id:
        created = await api_request(keycloak, http, "POST", "/v1/threads")
        created.raise_for_status()
        thread_id = created.json()["id"]
    # Files for this message: attached to the chat first, then named by the message
    attached: list[str] = []
    for path in args.attach or []:
        file = Path(path)
        try:
            content = await asyncio.to_thread(
                file.read_bytes
            )  # disk reads block: a thread
        except OSError as exc:
            print(f"Can't read {path}: {exc.strerror}", file=sys.stderr)
            return 1
        added = await api_request(
            keycloak,
            http,
            "POST",
            f"/v1/threads/{thread_id}/files",
            params={"name": file.name},
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )
        if added.status_code in (413, 422, 503):
            print(f"{file.name}: {added.json().get('detail')}", file=sys.stderr)
            return 1
        added.raise_for_status()
        attached.append(added.json()["id"])
    run_id = None
    # The last event shown: a reconnect replays the run's events after it (Last-Event-ID)
    last_id: str | None = None
    terminal: questions.Terminal | None = None

    async def follow(response: httpx2.Response) -> int | None:
        """Show the run's events; the exit code once it is done here, None if the stream ended
        first."""
        nonlocal run_id, last_id, terminal
        async for event, data, event_id in sse_events(response):
            last_id = event_id or last_id
            if event == "run.queued":
                run_id = data["run_id"]
            elif event == "run.started" and data.get("attempt", 1) > 1:
                print(
                    "\n(The agent restarted and is answering again…)",
                    file=sys.stderr,
                )
            elif event == "message.delta":
                print(data["text"], end="", flush=True)
            elif event == "status":
                print(f"({data['text']}…)", file=sys.stderr, flush=True)
            elif event == "files.shared":
                shared.extend(data.get("files") or [])
            elif event == "input.requested" and data.get("kind") in (
                "question",
                "approval",
                "elicitation",
                "retry",
            ):
                terminal = terminal or questions.Terminal()
                run_path = f"/v1/threads/{thread_id}/runs/{run_id}"
                if not await answer_here(keycloak, http, run_path, data, terminal):
                    print(
                        "\nGen9 is waiting for your answer: answer it in the web app, or "
                        "stop the chat there.",
                        file=sys.stderr,
                    )
                    return 1
            elif event == "run.completed":
                if data["status"] != "success":
                    print(f"\n{data.get('error') or 'Stopped.'}", file=sys.stderr)
                    return 1
                return 0
        return None

    try:
        try:
            async with http.stream(
                "POST",
                f"{API}/v1/threads/{thread_id}/runs/stream",
                json={
                    "message": args.message,
                    # From this message on, actions wait for the person's Allow
                    **({"permission_mode": "ask"} if args.ask_first else {}),
                    **({"files": attached} if attached else {}),
                },
                headers={"Authorization": f"Bearer {await access_token(keycloak)}"},
                timeout=None,
            ) as response:
                if response.status_code == 404:
                    print("That chat doesn't exist, or isn't yours.", file=sys.stderr)
                    return 1
                if response.status_code == 409:
                    print(
                        "That chat is still answering. Try again when it's done.",
                        file=sys.stderr,
                    )
                    return 1
                if response.is_error:
                    # A streamed refusal: its reason, for refusal()
                    await response.aread()
                response.raise_for_status()
                done = await follow(response)
        except httpx2.TransportError:
            if run_id is None:
                raise
            done = None
        # The connection dropped before the run ended (the API restarted, the network went):
        # the run goes on without it, so reconnect and replay what came after, as a browser's
        # EventSource does (WHATWG HTML, server-sent events)
        failures = 0
        while done is None:
            if failures == RECONNECTS:
                print(
                    "\nLost the connection to Gen9. It goes on answering: see the answer in the "
                    f'web app, or continue this chat: gen9 ask --thread {thread_id} "…"',
                    file=sys.stderr,
                )
                return 1
            if failures == 0:
                print("\n(Lost the connection to Gen9; reconnecting…)", file=sys.stderr)
            await asyncio.sleep(2**failures)
            failures += 1
            seen = last_id
            try:
                async with http.stream(
                    "GET",
                    f"{API}/v1/threads/{thread_id}/runs/{run_id}/stream",
                    headers={
                        "Authorization": f"Bearer {await access_token(keycloak)}",
                        **({"Last-Event-ID": last_id} if last_id else {}),
                    },
                    timeout=None,
                ) as response:
                    if response.status_code >= 500:
                        continue  # the API is starting, or its database isn't back yet
                    if response.is_error:
                        await response.aread()
                    response.raise_for_status()
                    done = await follow(response)
            except httpx2.TransportError:
                pass
            if last_id != seen:
                failures = 0  # it got through: a later drop gets its tries again
        if done:
            return done
    except asyncio.CancelledError:
        # Ctrl-C. The run keeps going on the server unless it's stopped. Uncancel first, so the
        # stop request itself isn't cancelled
        if task := asyncio.current_task():
            task.uncancel()
        # Pressed before the run said its id (its first event): the message may already have
        # started one, which would answer anyway (found by hand: manual-e2e.md, P2-K1). It is
        # the chat's active run with this message, once the API has recorded it
        for _ in range(STOP_LOOKUPS if run_id is None else 0):
            chat = await api_request(keycloak, http, "GET", f"/v1/threads/{thread_id}")
            active = chat.json().get("active_run") if chat.is_success else None
            if active and active.get("message") == args.message:
                run_id = active["id"]
                break
            await asyncio.sleep(0.5)
        if run_id:
            await api_request(
                keycloak, http, "POST", f"/v1/threads/{thread_id}/runs/{run_id}/cancel"
            )
        print("\nStopped.", file=sys.stderr)
        print(f'Continue this chat: gen9 ask --thread {thread_id} "…"', file=sys.stderr)
        return 130
    if shared:
        print(f"\n\n{format_files(shared, thread_id)}", file=sys.stderr)
    print(f'\n\nContinue this chat: gen9 ask --thread {thread_id} "…"', file=sys.stderr)
    return 0


def size_of(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{round(size / 1024)} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def format_files(files: list[dict], thread_id: str) -> str:
    """A chat's files, and how to download each."""
    if not files:
        return "This chat has no files."
    lines = ["Files:"]
    lines += [f"  {f['name']} ({size_of(f['size'])})" for f in files]
    lines.append(f"  gen9 files {thread_id} NAME   downloads one")
    return "\n".join(lines)


def write_new(path: Path, content: bytes) -> None:
    """Writes a file that doesn't exist yet (never over one)."""
    with path.open("xb") as f:
        f.write(content)


async def cmd_files(keycloak: Keycloak, http: httpx2.AsyncClient, args) -> int:
    listed = await api_request(keycloak, http, "GET", f"/v1/threads/{args.chat}/files")
    if listed.status_code == 404:
        print("No such chat.", file=sys.stderr)
        return 1
    listed.raise_for_status()
    files = listed.json()
    if not args.name:
        print(format_files(files, args.chat))
        return 0
    found = next((f for f in files if f["name"] == args.name), None)
    if found is None:
        print(f"This chat has no file {args.name}.", file=sys.stderr)
        return 1
    got = await api_request(
        keycloak, http, "GET", f"/v1/threads/{args.chat}/files/{found['id']}"
    )
    got.raise_for_status()
    target = Path(args.output or args.name.rsplit("/", 1)[-1])
    try:
        # Writing to disk blocks: in a thread
        await asyncio.to_thread(write_new, target, got.content)
    except FileExistsError:
        print(f"{target} exists; name another with -o.", file=sys.stderr)
        return 1
    print(f"Saved {target} ({size_of(len(got.content))}).")
    return 0


def format_hits(hits: list[dict]) -> str:
    """Each chat found: its title, what matched, and how to continue it."""
    if not hits:
        return "No chats found."
    lines = []
    for hit in hits:
        lines.append(hit["title"])
        if hit.get("snippet"):
            lines.append(f"  {hit['snippet']}")
        lines.append(f'  gen9 ask --thread {hit["thread_id"]} "…"')
    return "\n".join(lines)


async def cmd_search(keycloak: Keycloak, http: httpx2.AsyncClient, args) -> int:
    response = await api_request(
        keycloak,
        http,
        "GET",
        "/v1/search",
        params={"q": args.query, "mode": args.mode, "limit": args.limit},
    )
    if response.status_code in (429, 503):  # over budget, or search by meaning is down
        print(response.json().get("detail"), file=sys.stderr)
        return 1
    response.raise_for_status()
    print(format_hits(response.json()))
    return 0


EVERY = {
    "hour": "hourly",
    "day": "daily",
    "weekday": "weekdays",
    "week": "weekly",
    "once": "once",
}
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def local_zone() -> str:
    """This terminal's time zone, as an IANA name: TZ, or where /etc/localtime points."""
    if zone := os.environ.get("TZ", "").lstrip(":"):
        return zone
    try:
        target = os.readlink("/etc/localtime")
    except OSError:
        return "UTC"
    return target.split("zoneinfo/", 1)[-1] if "zoneinfo/" in target else "UTC"


OUTCOME_WORDS = {
    "satisfied": "meets its rubric",
    "needs_revision": "short of its rubric",
    "failed": "its rubric doesn't apply",
}


def when(iso: str, zone: str) -> str:
    """An API time (UTC, ISO 8601) as the terminal's wall clock, to the minute: 2026-09-27 09:00."""
    moment = datetime.fromisoformat(iso)
    try:
        moment = moment.astimezone(ZoneInfo(zone))
    except (ZoneInfoNotFoundError, ValueError):
        moment = moment.astimezone(UTC)
    return moment.strftime("%Y-%m-%d %H:%M")


def format_tasks(tasks: list[dict], zone: str = "UTC") -> str:
    """Each task: its name and state, when it runs, and when next, in the terminal's time zone."""
    if not tasks:
        return 'Nothing scheduled. gen9 tasks add --every day --at 09:00 NAME "…"'
    lines = []
    for task in tasks:
        state = "" if task["status"] == "active" else f" ({task['status']})"
        lines.append(f"{task['name']}{state}")
        lines.append(f"  {task['schedule_words']}")
        if task.get("next_at"):
            lines.append(f"  next: {when(task['next_at'], zone)}")
        if task.get("rubric"):
            lines.append(
                f"  checked against a rubric, {task['max_iterations']} tries at most"
            )
        if task["runs"]:
            last = task["runs"][0]
            said = last["status"] or "queued"
            if last.get("checking"):
                said = "checking against its rubric"
            elif last.get("outcome"):
                tries = last["graded"]
                said += f", {OUTCOME_WORDS[last['outcome']]} in {tries} tr{'y' if tries == 1 else 'ies'}"
            lines.append(
                f"  last: {when(last['created_at'], zone)} {said}, chat {last['thread_id']}"
            )
    return "\n".join(lines)


def schedule_of(args) -> dict:
    kind = EVERY[args.every]
    schedule: dict = {"kind": kind, "time": args.at}
    if kind == "weekly":
        schedule["weekday"] = WEEKDAYS.index(args.on)
    if kind == "once":
        schedule["date"] = args.on
    return schedule


async def _find_task(
    keycloak: Keycloak, http: httpx2.AsyncClient, which: str
) -> dict | None:
    listed = await api_request(keycloak, http, "GET", "/v1/tasks")
    listed.raise_for_status()
    for task in listed.json():
        if task["id"] == which or task["name"].lower() == which.lower():
            return task
    return None


async def cmd_tasks(keycloak: Keycloak, http: httpx2.AsyncClient, args) -> int:
    if args.action in (None, "list"):
        listed = await api_request(keycloak, http, "GET", "/v1/tasks")
        listed.raise_for_status()
        print(format_tasks(listed.json(), local_zone()))
        return 0
    if args.action == "add":
        if args.every == "week" and args.on not in WEEKDAYS:
            print(f"--on is a day: {', '.join(WEEKDAYS)}.", file=sys.stderr)
            return 1
        if args.every == "once" and not args.on:
            print("--on is the date, YYYY-MM-DD.", file=sys.stderr)
            return 1
        made = await api_request(
            keycloak,
            http,
            "POST",
            "/v1/tasks",
            json={
                "name": args.name,
                "prompt": args.message,
                "schedule": schedule_of(args),
                # Reading where /etc/localtime points touches the disk: in a thread
                "time_zone": args.tz or await asyncio.to_thread(local_zone),
                "permission_mode": "ask" if args.ask_first else "auto",
                **({"rubric": args.done_when} if args.done_when else {}),
                "max_iterations": args.tries,
            },
        )
        if made.status_code in (409, 422):
            print(made.json().get("detail"), file=sys.stderr)
            return 1
        made.raise_for_status()
        print(format_tasks([made.json()], local_zone()))
        return 0
    task = await _find_task(keycloak, http, args.task)
    if task is None:
        print(f"No task {args.task}.", file=sys.stderr)
        return 1
    method, path, said = {
        "run": ("POST", "/run", "Running it now, in a new chat."),
        "pause": ("POST", "/pause", "Paused."),
        "resume": ("POST", "/resume", "Resumed."),
        "delete": ("DELETE", "", "Deleted. Its chats stay."),
    }[args.action]
    done = await api_request(keycloak, http, method, f"/v1/tasks/{task['id']}{path}")
    if done.status_code == 409:
        print(done.json().get("detail"), file=sys.stderr)
        return 1
    done.raise_for_status()
    print(said)
    return 0


async def cmd_logout(keycloak: Keycloak, _http: httpx2.AsyncClient, _args) -> int:
    try:
        await keycloak.revoke(await load_tokens())
    except SignInRequired:
        pass
    await forget_tokens()
    print("Signed out of this terminal.")
    return 0


COMMANDS = {
    "login": cmd_login,
    "whoami": cmd_whoami,
    "ask": cmd_ask,
    "search": cmd_search,
    "files": cmd_files,
    "tasks": cmd_tasks,
    "logout": cmd_logout,
}


def _save_details(exc: BaseException) -> Path:
    """The traceback of an error gen9 didn't expect, in a file only the person can read, next to
    their credentials (0600 in a 0700 folder), so the terminal gets one line instead of it
    (clig.dev, "Errors": debug information, without overwhelming them). The last one replaces the
    one before."""
    path = config_dir() / "last-error.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(stat.S_IRWXU)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(
            f"gen9 {' '.join(sys.argv[1:2])}, {datetime.now(UTC):%Y-%m-%d %H:%M:%S} UTC\n\n"
        )
        file.write("".join(traceback.format_exception(exc)))
    path.chmod(0o600)
    return path


async def run(args: argparse.Namespace) -> int:
    async with httpx2.AsyncClient(timeout=30) as http:
        try:
            keycloak = await Keycloak.discover(ISSUER, http)
            return await COMMANDS[args.command](keycloak, http, args)
        except SignInRequired:
            print(
                "You're not signed in, or your sign-in ended. Run `gen9 login`.",
                file=sys.stderr,
            )
        except SignInFailed as exc:
            print(exc, file=sys.stderr)
        except httpx2.HTTPStatusError as exc:
            # Gen9 answered, and refused: say why, in its words
            print(
                f"Gen9 said no ({exc.response.status_code}): {refusal(exc.response)}",
                file=sys.stderr,
            )
        except httpx2.HTTPError as exc:
            print(f"Couldn't reach Gen9: {exc}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 (the last resort, below)
            # The last resort: one line in the terminal, never a raw traceback (manual-e2e.md,
            # P6-B2; an answer that wasn't JSON printed Python's whole traceback)
            where = await asyncio.to_thread(_save_details, exc)
            print(
                f"Something went wrong that gen9 didn't expect ({type(exc).__name__}). The details"
                f" are in {where}: if it happens again, report it with that file.",
                file=sys.stderr,
            )
        except asyncio.CancelledError:
            # Ctrl-C before anything needed stopping on the server
            if task := asyncio.current_task():
                task.uncancel()
            print("\nStopped.", file=sys.stderr)
            return 130
        return 1


def refusal(response: httpx2.Response) -> str:
    """Why the API refused a request: FastAPI's `detail`, a sentence or a list of what's invalid."""
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        return "; ".join(
            f"{'.'.join(str(p) for p in d.get('loc', [])[1:])}: {d.get('msg', '')}".lstrip(
                ": "
            )
            for d in detail
            if isinstance(d, dict)
        )
    return response.reason_phrase or "no reason given"


def limit(value: str) -> int:
    """--limit: a whole number the API takes (1 to 50)."""
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("give a whole number from 1 to 50") from None
    if not 1 <= number <= 50:
        raise argparse.ArgumentTypeError("give a whole number from 1 to 50")
    return number


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    """The command line, with a message or query of several words joined: quotes are optional."""
    parser = argparse.ArgumentParser(prog="gen9", description="Gen9 from the terminal.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("login", help="sign in on a browser with a one-time code")
    commands.add_parser("whoami", help="who you're signed in as")
    ask = commands.add_parser("ask", help="ask Gen9; the answer streams in")
    ask.add_argument("message", nargs="+")
    ask.add_argument("--thread", help="continue this chat")
    ask.add_argument(
        "--attach",
        action="append",
        metavar="PATH",
        help="attach a file (again for more): it's put in the chat's environment, in /work/in",
    )
    ask.add_argument(
        "--ask-first",
        action="store_true",
        help="ask before acting: actions wait for your Allow (the chat keeps it)",
    )
    search = commands.add_parser("search", help="search your past chats")
    search.add_argument("query", nargs="+")
    search.add_argument(
        "--mode",
        choices=["hybrid", "keyword", "semantic", "fuzzy"],
        default="hybrid",
        help="by words and meaning (default), words, meaning, or a misspelled title",
    )
    search.add_argument(
        "--limit", type=limit, default=5, help="at most this many (1-50)"
    )
    files = commands.add_parser(
        "files", help="a chat's files; with a name, download it"
    )
    files.add_argument("chat", help="the chat's id (gen9 ask prints it)")
    files.add_argument("name", nargs="?", help="the file to download")
    files.add_argument(
        "-o", "--output", help="where to save it (default: its name, here)"
    )
    tasks = commands.add_parser(
        "tasks", help="what Gen9 runs on its own; with no action, list them"
    )
    task_actions = tasks.add_subparsers(dest="action")
    task_actions.add_parser("list", help="your scheduled tasks")
    add = task_actions.add_parser("add", help="schedule a task")
    add.add_argument("name")
    add.add_argument("message", help="what Gen9 should do, as you'd ask it")
    add.add_argument("--every", choices=list(EVERY), required=True)
    add.add_argument(
        "--at", default="09:00", help="HH:MM (for --every hour, the minutes count)"
    )
    add.add_argument("--on", help="a day (mon…sun) for --every week, a date for once")
    add.add_argument("--tz", help="IANA time zone (default: this terminal's)")
    add.add_argument(
        "--ask-first", action="store_true", help="its runs ask before acting"
    )
    add.add_argument(
        "--done-when",
        help="a rubric (Markdown criteria) each run is checked against; short of it, "
        "Gen9 tries again in the same chat",
    )
    add.add_argument(
        "--tries", type=int, default=3, help="with --done-when: runs at most (1-20)"
    )
    for action, say in [
        ("run", "run it now, in a new chat"),
        ("pause", "pause it"),
        ("resume", "resume it"),
        ("delete", "delete it (its chats stay)"),
    ]:
        one = task_actions.add_parser(action, help=say)
        one.add_argument("task", help="its name or id")
    commands.add_parser("logout", help="sign this terminal out")
    args = parser.parse_args(argv)
    for words in ("message", "query"):
        if isinstance(getattr(args, words, None), list):
            setattr(args, words, " ".join(getattr(args, words)))
    return args


def main() -> None:
    sys.exit(asyncio.run(run(parse())))
