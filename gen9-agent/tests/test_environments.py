"""A chat's environment (environments.py, workflows/environment.py): one sandbox per chat, created
once however many ask, renewed while used, removed when idle or when the chat goes; how a command's
output reads; and the agent's files with environments on and off. Temporal's time-skipping test
server, fake Activities."""

import asyncio
import base64
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from deepagents.backends import StateBackend
from opensandbox.exceptions import SandboxApiException, SandboxException
from opensandbox.exceptions.sandbox import SandboxConnectionException, SandboxError
from pydantic import ValidationError
from temporalio import activity
from temporalio.client import WithStartWorkflowOperation, WorkflowUpdateFailedError
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.service import RPCError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from gen9_agent import agent, chat_files, environments
from gen9_agent.api import files as files_api
from gen9_agent.api import threads as threads_api
from gen9_agent.api.environment_secrets import SecretIn
from gen9_agent.api.threads import MessageOut, RunIn
from gen9_agent.environments import (
    EnvironmentBackend,
    Environments,
    Secret,
    allowed_hosts,
    network_policy,
    output_of,
    vault_entries,
)
from gen9_agent.settings import Settings
from gen9_agent.temporal import WORKFLOW_RUNNER
from gen9_agent.workflows.environment import (
    CheckEnvironment,
    CreateEnvironment,
    EnvironmentInput,
    EnvironmentWorkflow,
    RenewEnvironment,
)
from gen9_agent.workflows.names import (
    ACQUIRE_UPDATE,
    CHECK_ENVIRONMENT_SECRETS,
    CREATE_ENVIRONMENT,
    END_SIGNAL,
    REMOVE_ENVIRONMENT,
    RENEW_ENVIRONMENT,
    SYSTEM_QUEUE,
)

IDLE_S = 600


class Fakes:
    """Records what the workflow asked OpenSandbox for; creating takes a moment, as it does."""

    def __init__(self, removing_s: float = 0) -> None:
        self.removing_s = removing_s
        self.created: list[CreateEnvironment] = []
        self.renewed: list[RenewEnvironment] = []
        self.checked: list[CheckEnvironment] = []
        self.removed: list[str] = []

    def all(self) -> list:
        @activity.defn(name=CREATE_ENVIRONMENT)
        async def create(env: CreateEnvironment) -> str:
            self.created.append(env)
            await asyncio.sleep(0.2)
            return f"sandbox-{len(self.created)}"

        @activity.defn(name=RENEW_ENVIRONMENT)
        async def renew(env: RenewEnvironment) -> None:
            self.renewed.append(env)

        @activity.defn(name=CHECK_ENVIRONMENT_SECRETS)
        async def check(env: CheckEnvironment) -> bool:
            self.checked.append(env)
            return False

        @activity.defn(name=REMOVE_ENVIRONMENT)
        async def remove(sandbox_id: str) -> None:
            await asyncio.sleep(self.removing_s)
            self.removed.append(sandbox_id)

        return [create, renew, check, remove]


@pytest_asyncio.fixture
async def env():
    # One test server per test: a later test's timers hung at times after an earlier test had
    # skipped time in a shared one
    async with await WorkflowEnvironment.start_time_skipping() as env:
        yield env


async def start(env: WorkflowEnvironment, idle_s: int = IDLE_S):
    return await env.client.start_workflow(
        EnvironmentWorkflow.run,
        EnvironmentInput("thread-1", "sub-1", idle_s),
        id=f"environment-test-{uuid.uuid4()}",
        task_queue=SYSTEM_QUEUE,
    )


def worker(env: WorkflowEnvironment, fakes: Fakes) -> Worker:
    return Worker(
        env.client,
        task_queue=SYSTEM_QUEUE,
        workflows=[EnvironmentWorkflow],
        workflow_runner=WORKFLOW_RUNNER,
        activities=fakes.all(),
    )


@pytest.mark.asyncio
async def test_one_sandbox_however_many_ask_then_removed_when_idle(
    env: WorkflowEnvironment,
) -> None:
    fakes = Fakes()
    async with worker(env, fakes):
        handle = await start(env)
        first, second = await asyncio.gather(
            handle.execute_update(EnvironmentWorkflow.acquire),
            handle.execute_update(EnvironmentWorkflow.acquire),
        )
        assert first == second == "sandbox-1"
        assert await handle.execute_update(EnvironmentWorkflow.acquire) == "sandbox-1"
        assert len(fakes.created) == 1
        # Asked again, the sandbox has its secrets checked; the asks that made it or waited for
        # it don't, as it has them from its creation
        assert fakes.checked == [CheckEnvironment("sandbox-1", "sub-1")]
        # OpenSandbox's own timeout, the backstop: twice the idle time
        assert fakes.created[0] == CreateEnvironment("thread-1", "sub-1", 2 * IDLE_S)
        await handle.result()  # time skips past the idle time
        assert fakes.removed == ["sandbox-1"]


