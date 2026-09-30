"""A chat's environment: an OpenSandbox sandbox (gen9-sandbox) where the agent runs commands and
keeps its files, created on the chat's first command or file (docs/plans/harness.md, Milestone 3).

- **The backend.** With environments on, the agent's default backend is `EnvironmentBackend`. Deep
  Agents' `BaseSandbox` builds every file tool on `aexecute`, which runs in the run's own
  environment. One instance serves every run, so it finds the run's chat in LangGraph's config and
  its person in the run's context (explore/sandbox/NOTES.md).
- **Its life.** The chat's `EnvironmentWorkflow` (workflows/environment.py) creates, renews and
  removes the sandbox. This asks it with the `acquire` Update, at most once a minute per chat.
- **Its network.** Closed but for SANDBOX_EGRESS_ALLOW and the hosts of the person's secrets. Gen9
  reaches the sandbox only through the server (`use_server_proxy`), and the sandbox's own ports
  stay off the network (gen9-sandbox/launch.py).
- **The person's secrets.** OpenSandbox's credential vault adds each to requests to its host, so
  code in the environment never sees it (explore/sandbox/NOTES.md). The worker opens them just
  before writing them to a new environment's vault, and rewrites every running one's when they
  change (`apply_secrets`); an environment it can't update is removed.
- **Its output.** OpenSandbox streams a command's output line by line and doesn't say whether the
  last line ended in a newline; Deep Agents' file tools print one line of JSON, so nothing they
  read is lost.
"""

import asyncio
import base64
import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox
from langgraph.config import get_config
from langgraph.runtime import get_runtime
from opensandbox.config import ConnectionConfig
from opensandbox.exceptions import SandboxApiException, SandboxException
from opensandbox.exceptions.sandbox import SandboxConnectionException
from opensandbox.manager import SandboxManager
from opensandbox.models.execd import ExecutionHandlers, ExecutionInit, RunCommandOpts
from opensandbox.models.filesystem import WriteEntry
from opensandbox.models.sandboxes import (
    Credential,
    CredentialBinding,
    CredentialProxyConfig,
    NetworkPolicy,
    NetworkRule,
    SandboxFilter,
)
from opensandbox.sandbox import Sandbox
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from temporalio.client import (
    Client,
    WithStartWorkflowOperation,
    WorkflowUpdateFailedError,
)
from temporalio.common import (
    SearchAttributePair,
    TypedSearchAttributes,
    WorkflowIDConflictPolicy,
)
from temporalio.service import RPCError, RPCStatusCode

from .models import EnvironmentSecret, User
from .settings import Settings
from .temporal import GEN9_KIND, GEN9_THREAD, GEN9_USER
from .vault import Vault
from .workflows.environment import EnvironmentInput, EnvironmentWorkflow
from .workflows.names import (
    ACQUIRE_UPDATE,
    END_SIGNAL,
    SYSTEM_QUEUE,
    environment_workflow_id,
)

log = logging.getLogger(__name__)

# How often a chat's use reaches its workflow (resetting the idle time, renewing the sandbox)
ACQUIRE_EVERY_S = 60
# A closing environment's workflow completes within seconds; then a new one starts
CLOSING_RETRY_S = 1
CLOSING_RETRIES = 30
# OpenSandbox's states of a sandbox that won't run commands again
GONE = ("Stopping", "Terminated", "Failed")
# A running sandbox that doesn't answer this many pings, this far apart, is gone (`answers`)
PINGS = 3
PING_EVERY_S = 1.5
# What a command may print into the conversation
MAX_OUTPUT = 1_000_000
# The longest a command may run, whatever the agent asks: under the turn's 60 minutes
# (workflows/runs.py), so a command that hangs ends as a command the agent hears about, not as a
# turn that times out and is retried into the same command (found by hand: manual-e2e.md, P2-C4)
MAX_COMMAND_S = 3000
# Metadata that names a sandbox's chat and person at OpenSandbox
THREAD_KEY = "gen9-thread"
USER_KEY = "gen9-user"
# A chat's files in its environment: what the person attached, and what the model shares
IN_DIR = "/work/in"
OUT_DIR = "/work/out"


