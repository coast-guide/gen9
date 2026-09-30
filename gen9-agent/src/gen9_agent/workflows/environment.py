"""A chat's environment (environments.py): one workflow per chat that has one,
`environment-<thread>`. It owns the chat's sandbox from first use to removal, so a crash never
leaves a container running:
- the `acquire` Update returns the sandbox, creating it the first time (one creation, however many
  calls ask at once), renewing OpenSandbox's own timeout while it's used, and putting its vault
  and rules back to the person's secrets if its egress sidecar lost them;
- after `idle_s` without an `acquire`, or on the `end` Signal (the chat or its account deleted,
  or its sandbox stopped), it removes the sandbox and completes. A later command starts a new
  workflow, with a new sandbox.
OpenSandbox's timeout (twice the idle time, renewed) removes the container even if Temporal can't.
"""

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

from .names import (
    ACQUIRE_UPDATE,
    CHECK_ENVIRONMENT_SECRETS,
    CREATE_ENVIRONMENT,
    END_SIGNAL,
    REFRESH_ENVIRONMENT_SECRETS,
    REMOVE_ENVIRONMENT,
    RENEW_ENVIRONMENT,
    SYSTEM_QUEUE,
)


@dataclass(frozen=True)
class EnvironmentInput:
    thread_id: str
    user_sub: str
    idle_s: int
    # Carried over when the workflow continues as new
    sandbox_id: str | None = None


@dataclass(frozen=True)
class CreateEnvironment:
    thread_id: str
    user_sub: str
    # OpenSandbox's own timeout for it: the backstop
    timeout_s: int


@dataclass(frozen=True)
class RenewEnvironment:
    sandbox_id: str
    timeout_s: int


@dataclass(frozen=True)
class CheckEnvironment:
    sandbox_id: str
    user_sub: str


QUICK = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2,
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=5,
)


@workflow.defn
class EnvironmentWorkflow:
    # The input in __init__: with Update-with-Start, `acquire` can run before `run` begins
    @workflow.init
    def __init__(self, env: EnvironmentInput) -> None:
        self._env = env
        self._sandbox_id: str | None = env.sandbox_id
        self._used = False
        self._ended = False
        self._leaving = False
        self._creating = False
        self._renewed_at = workflow.time()

    @workflow.run
    async def run(self, env: EnvironmentInput) -> None:
        while True:
            self._used = False
            try:
                await workflow.wait_condition(
                    lambda: self._used or self._ended,
                    timeout=timedelta(seconds=env.idle_s),
                )
            except TimeoutError:
                break  # unused for idle_s
            if self._ended:
                break
            if workflow.info().is_continue_as_new_suggested():
                await workflow.wait_condition(workflow.all_handlers_finished)
                workflow.continue_as_new(
                    EnvironmentInput(
                        env.thread_id, env.user_sub, env.idle_s, self._sandbox_id
                    )
                )
        # No new callers from here: they start the next workflow once this one completes
        self._leaving = True
        await workflow.wait_condition(lambda: not self._creating)
        if self._sandbox_id is not None:
            await workflow.execute_activity(
                REMOVE_ENVIRONMENT,
                self._sandbox_id,
                task_queue=SYSTEM_QUEUE,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=QUICK,
            )

    @workflow.update(name=ACQUIRE_UPDATE)
    async def acquire(self) -> str:
        """The chat's sandbox id, created if it has none yet."""
        self._used = True
        existing = self._sandbox_id is not None
        if self._sandbox_id is None:
            if self._creating:
                await workflow.wait_condition(lambda: not self._creating)
            else:
                self._creating = True
                try:
                    self._sandbox_id = await workflow.execute_activity(
                        CREATE_ENVIRONMENT,
                        CreateEnvironment(
                            self._env.thread_id,
                            self._env.user_sub,
                            2 * self._env.idle_s,
                        ),
                        task_queue=SYSTEM_QUEUE,
                        result_type=str,
                        start_to_close_timeout=timedelta(minutes=3),
                        retry_policy=QUICK,
                    )
                    self._renewed_at = workflow.time()
                finally:
                    self._creating = False
            if self._sandbox_id is None:
                raise ApplicationError("The environment couldn't be created.")
        elif workflow.time() - self._renewed_at > self._env.idle_s / 2:
            # Keep OpenSandbox's backstop a full idle time ahead of the last use
            self._renewed_at = workflow.time()
            await workflow.execute_activity(
                RENEW_ENVIRONMENT,
                RenewEnvironment(self._sandbox_id, 2 * self._env.idle_s),
                task_queue=SYSTEM_QUEUE,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=QUICK,
            )
        if existing and workflow.patched("check-secrets"):
            # Its vault and rules as the person's secrets are, in case its egress sidecar restarted
            # and lost them (environments.drifted): at most once a minute, as acquire is asked
            await workflow.execute_activity(
                CHECK_ENVIRONMENT_SECRETS,
                CheckEnvironment(self._sandbox_id, self._env.user_sub),
                task_queue=SYSTEM_QUEUE,
                result_type=bool,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
        return self._sandbox_id

    @acquire.validator
    def _acquire_while_open(self) -> None:
        if self._ended or self._leaving:
            raise ApplicationError("This environment is closing.", type="Leaving")

    @workflow.signal(name=END_SIGNAL)
    def end(self, sandbox_id: str | None = None) -> None:
        """The chat or its account is being deleted, or its sandbox stopped (`sandbox_id`: only
        if it's still this one's): remove the sandbox now. The next command starts a new one."""
        if sandbox_id is None or sandbox_id == self._sandbox_id:
            self._ended = True


@workflow.defn
class RefreshEnvironmentsWorkflow:
    """A person's secrets changed (api/environment_secrets.py): their running environments' vaults
    and rules rewritten to match, within seconds. One per person
    (`refresh-environments-<sub>`); a newer change replaces one still running."""

    @workflow.run
    async def run(self, user_sub: str) -> int:
        return await workflow.execute_activity(
            REFRESH_ENVIRONMENT_SECRETS,
            user_sub,
            task_queue=SYSTEM_QUEUE,
            result_type=int,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=QUICK,
        )