@pytest.mark.asyncio
async def test_used_it_is_renewed_and_kept(env: WorkflowEnvironment) -> None:
    # In real time with a short idle time: skipped time and a manual sleep don't mix reliably
    fakes = Fakes()
    with env.auto_time_skipping_disabled():
        async with worker(env, fakes):
            handle = await start(env, idle_s=4)
            await handle.execute_update(EnvironmentWorkflow.acquire)
            for _ in range(3):
                await asyncio.sleep(2.5)
                assert (
                    await handle.execute_update(EnvironmentWorkflow.acquire)
                    == "sandbox-1"
                )
            # Used every 2.5 s of 4: never removed, renewed each time (past half the idle time)
            assert fakes.removed == []
            assert [r.sandbox_id for r in fakes.renewed] == ["sandbox-1"] * 3
            await handle.result()
            assert fakes.removed == ["sandbox-1"]


@pytest.mark.asyncio
async def test_ended_it_is_removed_and_takes_no_more_commands(
    env: WorkflowEnvironment,
) -> None:
    fakes = Fakes(removing_s=1.0)
    # Ended by the Signal, it needs no skipped time; skipping it would cut the slow removal short
    with env.auto_time_skipping_disabled():
        await ended_takes_no_more_commands(env, fakes)


async def ended_takes_no_more_commands(env: WorkflowEnvironment, fakes: Fakes) -> None:
    async with worker(env, fakes):
        handle = await start(env)
        await handle.execute_update(EnvironmentWorkflow.acquire)
        await handle.signal(END_SIGNAL)
        await asyncio.sleep(0.3)  # removing now
        # While it removes the sandbox, a command is told it's closing (the caller then starts
        # the next environment once this workflow completes)
        with pytest.raises(WorkflowUpdateFailedError) as refused:
            await handle.execute_update(EnvironmentWorkflow.acquire)
        assert getattr(refused.value.cause, "type", None) == "Leaving"
        await handle.result()
        assert fakes.removed == ["sandbox-1"]
        # Completed, it takes none at all
        with pytest.raises((WorkflowUpdateFailedError, RPCError)):
            await handle.execute_update(EnvironmentWorkflow.acquire)


@pytest.mark.asyncio
async def test_a_stopped_sandbox_ends_it_but_not_its_successor(
    env: WorkflowEnvironment,
) -> None:
    """`end` with a sandbox id ends the workflow only if that is still its sandbox: a report about
    a sandbox already replaced leaves the new one alone (P2-C5)."""
    fakes = Fakes()
    with env.auto_time_skipping_disabled():
        async with worker(env, fakes):
            handle = await start(env)
            await handle.execute_update(EnvironmentWorkflow.acquire)
            await handle.signal(END_SIGNAL, "sandbox-0")  # an earlier one's
            assert (
                await handle.execute_update(EnvironmentWorkflow.acquire) == "sandbox-1"
            )
            await handle.signal(END_SIGNAL, "sandbox-1")
            await handle.result()
            assert fakes.removed == ["sandbox-1"]


@pytest.mark.asyncio
async def test_never_used_it_removes_nothing(env: WorkflowEnvironment) -> None:
    fakes = Fakes()
    async with worker(env, fakes):
        handle = await start(env)
        await handle.result()
        assert fakes.created == [] and fakes.removed == []


def lines(*texts: str) -> list[SimpleNamespace]:
    return [SimpleNamespace(text=t) for t in texts]


def test_a_commands_output_reads_as_printed() -> None:
    # OpenSandbox sends an empty line as "\n", every other line without its newline
    execution = SimpleNamespace(
        logs=SimpleNamespace(stdout=lines("a", "b", "\n", "c"), stderr=lines("oops"))
    )
    assert output_of(execution) == ("a\nb\n\nc\noops", False)
    quiet = SimpleNamespace(logs=SimpleNamespace(stdout=[], stderr=lines("only")))
    assert output_of(quiet) == ("only", False)
    loud = SimpleNamespace(
        logs=SimpleNamespace(
            stdout=lines("x" * (environments.MAX_OUTPUT + 5)), stderr=[]
        )
    )
    output, truncated = output_of(loud)
    assert truncated and len(output) == environments.MAX_OUTPUT


def test_with_environments_the_agents_files_are_in_the_chats_environment() -> None:
    skills = StateBackend()
    off = agent.backend(skills, StateBackend(), None)
    assert isinstance(off.default, StateBackend)
    settings = Settings.model_construct(sandbox_url="http://gen9-sandbox:8090")
    on = agent.backend(skills, StateBackend(), Environments(settings))  # ty: ignore[invalid-argument-type]
    assert isinstance(on.default, EnvironmentBackend)
    # What Deep Agents offloads stays in the chat's state: no environment just for that
    assert on.artifacts_root == "/gen9"
    assert isinstance(on.routes[agent.ARTIFACTS_ROUTE], StateBackend)