def connection(settings: Settings) -> ConnectionConfig:
    """How Gen9 reaches OpenSandbox's server: only the server, which proxies to the sandboxes."""
    url = urlsplit(settings.sandbox_url or "")
    return ConnectionConfig(
        domain=url.netloc,
        protocol=url.scheme or "http",
        api_key=settings.sandbox_api_key.get_secret_value()
        if settings.sandbox_api_key
        else None,
        use_server_proxy=True,
    )


@dataclass(frozen=True)
class Secret:
    """A person's secret, opened, as the vault takes it."""

    name: str
    host: str
    path: str
    auth: str  # bearer, header or basic
    header: str | None
    value: str
    methods: str = "read"  # a key of METHODS


# Which requests a secret goes with (the vault binding's `match.methods`). In a chat that acts
# without asking, a command could send anything to its host as the person (P5-C2), so "read" is
# the default: requests that only read, as Codex's internet access offers ("GET, HEAD, and
# OPTIONS"). "all" adds changes. Others reach the host without it.
METHODS = {
    "read": ["GET", "HEAD", "OPTIONS"],
    "all": ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"],
}


def allowed_hosts(settings: Settings, secrets: list[Secret]) -> list[str]:
    """What an environment may reach: the operator's hosts, and those of the person's secrets."""
    return list(
        dict.fromkeys([*settings.sandbox_egress_allow, *(s.host for s in secrets)])
    )


def network_policy(settings: Settings, secrets: list[Secret]) -> NetworkPolicy:
    """Closed but for the hosts allowed."""
    return NetworkPolicy(
        defaultAction="deny",
        egress=[
            NetworkRule(action="allow", target=host)
            for host in allowed_hosts(settings, secrets)
        ],
    )


def vault_entries(
    secrets: list[Secret],
) -> tuple[
    list[Credential | dict[str, object]], list[CredentialBinding | dict[str, object]]
]:
    """The credentials and bindings of the person's secrets: each only on https to its host and
    path, sent as they chose."""
    credentials: list[Credential | dict[str, object]] = []
    bindings: list[CredentialBinding | dict[str, object]] = []
    for s in secrets:
        value = (
            base64.b64encode(s.value.encode()).decode()
            if s.auth == "basic"
            else s.value
        )
        credentials.append(Credential(name=s.name, source={"value": value}))
        auth: dict[str, Any] = (
            {"type": "apiKey", "name": s.header, "credential": s.name}
            if s.auth == "header"
            else {"type": s.auth, "credential": s.name}
        )
        bindings.append(
            CredentialBinding(
                name=s.name,
                match={
                    "schemes": ["https"],
                    "hosts": [s.host],
                    "paths": [s.path],
                    "methods": METHODS[s.methods],
                },
                auth=auth,
            )
        )
    return credentials, bindings


async def secrets_of(
    engine: AsyncEngine, vault: Vault | None, user_sub: str
) -> list[Secret]:
    """A person's secrets, opened; none without the vault's keys."""
    if vault is None:
        return []
    async with engine.connect() as conn:
        rows = list(
            await conn.execute(
                select(EnvironmentSecret)
                .join(User, User.id == EnvironmentSecret.user_id)
                .where(User.sub == user_sub)
                .order_by(EnvironmentSecret.name)
            )
        )
    return [
        Secret(
            r.name,
            r.host,
            r.path,
            r.auth,
            r.header,
            vault.open(r.sealed_value, user_sub, str(r.id), "environment"),
            r.methods,
        )
        for r in rows
    ]


async def secret_hosts_of(engine: AsyncEngine, user_sub: str) -> dict[str, str]:
    """A person's secrets' names and hosts, their values left sealed."""
    async with engine.connect() as conn:
        rows = await conn.execute(
            select(EnvironmentSecret.name, EnvironmentSecret.host)
            .join(User, User.id == EnvironmentSecret.user_id)
            .where(User.sub == user_sub)
        )
        return {name: host for name, host in rows}