@pytest.mark.asyncio
async def test_the_first_ask_can_start_the_workflow(env: WorkflowEnvironment) -> None:
    # Update-with-Start, as environments.py asks: the Update can run before `run` begins
    fakes = Fakes()
    async with worker(env, fakes):
        start = WithStartWorkflowOperation(
            EnvironmentWorkflow.run,
            EnvironmentInput("thread-1", "sub-1", IDLE_S),
            id=f"environment-test-{uuid.uuid4()}",
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            task_queue=SYSTEM_QUEUE,
        )
        got = await env.client.execute_update_with_start_workflow(
            ACQUIRE_UPDATE, start_workflow_operation=start, result_type=str
        )
        assert got == "sandbox-1" and len(fakes.created) == 1


def secret(
    name: str, auth: str = "bearer", header: str | None = None, value: str = "v"
) -> Secret:
    return Secret(name, f"{name}.example.com", "/v1/*", auth, header, value)


def test_a_secret_goes_to_its_host_and_path_as_chosen() -> None:
    credentials, bindings = vault_entries(
        [
            secret("gh"),
            secret("key", "header", "X-Api-Key"),
            secret("reg", "basic", value="ada:pw"),
        ]
    )

    def dump(model) -> dict:
        return model.model_dump(exclude_none=True, by_alias=True)

    assert [c.name for c in credentials] == ["gh", "key", "reg"]
    # Basic takes user:password, base64-encoded as the vault expects
    assert dump(credentials[2].source) == {
        "value": base64.b64encode(b"ada:pw").decode(),
        "type": "inline",
    }
    assert [dump(b.auth) for b in bindings] == [
        {"type": "bearer", "credential": "gh"},
        {"type": "apiKey", "name": "X-Api-Key", "credential": "key"},
        {"type": "basic", "credential": "reg"},
    ]
    # Only https, to its own host and path, and only with reads unless it was given changes
    assert dump(bindings[0].match) == {
        "schemes": ["https"],
        "hosts": ["gh.example.com"],
        "paths": ["/v1/*"],
        "methods": ["GET", "HEAD", "OPTIONS"],
    }


def test_a_secret_goes_with_changes_only_when_given_them() -> None:
    _, bindings = vault_entries(
        [
            secret("gh"),
            Secret("push", "push.example.com", "/*", "bearer", None, "v", "all"),
        ]
    )
    methods = [b.match.model_dump(by_alias=True)["methods"] for b in bindings]
    assert methods == [
        ["GET", "HEAD", "OPTIONS"],
        ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"],
    ]


def test_an_environment_reaches_the_operators_hosts_and_its_secrets_hosts() -> None:
    settings = Settings.model_construct(
        sandbox_egress_allow=["pypi.org", "gh.example.com"]
    )
    assert allowed_hosts(settings, [secret("gh"), secret("key")]) == [
        "pypi.org",
        "gh.example.com",
        "key.example.com",
    ]
    policy = network_policy(settings, [])
    assert (
        policy.default_action == "deny"
        or getattr(policy, "defaultAction", "deny") == "deny"
    )


@pytest.mark.parametrize(
    ("body", "field", "why"),
    [
        ({"host": "https://api.example.com"}, "host", "a host, not a URL"),
        ({"host": "api.example.com:443"}, "host", "no port"),
        ({"host": "localhost"}, "host", "a dotted name"),
        (
            {"host": "10.0.0.5"},
            "host",
            "a name, not an IP address (OpenSandbox's vault)",
        ),
        ({"host": "*.0.0.1"}, "host", "nor a wildcard over addresses"),
        ({"auth": "header"}, "header", "a header needs its name"),
        ({"header": "X-Key"}, "header", "a name without auth header"),
        ({"auth": "basic", "value": "no-colon"}, "value", "Basic takes user:password"),
        ({"path": "v1/*"}, "path", "a path starts with /"),
        ({"name": "Has Spaces"}, "name", "a plain name"),
    ],
)
def test_a_secret_that_doesnt_fit_is_refused_at_its_field(
    body: dict, field: str, why: str
) -> None:
    """Each refusal names the field it is about, which Settings shows it under (P2-K2)."""
    base = {"name": "gh", "host": "api.github.com", "value": "t"}
    with pytest.raises(ValidationError) as refused:
        SecretIn.model_validate({**base, **body})
    assert [e["loc"] for e in refused.value.errors()] == [(field,)], why


def test_a_secret_that_fits() -> None:
    ok = SecretIn.model_validate(
        {
            "name": "gh",
            "host": "*.github.com",
            "auth": "header",
            "header": "X-Key",
            "value": "t",
        }
    )
    assert ok.path == "/*"
    # Digits are fine in a name, only not as all of its last label
    assert SecretIn.model_validate(
        {"name": "s3", "host": "s3.eu-1.example.com", "value": "t"}
    )


class RecordingSandbox:
    """A running sandbox as `apply_secrets` changes it: the calls, in order."""

    def __init__(self, rules: list[str]) -> None:
        self.calls: list[str] = []
        self.rules = rules
        vault = SimpleNamespace(delete=self._delete_vault, create=self._create_vault)
        self.credential_vault = vault

    async def _delete_vault(self) -> None:
        self.calls.append("delete vault")

    async def _create_vault(self, credentials, bindings) -> None:
        self.calls.append(f"create vault {[c.name for c in credentials]}")

    async def get_egress_policy(self):
        return SimpleNamespace(egress=[SimpleNamespace(target=t) for t in self.rules])

    async def delete_egress_rules(self, targets: list[str]) -> None:
        self.calls.append(f"close {targets}")

    async def patch_egress_rules(self, rules) -> None:
        self.calls.append(f"allow {[r.target for r in rules]}")

    async def close(self) -> None:
        self.calls.append("close connection")


@pytest.mark.asyncio
async def test_a_changed_secret_replaces_the_vault_before_closing_hosts(
    monkeypatch,
) -> None:
    # OpenSandbox refuses to close a host a vault binding still uses: the vault goes first
    sandbox = RecordingSandbox(rules=["pypi.org", "gh.example.com"])

    async def connect(*args, **kwargs):
        return sandbox

    monkeypatch.setattr(environments.Sandbox, "connect", connect)
    settings = Settings.model_construct(
        sandbox_url="http://gen9-sandbox:8090",
        sandbox_api_key=None,
        sandbox_egress_allow=["pypi.org"],
    )
    await environments.apply_secrets(settings, "sb-1", [secret("key")])
    assert sandbox.calls == [
        "delete vault",
        "close ['gh.example.com']",
        "allow ['pypi.org', 'key.example.com']",
        "create vault ['key']",
        "close connection",
    ]


class HeldSandbox:
    """A running sandbox as `drifted` reads it: its vault's names (None: no vault) and rules."""

    def __init__(self, held: list[str] | None, rules: list[str]) -> None:
        self.held, self.rules = held, rules
        self.credential_vault = SimpleNamespace(get=self._vault)

    async def _vault(self) -> SimpleNamespace:
        if self.held is None:
            raise SandboxApiException("credential vault not found", status_code=404)
        return SimpleNamespace(credentials=[SimpleNamespace(name=n) for n in self.held])

    async def get_egress_policy(self) -> SimpleNamespace:
        return SimpleNamespace(
            egress=[SimpleNamespace(action="allow", target=t) for t in self.rules]
        )

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("held", "rules", "secrets", "drifted"),
    [
        # As written: the operator's host and the secret's
        (["key"], ["pypi.org", "key.example.com"], {"key": "key.example.com"}, False),
        # Its egress restarted: an empty vault, and the rules it was created with, a host the
        # person took away since among them (P4-E4)
        (
            [],
            ["pypi.org", "key.example.com", "old.example.com"],
            {"key": "key.example.com"},
            True,
        ),
        ([], ["pypi.org", "key.example.com"], {"key": "key.example.com"}, True),
        (
            ["key"],
            ["pypi.org", "key.example.com", "old.example.com"],
            {"key": "key.example.com"},
            True,
        ),
        # No secrets, and no vault ever written
        (None, ["pypi.org"], {}, False),
    ],
)
async def test_a_sandbox_whose_vault_or_rules_differ_from_the_secrets_has_drifted(
    monkeypatch, held, rules, secrets, drifted
) -> None:
    sandbox = HeldSandbox(held, rules)

    async def connect(*args, **kwargs):
        return sandbox

    monkeypatch.setattr(environments.Sandbox, "connect", connect)
    settings = Settings.model_construct(
        sandbox_url="http://gen9-sandbox:8090",
        sandbox_api_key=None,
        sandbox_egress_allow=["pypi.org"],
    )
    assert await environments.drifted(settings, "sb-1", secrets) is drifted


def test_only_the_out_folders_regular_files_are_shared() -> None:
    entries = [
        SimpleNamespace(path="/work/out/report.csv", entry_type="file"),
        SimpleNamespace(path="/work/out/charts", entry_type="directory"),
        SimpleNamespace(path="/work/out/charts/a.png", entry_type="file"),
        SimpleNamespace(
            path="/work/out/passwd", entry_type="symlink"
        ),  # could point anywhere
    ]
    shared = chat_files.shareable(entries)
    assert [chat_files.name_of(e.path) for e in shared] == [
        "report.csv",
        "charts/a.png",
    ]
    many = [
        SimpleNamespace(path=f"/work/out/{i}", entry_type="file") for i in range(150)
    ]
    assert len(chat_files.shareable(many)) == chat_files.MAX_FILES
    assert chat_files.media_type("report.csv") == "text/csv"
    assert chat_files.media_type("blob") == "application/octet-stream"