async def drifted(settings: Settings, sandbox_id: str, secrets: dict[str, str]) -> bool:
    """Whether a running sandbox's vault or rules no longer match the person's secrets (`secrets`:
    name to host). Its egress sidecar holds both in memory: restarted after a crash, it came back
    with an empty vault, so a secret was no longer sent, and with the rules it was created with,
    so a host taken away since was open again (manual-e2e.md, P4-E4)."""
    sandbox = await Sandbox.connect(
        sandbox_id, connection_config=connection(settings), skip_health_check=True
    )
    try:
        try:
            vault = await sandbox.credential_vault.get()
            held = {c.name for c in vault.credentials}
        except SandboxApiException as e:
            if "404" not in str(e) and "not found" not in str(e).lower():
                raise
            held = set()
        policy = await sandbox.get_egress_policy()
    finally:
        await sandbox.close()
    allowed = {r.target for r in policy.egress or [] if r.action == "allow"}
    wanted = {*settings.sandbox_egress_allow, *secrets.values()}
    return held != set(secrets) or allowed != wanted


def output_of(execution: Any) -> tuple[str, bool]:
    """A command's output as the model reads it: stdout, then stderr, bounded."""

    def text(messages: list[Any]) -> str:
        # An empty line arrives as "\n", every other line without its newline
        return "\n".join("" if m.text == "\n" else m.text for m in messages)

    stdout, stderr = text(execution.logs.stdout), text(execution.logs.stderr)
    output = stdout + ("\n" if stdout and stderr else "") + stderr
    if len(output) > MAX_OUTPUT:
        return output[:MAX_OUTPUT], True
    return output, False


def stopped(why: str) -> str:
    """What the agent is told when the chat's environment stopped, before or during a command."""
    return (
        f"(This chat's environment stopped: {why}. What was in it is gone. The next command "
        "starts a new, empty environment.)"
    )


async def interrupt(sandbox: Sandbox, execution_id: str) -> None:
    """End a running command: execd stops its process group, SIGTERM then SIGKILL 3 s later.
    One that ended meanwhile, or an environment that no longer answers, is only logged."""
    try:
        async with asyncio.timeout(10):
            await sandbox.commands.interrupt(execution_id)
    except Exception as e:  # noqa: BLE001 (the run is stopping either way)
        log.warning("environment: command %s not interrupted: %s", execution_id, e)


# The commands the current turn has running, by their execution id at execd: which sandbox, and the
# command. The turn's heartbeat carries them (runs/activities.py), so an attempt retried after its
# worker crashed stops them first: the crashed attempt's command runs on in the sandbox, and the
# turn, resumed from its last checkpoint, would run it again beside it (plan, P3-C6)
RUNNING: ContextVar[dict[str, dict[str, str]] | None] = ContextVar(
    "gen9_running_commands", default=None
)
# The commands a retried attempt stopped: the same command run again says so to the agent
STOPPED: ContextVar[set[str] | None] = ContextVar("gen9_stopped_commands", default=None)
# How much of a command a heartbeat carries, to know it again
COMMAND_KEPT = 500
RESTARTED = (
    "(Gen9 restarted while this command ran before: that run was stopped, and this output is a "
    "new run's. What the earlier run did before it stopped, such as files it wrote or requests it "
    "sent, may already be done.)"
)


async def stop_left(settings: Settings, left: dict[str, dict[str, str]]) -> list[str]:
    """Stops the commands a crashed attempt left running (its last heartbeat's): the ones that
    ended meanwhile, or whose sandbox is gone, are only logged. Returns the commands."""
    stopped: list[str] = []
    for execution_id, where in left.items():
        try:
            sandbox = await Sandbox.connect(
                where["sandbox"],
                connection_config=connection(settings),
                skip_health_check=True,
            )
        except Exception as e:  # noqa: BLE001 (a sandbox that's gone has nothing running)
            log.info("environment: command %s left no sandbox: %s", execution_id, e)
            continue
        await interrupt(sandbox, execution_id)
        log.warning(
            "environment: stopped command %s, left running by an earlier attempt",
            execution_id,
        )
        stopped.append(where.get("command", ""))
    return stopped


async def answers(sandbox: Sandbox) -> bool:
    """Whether the sandbox's execd answers, asked a few times over a few seconds. OpenSandbox calls
    a sandbox running while its own container runs, but its egress sidecar holds its network and
    the ports Gen9 reaches it by: with the sidecar dead the container runs on, cut off, and every
    command failed with "try again" until the chat was left idle (manual-e2e.md, P4-E4)."""
    for attempt in range(PINGS):
        if attempt:
            await asyncio.sleep(PING_EVERY_S)
        if await sandbox.is_healthy():
            return True
    return False