def test_a_message_says_where_its_attachments_are() -> None:
    assert chat_files.attached_note([]) == ""
    assert (
        chat_files.attached_note(["a.csv", "b.png"])
        == "\n\nAttached, in /work/in: a.csv, b.png"
    )


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("report.csv", True),
        ("scores 2026.xlsx", True),
        ("../etc/passwd", False),
        ("dir/file.txt", False),
        (".env", False),
        ("x" * 129, False),
    ],
)
def test_an_attachment_is_named_without_folders(name: str, ok: bool) -> None:
    assert bool(files_api.NAME.match(name)) is ok


@pytest.mark.parametrize(
    ("name", "spoofs"),
    [
        ("تقرير.txt", False),  # a right-to-left name needs no controls
        ("📊 sales.csv", False),
        ("report\u202egpj.exe", True),  # shows as "reportexe.jpg"
        ("a\u2067b\u2069.txt", True),
        ("x\u200f.txt", True),
    ],
)
def test_a_name_that_reads_in_a_different_order_than_it_is(
    name: str, spoofs: bool
) -> None:
    """Refused for an upload; a shared file's name shows each control as "_" (P2-H2)."""
    assert bool(chat_files.BIDI_CONTROLS.search(name)) is spoofs
    shown = chat_files.name_of(f"{chat_files.OUT_DIR}/{name}")
    assert not chat_files.BIDI_CONTROLS.search(shown)
    assert (shown == name) is not spoofs


def test_a_message_names_at_most_ten_attachments() -> None:
    RunIn.model_validate(
        {"message": "hi", "files": [str(uuid.uuid4()) for _ in range(10)]}
    )
    with pytest.raises(ValidationError):
        RunIn.model_validate(
            {"message": "hi", "files": [str(uuid.uuid4()) for _ in range(11)]}
        )


def test_the_history_puts_attachments_on_the_question_and_shared_files_on_the_answer() -> (
    None
):
    def file(run: str, origin: str, name: str) -> SimpleNamespace:
        return SimpleNamespace(
            run_id=run,
            origin=origin,
            id=uuid.uuid4(),
            name=name,
            size=1,
            media_type="text/csv",
        )

    messages = [
        MessageOut(
            role="user",
            content="Sum it" + chat_files.attached_note(["in.csv"]),
            run_id="r1",
        ),
        MessageOut(role="assistant", content="Done: 3."),
        MessageOut(role="user", content="Thanks", run_id="r2"),
        MessageOut(role="assistant", content="You're welcome."),
    ]
    out = threads_api.with_files(
        messages, [file("r1", "upload", "in.csv"), file("r1", "output", "sum.csv")]
    )
    assert out[0].content == "Sum it" and [f.name for f in out[0].files] == ["in.csv"]
    assert [f.name for f in out[1].files] == ["sum.csv"]
    assert out[2].files == [] and out[3].files == []


@pytest.mark.asyncio
async def test_a_command_has_a_time_limit_and_is_told_when_it_runs_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No timeout from the agent: SANDBOX_COMMAND_TIMEOUT_S; more than MAX_COMMAND_S: capped
    under the turn's hour; killed at its limit: the output says why (P2-C4)."""
    asked: list[float] = []
    clock = iter([0.0, 1.0, 10.0, 20.0, 30.0, 630.0])

    class Commands:
        async def run(self, command: str, opts, handlers=None) -> SimpleNamespace:
            asked.append(opts.timeout.total_seconds())
            return SimpleNamespace(
                exit_code=-1, logs=SimpleNamespace(stdout=lines("started"), stderr=[])
            )

    settings = Settings.model_construct(sandbox_command_timeout_s=600)
    backend = EnvironmentBackend(Environments(settings))  # ty: ignore[invalid-argument-type]
    running = SimpleNamespace(state="Running", reason=None)

    async def get_info() -> SimpleNamespace:
        return SimpleNamespace(status=running)

    async def answers() -> bool:
        return True

    async def sandbox(thread_id: str, user_sub: str) -> SimpleNamespace:
        return SimpleNamespace(
            id="sandbox-1", commands=Commands(), get_info=get_info, is_healthy=answers
        )

    monkeypatch.setattr(backend, "_chat", lambda: ("thread-1", "sub-1"))
    monkeypatch.setattr(backend.environments, "sandbox", sandbox)
    # Only this module's own reference to time: asyncio and pytest keep the real clock
    monkeypatch.setattr(
        environments, "time", SimpleNamespace(monotonic=lambda: next(clock))
    )
    quick = await backend.aexecute("false")  # ends at once with -1: not a timeout
    capped = await backend.aexecute(
        "make", timeout=99_999
    )  # 10 s: not its limit either
    killed = await backend.aexecute("sleep 1000")  # 600 s: its limit
    assert asked == [600, environments.MAX_COMMAND_S, 600]
    assert quick.output == "started" and capped.output == "started"
    assert killed.exit_code == -1
    assert "Stopped after 600 s, this command's time limit" in killed.output


@pytest.mark.asyncio
async def test_an_environment_that_stops_under_a_command_is_told_and_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Its container killed before or during a command (P2-C5): the command raises, or its output
    ends without an exit code. The agent is told the environment stopped, and its workflow is ended so the
    next command gets a new one; OpenSandbox not answering while the sandbox runs and answers is
    only a failed command. One that runs but no longer answers (its egress sidecar died, P4-E4)
    is gone too. OpenSandbox's errors are their own cause (killed_probe.py)."""
    state = {"now": "Failed", "reason": "CONTAINER_EXITED_ERROR", "answers": True}
    pings: list[bool] = []
    monkeypatch.setattr("gen9_agent.environments.PING_EVERY_S", 0)
    signals: list[tuple[str, str, object]] = []

    def disconnected() -> SandboxException:
        if state["now"] == "unreached":
            return SandboxConnectionException(
                "Network connectivity error: [Errno -2] Name or service not known"
            )
        e = SandboxApiException(
            "Failed to run command",
            status_code=500,
            error=SandboxError("GENERAL::UNKNOWN_ERROR", "Server disconnected"),
        )
        e.__cause__ = e
        return e

    class Commands:
        def __init__(self, raises: bool) -> None:
            self.raises = raises

        async def run(self, command: str, opts, handlers=None) -> SimpleNamespace:
            if self.raises:
                raise disconnected()
            return SimpleNamespace(
                exit_code=None, logs=SimpleNamespace(stdout=lines("begin"), stderr=[])
            )

    async def get_info() -> SimpleNamespace:
        if state["now"] == "404":
            raise SandboxApiException("Get sandbox failed", status_code=404)
        if state["now"] == "unreached":
            raise SandboxConnectionException("Network connectivity error")
        return SimpleNamespace(
            status=SimpleNamespace(state=state["now"], reason=state["reason"])
        )

    class Handle:
        def __init__(self, workflow_id: str) -> None:
            self.workflow_id = workflow_id

        async def signal(self, name: str, arg: object) -> None:
            signals.append((self.workflow_id, name, arg))

    environments = Environments(Settings.model_construct(sandbox_command_timeout_s=600))  # ty: ignore[invalid-argument-type]
    environments.temporal = SimpleNamespace(get_workflow_handle=Handle)  # ty: ignore[invalid-assignment]
    backend = EnvironmentBackend(environments)
    raises = True

    async def sandbox(thread_id: str, user_sub: str) -> SimpleNamespace:
        environments._used[thread_id] = 1.0

        async def is_healthy() -> bool:
            pings.append(state["answers"])
            return state["answers"]

        return SimpleNamespace(
            id="sandbox-1",
            commands=Commands(raises),
            get_info=get_info,
            is_healthy=is_healthy,
        )

    monkeypatch.setattr(backend, "_chat", lambda: ("thread-1", "sub-1"))
    monkeypatch.setattr(environments, "sandbox", sandbox)

    killed = await backend.aexecute("echo begin; sleep 60")
    assert killed.exit_code == 1
    assert "environment stopped: CONTAINER_EXITED_ERROR" in killed.output
    assert "The next command starts a new, empty environment" in killed.output
    assert signals == [("environment-thread-1", END_SIGNAL, "sandbox-1")]
    # The turn's files aren't captured from it (a capture would start a new one for nothing)
    assert not environments.used_since("thread-1", 0.5)

    raises = False  # its output ended without an exit code
    ended = await backend.aexecute("echo begin; sleep 60")
    assert ended.output.startswith("begin\n(This chat's environment stopped")
    assert ended.exit_code == -1

    raises, state["now"] = True, "404"  # already removed
    assert "environment stopped: removed" in (await backend.aexecute("ls")).output

    state["now"] = (
        "Running"  # OpenSandbox didn't answer, the sandbox runs: nothing ended
    )
    signals.clear()
    failed = await backend.aexecute("ls")
    assert failed.exit_code == 1
    assert failed.output == (
        "The command couldn't run in this chat's environment: Server disconnected. "
        "Try again in a moment."
    )
    assert signals == [] and pings == [True]

    state["answers"] = False  # it runs, but its execd never answers
    gone = await backend.aexecute("ls")
    assert "environment stopped: it no longer answers" in gone.output
    assert pings == [True, False, False, False]  # asked three times before giving it up
    assert signals == [("environment-thread-1", END_SIGNAL, "sandbox-1")]

    # OpenSandbox itself stopped: it can't say, so nothing ended, and the agent is told rather
    # than the turn failing (P4-E4)
    state["now"] = "unreached"
    signals.clear()
    down = await backend.aexecute("ls")
    assert down.output == (
        "The command couldn't run in this chat's environment: OpenSandbox didn't answer. "
        "Try again in a moment."
    )
    assert signals == [] and pings == [True, False, False, False]