class EnvironmentUnavailable(RuntimeError):
    """The chat's environment can't be had right now; the model is told, the run goes on."""


@dataclass
class _Chat:
    sandbox_id: str
    sandbox: Sandbox
    acquired_at: float


class Environments:
    """Every chat's environment as the agent's runs reach it: the sandbox of a chat, asked of its
    workflow. The worker gives it the Temporal client once connected."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.temporal: Client | None = None
        self._chats: dict[str, _Chat] = {}
        # When each chat last used its environment in this worker (monotonic)
        self._used: dict[str, float] = {}

    def used_since(self, thread_id: str, since: float) -> bool:
        """Whether the chat used its environment in this worker since `since` (a turn's start)."""
        return self._used.get(thread_id, 0.0) >= since

    async def sandbox(self, thread_id: str, user_sub: str) -> Sandbox:
        """The chat's sandbox, created the first time it's needed."""
        self._used[thread_id] = time.monotonic()
        chat = self._chats.get(thread_id)
        if chat and time.monotonic() - chat.acquired_at < ACQUIRE_EVERY_S:
            return chat.sandbox
        for _ in range(2):
            sandbox_id = await self._acquire(thread_id, user_sub)
            if chat and chat.sandbox_id == sandbox_id:
                chat.acquired_at = time.monotonic()
                return chat.sandbox
            try:
                sandbox = await Sandbox.connect(
                    sandbox_id,
                    connection_config=connection(self.settings),
                    skip_health_check=True,
                )
            except SandboxApiException as e:
                if e.status_code != 404:
                    log.warning("environment of chat %s: %s", thread_id, e)
                    raise EnvironmentUnavailable("OpenSandbox didn't answer") from None
                # Removed while its workflow still held it (its secrets couldn't be rewritten, an
                # operator removed it): the turn failed on every Retry until the chat was left
                # idle (manual-e2e.md, P4-E4). Its workflow is ended, and the next one makes a new
                log.warning(
                    "environment of chat %s: sandbox %s is gone", thread_id, sandbox_id
                )
                await self._end(thread_id, sandbox_id)
                continue
            except SandboxException as e:
                log.warning("environment of chat %s: %s", thread_id, e)
                raise EnvironmentUnavailable("OpenSandbox didn't answer") from None
            self._chats[thread_id] = _Chat(sandbox_id, sandbox, time.monotonic())
            return sandbox
        raise EnvironmentUnavailable("its environment couldn't start")

    async def lost(self, thread_id: str, sandbox: Sandbox) -> str | None:
        """Why the chat's sandbox is gone (its container stopped: killed, crashed, removed; or it
        runs but no longer answers), or None while it runs or OpenSandbox can't say. A gone one is
        ended, so the chat's next command gets a new one; the turn's files aren't captured from
        it."""
        try:
            info = await sandbox.get_info()
        except SandboxApiException as e:
            if e.status_code != 404:
                return None
            why = "removed"
        except SandboxException:
            # OpenSandbox itself not reached (stopped, restarting): it can't say. This escaped
            # before, and the turn failed three times in 5 s and waited for Retry (P4-E4)
            return None
        else:
            if info.status.state in GONE:
                why = info.status.reason or info.status.state
            elif await answers(sandbox):
                return None
            else:
                why = "it no longer answers"
        log.warning("environment of chat %s stopped: %s", thread_id, why)
        self._used.pop(thread_id, None)
        await self._end(thread_id, sandbox.id)
        return why

    async def _end(self, thread_id: str, sandbox_id: str) -> None:
        """The chat's workflow told its sandbox is gone, if that is still its sandbox."""
        self._chats.pop(thread_id, None)
        if self.temporal is None:
            return
        try:
            await self.temporal.get_workflow_handle(
                environment_workflow_id(thread_id)
            ).signal(END_SIGNAL, sandbox_id)
        except RPCError as e:
            if e.status != RPCStatusCode.NOT_FOUND:
                raise

    async def _acquire(self, thread_id: str, user_sub: str) -> str:
        if self.temporal is None:
            raise RuntimeError("Environments need the worker's Temporal client")
        for _ in range(CLOSING_RETRIES):
            start = WithStartWorkflowOperation(
                EnvironmentWorkflow.run,
                EnvironmentInput(thread_id, user_sub, self.settings.sandbox_idle_s),
                id=environment_workflow_id(thread_id),
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                task_queue=SYSTEM_QUEUE,
                # So deleting the chat or the account finds it (deletion.py)
                search_attributes=TypedSearchAttributes(
                    [
                        SearchAttributePair(GEN9_KIND, "environment"),
                        SearchAttributePair(GEN9_THREAD, thread_id),
                        SearchAttributePair(GEN9_USER, user_sub),
                    ]
                ),
            )
            try:
                return await self.temporal.execute_update_with_start_workflow(
                    ACQUIRE_UPDATE, start_workflow_operation=start, result_type=str
                )
            except WorkflowUpdateFailedError as e:
                # The chat's environment is being removed (idle, or deleted): its workflow completes
                # in seconds, and the next asks a new one
                if getattr(e.cause, "type", None) != "Leaving":
                    log.warning("environment of chat %s: %s", thread_id, e.cause or e)
                    raise EnvironmentUnavailable(
                        "its environment couldn't start"
                    ) from None
                await asyncio.sleep(CLOSING_RETRY_S)
            except RPCError as e:
                log.warning("environment of chat %s: %s", thread_id, e)
                raise EnvironmentUnavailable("its environment couldn't start") from None
        raise EnvironmentUnavailable("its environment didn't close in time")


class EnvironmentBackend(BaseSandbox):
    """The agent's default backend with environments on: the run's chat's sandbox. Async only, as
    Gen9 runs its agent."""

    def __init__(self, environments: Environments) -> None:
        self.environments = environments

    @property
    def id(self) -> str:
        return "gen9-environment"

    def _chat(self) -> tuple[str, str]:
        """The run's chat and person."""
        context = get_runtime().context
        # A background task works in the environment of the chat that started it (background.py)
        thread_id = getattr(context, "environment_of", None) or (
            get_config().get("configurable") or {}
        ).get("thread_id")
        user_sub = getattr(context, "user_sub", None)
        if not thread_id or not user_sub:
            raise RuntimeError("An environment belongs to a chat's run")
        return str(thread_id), user_sub

    async def _sandbox(self) -> Sandbox:
        return await self.environments.sandbox(*self._chat())

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        raise NotImplementedError("Gen9 runs its agent asynchronously: aexecute")

    async def aexecute(
        self,
        command: str,
        *,
        timeout: int | None = None,  # noqa: ASYNC109 (the command's own limit, not this call's)
    ) -> ExecuteResponse:
        thread_id, user_sub = self._chat()
        try:
            sandbox = await self.environments.sandbox(thread_id, user_sub)
        except EnvironmentUnavailable as e:
            return ExecuteResponse(
                output=f"This chat's environment isn't available: {e}. Try again later.",
                exit_code=1,
                truncated=False,
            )
        limit = min(
            timeout or self.environments.settings.sandbox_command_timeout_s,
            MAX_COMMAND_S,
        )
        started = time.monotonic()
        # The command's id at execd, from the first event of its stream
        running: list[str] = []
        tracked = RUNNING.get()

        async def on_init(init: ExecutionInit) -> None:
            running.append(init.id)
            if tracked is not None:
                tracked[init.id] = {
                    "sandbox": sandbox.id,
                    "command": command[:COMMAND_KEPT],
                }

        try:
            execution = await sandbox.commands.run(
                command,
                opts=RunCommandOpts(timeout=timedelta(seconds=limit)),
                handlers=ExecutionHandlers(on_init=on_init),
            )
        except asyncio.CancelledError:
            # The run was stopped. Closing the stream doesn't reach execd through the server's
            # proxy, and the command would run on to its limit: interrupted by its id, which ends
            # its process group (explore/sandbox/cancel_probe.py)
            if running:
                await asyncio.shield(interrupt(sandbox, running[0]))
            raise
        except SandboxException as e:
            # The agent is told, rather than the turn failing
            why = await self.environments.lost(thread_id, sandbox)
            if why is None:
                log.warning("environment: command not run: %s", e)
                # Not reached at all: the network error's words mean nothing to the person
                reason = (
                    "OpenSandbox didn't answer"
                    if isinstance(e, SandboxConnectionException)
                    else e.error.message or "OpenSandbox didn't answer"
                )
                return ExecuteResponse(
                    output=f"The command couldn't run in this chat's environment: {reason}. "
                    "Try again in a moment.",
                    exit_code=1,
                    truncated=False,
                )
            return ExecuteResponse(output=stopped(why), exit_code=1, truncated=False)
        finally:
            if tracked is not None:
                for execution_id in running:
                    tracked.pop(execution_id, None)
        output, truncated = output_of(execution)
        exit_code = execution.exit_code if execution.exit_code is not None else -1
        rerun = STOPPED.get()
        if rerun and command[:COMMAND_KEPT] in rerun:
            rerun.discard(command[:COMMAND_KEPT])
            output = RESTARTED + ("\n" + output if output else "")
        if exit_code == -1 and time.monotonic() - started >= limit:
            # OpenSandbox kills it and says only "-1": the agent is told why, and what it can do
            output += (
                f"\n(Stopped after {limit} s, this command's time limit. If it needs longer, "
                f"run it again with a timeout of up to {MAX_COMMAND_S} s.)"
            )
        elif exit_code == -1 and (
            why := await self.environments.lost(thread_id, sandbox)
        ):
            # Its output ended without an exit code: the environment stopped under it
            output += ("\n" if output else "") + stopped(why)
        return ExecuteResponse(output=output, exit_code=exit_code, truncated=truncated)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        raise NotImplementedError("Gen9 runs its agent asynchronously: aupload_files")

    async def aupload_files(
        self, files: list[tuple[str, bytes]]
    ) -> list[FileUploadResponse]:
        try:
            sandbox = await self._sandbox()
        except EnvironmentUnavailable:
            return [
                FileUploadResponse(path=p, error="permission_denied") for p, _ in files
            ]
        uploaded = []
        for path, content in files:
            try:
                await sandbox.files.write_file(path, content)
                uploaded.append(FileUploadResponse(path=path, error=None))
            except SandboxException as e:
                log.info("environment: upload of %s refused: %s", path, e)
                uploaded.append(
                    FileUploadResponse(path=path, error="permission_denied")
                )
        return uploaded

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        raise NotImplementedError("Gen9 runs its agent asynchronously: adownload_files")

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        try:
            sandbox = await self._sandbox()
        except EnvironmentUnavailable:
            return [
                FileDownloadResponse(path=p, content=None, error="permission_denied")
                for p in paths
            ]
        downloaded = []
        for path in paths:
            try:
                content = await sandbox.files.read_bytes(path)
                downloaded.append(
                    FileDownloadResponse(path=path, content=content, error=None)
                )
            except SandboxException as e:
                missing = "404" in str(e) or "not found" in str(e).lower()
                downloaded.append(
                    FileDownloadResponse(
                        path=path,
                        content=None,
                        error="file_not_found" if missing else "permission_denied",
                    )
                )
        return downloaded


async def create(
    settings: Settings,
    thread_id: str,
    user_sub: str,
    timeout_s: int,
    secrets: list[Secret],
) -> str:
    """A new sandbox for the chat, or the one an earlier attempt already made (found by its
    metadata), so a retried Activity never leaves one running. The person's secrets go in its
    vault; its credential proxy is on either way, so secrets added later reach it."""
    config = connection(settings)
    manager = await SandboxManager.create(connection_config=config)
    try:
        existing = await manager.list_sandbox_infos(
            SandboxFilter(
                metadata={THREAD_KEY: thread_id}, states=["Pending", "Running"]
            )
        )
        if existing.sandbox_infos:
            return existing.sandbox_infos[0].id
    finally:
        await manager.close()
    sandbox = await Sandbox.create(
        settings.sandbox_image,
        connection_config=config,
        timeout=timedelta(seconds=timeout_s),
        ready_timeout=timedelta(seconds=120),
        resource={"cpu": settings.sandbox_cpu, "memory": settings.sandbox_memory},
        network_policy=network_policy(settings, secrets),
        credential_proxy=CredentialProxyConfig(enabled=True),
        metadata={THREAD_KEY: thread_id, USER_KEY: user_sub},
    )
    try:
        if secrets:
            credentials, bindings = vault_entries(secrets)
            await sandbox.credential_vault.create(
                credentials=credentials, bindings=bindings
            )
        # The folder the model is told to share files in, there from the start: after a turn
        # that used the environment, the worker lists it (chat_files.capture), and listing a
        # folder that isn't there made the SDK log an error with its traceback, though nothing
        # was wrong (manual-e2e.md, P3-C14)
        await sandbox.files.create_directories([WriteEntry(path=OUT_DIR)])
    except Exception:
        # Without its secrets or its folder it isn't the environment asked for: none rather
        # than half
        await sandbox.kill()
        raise
    finally:
        await sandbox.close()
    log.info("environment %s created for chat %s", sandbox.id, thread_id)
    return sandbox.id


async def apply_secrets(
    settings: Settings, sandbox_id: str, secrets: list[Secret]
) -> None:
    """A running sandbox's rules and vault rewritten to the person's current secrets: hosts no
    longer allowed are closed, the vault replaced."""
    sandbox = await Sandbox.connect(
        sandbox_id, connection_config=connection(settings), skip_health_check=True
    )
    try:
        # The vault first: OpenSandbox refuses to close a host a binding still uses
        try:
            await sandbox.credential_vault.delete()
        except SandboxApiException as e:
            if "404" not in str(e) and "not found" not in str(e).lower():
                raise
        wanted = allowed_hosts(settings, secrets)
        policy = await sandbox.get_egress_policy()
        stale = [r.target for r in policy.egress or [] if r.target not in wanted]
        if stale:
            await sandbox.delete_egress_rules(stale)
        if wanted:
            await sandbox.patch_egress_rules(
                [NetworkRule(action="allow", target=host) for host in wanted]
            )
        if secrets:
            credentials, bindings = vault_entries(secrets)
            await sandbox.credential_vault.create(
                credentials=credentials, bindings=bindings
            )
    finally:
        await sandbox.close()


async def renew(settings: Settings, sandbox_id: str, timeout_s: int) -> None:
    """OpenSandbox's timeout for it put further off. One already gone has nothing to renew: the
    command that asked finds it gone and ends it (`Environments.sandbox`). With OpenSandbox not
    reached, this renewal is skipped (the next is due a half idle time on, and the backstop is a
    full idle time ahead): retried, it failed the ask after 38 s, "couldn't start" (P4-E4)."""
    manager = await SandboxManager.create(connection_config=connection(settings))
    try:
        await manager.renew_sandbox(sandbox_id, timedelta(seconds=timeout_s))
    except SandboxApiException as e:
        if e.status_code != 404:
            raise
        log.warning("environment %s: not renewed, it is gone", sandbox_id)
    except SandboxConnectionException as e:
        log.warning(
            "environment %s: not renewed, OpenSandbox not reached: %s", sandbox_id, e
        )
    finally:
        await manager.close()


async def remove(settings: Settings, sandbox_ids: list[str]) -> int:
    """Kills the sandboxes; one already gone counts as removed. How many were running."""
    manager = await SandboxManager.create(connection_config=connection(settings))
    removed = 0
    try:
        for sandbox_id in sandbox_ids:
            try:
                await manager.kill_sandbox(sandbox_id)
                removed += 1
            except SandboxApiException as e:
                if "404" not in str(e) and "not found" not in str(e).lower():
                    raise
    finally:
        await manager.close()
    return removed


async def _ids(settings: Settings, metadata: dict[str, str]) -> list[str]:
    manager = await SandboxManager.create(connection_config=connection(settings))
    try:
        found = await manager.list_sandbox_infos(SandboxFilter(metadata=metadata))
        return [s.id for s in found.sandbox_infos]
    finally:
        await manager.close()


async def of_user(settings: Settings, user_sub: str) -> list[str]:
    """The ids of a person's sandboxes, whatever their chats."""
    return await _ids(settings, {USER_KEY: user_sub})


async def of_thread(settings: Settings, thread_id: str) -> list[str]:
    """The ids of a chat's sandboxes (one, or none)."""
    return await _ids(settings, {THREAD_KEY: thread_id})