@pytest.mark.asyncio
async def test_a_command_stopped_with_its_run_is_interrupted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop during a command (found by hand: manual-e2e.md, P2-I3): the command is interrupted by
    its id, since closing the stream doesn't reach execd through the server's proxy
    (cancel_probe.py). Before its id is known there is nothing to interrupt; an interrupt that
    fails doesn't keep the run from stopping."""
    interrupted: list[str] = []
    fails = False

    class Commands:
        def __init__(self, started: bool) -> None:
            self.started = started

        async def run(self, command: str, opts, handlers=None) -> SimpleNamespace:
            if self.started:
                await handlers.on_init(SimpleNamespace(id="exec-1"))
            await asyncio.Event().wait()
            raise AssertionError("not cancelled")

        async def interrupt(self, execution_id: str) -> None:
            if fails:
                raise SandboxApiException("Interrupt command failed", status_code=500)
            interrupted.append(execution_id)

    settings = Settings.model_construct(sandbox_command_timeout_s=600)
    backend = EnvironmentBackend(Environments(settings))  # ty: ignore[invalid-argument-type]
    started = True

    async def sandbox(thread_id: str, user_sub: str) -> SimpleNamespace:
        return SimpleNamespace(id="sandbox-1", commands=Commands(started))

    monkeypatch.setattr(backend, "_chat", lambda: ("thread-1", "sub-1"))
    monkeypatch.setattr(backend.environments, "sandbox", sandbox)

    async def stop_during(command: str) -> None:
        task = asyncio.create_task(backend.aexecute(command))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    await stop_during("sleep 90")
    assert interrupted == ["exec-1"]
    started = False
    await stop_during("sleep 90")
    assert interrupted == ["exec-1"]
    started, fails = True, True
    await stop_during("sleep 90")
    assert interrupted == ["exec-1"]


@pytest.mark.asyncio
async def test_a_retried_turn_stops_the_command_a_crashed_worker_left_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker that crashes mid-command leaves it running in the sandbox, and the retried turn
    ran it again beside it: every line of a 40-step loop written twice (manual-e2e.md, P3-C6).
    Now the turn's record of what it runs goes with its heartbeat; a retry stops those commands
    first, and the same command run again tells the agent what happened."""
    seen: dict[str, dict[str, str]] = {}
    release = asyncio.Event()

    class Commands:
        async def run(self, command: str, opts, handlers=None) -> SimpleNamespace:
            await handlers.on_init(SimpleNamespace(id="exec-1"))
            seen.update(running)  # what a heartbeat sent now would carry
            await release.wait()
            return SimpleNamespace(
                exit_code=0, logs=SimpleNamespace(stdout=lines("tick 1"), stderr=[])
            )

    settings = Settings.model_construct(sandbox_command_timeout_s=600)
    backend = EnvironmentBackend(Environments(settings))  # ty: ignore[invalid-argument-type]

    async def sandbox(thread_id: str, user_sub: str) -> SimpleNamespace:
        return SimpleNamespace(id="sandbox-1", commands=Commands())

    monkeypatch.setattr(backend, "_chat", lambda: ("thread-1", "sub-1"))
    monkeypatch.setattr(backend.environments, "sandbox", sandbox)

    # The first attempt: the command is in the turn's record while it runs, and leaves it after
    running: dict[str, dict[str, str]] = {}
    environments.RUNNING.set(running)
    task = asyncio.create_task(backend.aexecute("./loop.sh"))
    await asyncio.sleep(0.01)
    assert seen == {"exec-1": {"sandbox": "sandbox-1", "command": "./loop.sh"}}
    release.set()
    first = await task
    assert running == {} and first.output == "tick 1"

    # The retry: what the crashed attempt's last heartbeat carried is stopped, in its sandbox
    interrupted: list[tuple[str, str]] = []

    async def connect(sandbox_id: str, **_: object) -> SimpleNamespace:
        async def interrupt(execution_id: str) -> None:
            interrupted.append((sandbox_id, execution_id))

        return SimpleNamespace(commands=SimpleNamespace(interrupt=interrupt))

    monkeypatch.setattr(environments.Sandbox, "connect", connect)
    stopped = await environments.stop_left(settings, seen)
    assert interrupted == [("sandbox-1", "exec-1")] and stopped == ["./loop.sh"]

    # The same command run again says so, once
    environments.STOPPED.set(set(stopped))
    again = await backend.aexecute("./loop.sh")
    assert again.output.startswith(environments.RESTARTED) and again.output.endswith(
        "tick 1"
    )
    assert (await backend.aexecute("./loop.sh")).output == "tick 1"


@pytest.mark.asyncio
async def test_a_sandbox_removed_while_its_workflow_held_it_is_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removed behind its workflow's back (its secrets couldn't be rewritten, an operator): the
    worker, connecting afresh, ends the workflow and asks again, rather than failing the turn on
    every Retry (P4-E4). OpenSandbox not answering is told to the agent."""
    signals: list[tuple[str, str, object]] = []
    handed: list[str] = ["sandbox-1", "sandbox-2"]
    connects: list[str] = []
    down = {"now": False}

    class Handle:
        def __init__(self, workflow_id: str) -> None:
            self.workflow_id = workflow_id

        async def signal(self, name: str, arg: object) -> None:
            signals.append((self.workflow_id, name, arg))

    async def acquire(thread_id: str, user_sub: str) -> str:
        return handed[0]

    async def connect(sandbox_id: str, **kwargs) -> SimpleNamespace:
        connects.append(sandbox_id)
        if down["now"]:
            raise SandboxApiException("Get endpoint failed", status_code=502)
        if sandbox_id == "sandbox-1":
            handed.pop(0)  # the workflow, ended, is followed by one with a new sandbox
            raise SandboxApiException("Sandbox sandbox-1 not found", status_code=404)
        return SimpleNamespace(id=sandbox_id)

    envs = Environments(Settings.model_construct())
    envs.temporal = SimpleNamespace(get_workflow_handle=Handle)  # ty: ignore[invalid-assignment]
    monkeypatch.setattr(envs, "_acquire", acquire)
    monkeypatch.setattr(environments.Sandbox, "connect", connect)

    sandbox = await envs.sandbox("thread-1", "sub-1")
    assert sandbox.id == "sandbox-2" and connects == ["sandbox-1", "sandbox-2"]
    assert signals == [("environment-thread-1", END_SIGNAL, "sandbox-1")]

    down["now"] = True
    envs._chats.clear()
    with pytest.raises(
        environments.EnvironmentUnavailable, match="OpenSandbox didn't answer"
    ):
        await envs.sandbox("thread-1", "sub-1")


@pytest.mark.asyncio
async def test_renewing_a_sandbox_already_gone_is_nothing_to_do(monkeypatch) -> None:
    """The command that asked finds it gone and ends it; a failed renewal failed the ask instead,
    with "couldn't start" (P4-E4). OpenSandbox not reached skips it; other errors still count."""
    raised: list[Exception] = [
        SandboxApiException("Sandbox sb-1 not found", status_code=404),
        SandboxConnectionException("Network connectivity error"),  # OpenSandbox stopped
        SandboxApiException("OpenSandbox is down", status_code=502),
    ]

    class Manager:
        async def renew_sandbox(self, sandbox_id: str, until) -> None:
            raise raised.pop(0)

        async def close(self) -> None:
            pass

    async def create(**kwargs) -> Manager:
        return Manager()

    monkeypatch.setattr(environments.SandboxManager, "create", create)
    settings = Settings.model_construct(
        sandbox_url="http://gen9-sandbox:8090", sandbox_api_key=None
    )
    await environments.renew(settings, "sb-1", 3600)
    await environments.renew(settings, "sb-1", 3600)
    with pytest.raises(SandboxApiException):
        await environments.renew(settings, "sb-1", 3600)


class GrownFiles:
    """An environment's files as execd answers a byte range: at most its end + 1 bytes."""

    def __init__(self, sizes: dict[str, int]) -> None:
        self.sizes = sizes
        self.ranges: list[str | None] = []

    async def read_bytes(self, path: str, range_header: str | None = None) -> bytes:
        self.ranges.append(range_header)
        size = self.sizes[path]
        if range_header:
            end = int(range_header.removeprefix("bytes=0-"))
            size = min(size, end + 1)
        return b"x" * size


@pytest.mark.asyncio
async def test_a_file_grown_after_its_listing_is_read_no_further_than_the_cap() -> None:
    # P7-F2: listed small, then grown to 9 GB by a process left running in the environment
    files = GrownFiles(
        {"/work/out/a.txt": 40, "/work/out/big.bin": 9 * 1024**3, "/work/out/empty": 0}
    )
    assert await chat_files.read_capped(files, "/work/out/a.txt", 1000) == b"x" * 40
    assert await chat_files.read_capped(files, "/work/out/big.bin", 1000) is None
    assert await chat_files.read_capped(files, "/work/out/empty", 1000) == b""
    assert files.ranges == ["bytes=0-1000"] * 3
