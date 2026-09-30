"""A chat's environment's Activities (workflows/environment.py): create, renew and remove its
sandbox at OpenSandbox, and remove every sandbox of a deleted account. Each is safe to repeat:
creating finds a sandbox an earlier attempt made, removing one already gone succeeds."""

import asyncio

from sqlalchemy import select
from temporalio import activity
from temporalio.client import Client, WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError, RPCStatusCode

from . import environments
from .models import Thread
from .runtime import Runtime
from .workflows.environment import (
    CheckEnvironment,
    CreateEnvironment,
    RenewEnvironment,
)
from .workflows.names import (
    CHECK_ENVIRONMENT_SECRETS,
    CREATE_ENVIRONMENT,
    END_SIGNAL,
    REFRESH_ENVIRONMENT_SECRETS,
    REMOVE_ENVIRONMENT,
    REMOVE_THREAD_ENVIRONMENT,
    REMOVE_USER_ENVIRONMENTS,
    RENEW_ENVIRONMENT,
    environment_workflow_id,
)

# How long a deleted chat's environment workflow is given to remove its sandbox (its removal
# retries for about 15 s), within the deletion step's heartbeat timeout of a minute
ENDING_S = 45


class EnvironmentActivities:
    def __init__(self, runtime: Runtime, temporal: Client) -> None:
        self.runtime = runtime
        self.temporal = temporal

    @activity.defn(name=CREATE_ENVIRONMENT)
    async def create_environment(self, env: CreateEnvironment) -> str:
        secrets = await environments.secrets_of(
            self.runtime.engine, self.runtime.vault, env.user_sub
        )
        sandbox_id = await environments.create(
            self.runtime.settings, env.thread_id, env.user_sub, env.timeout_s, secrets
        )
        # A chat or an account deleted while this ran: the deletion took away this environment's
        # workflow, and swept the sandboxes, before this one existed; nothing would remove it until
        # it expired, an hour on (found by hand: manual-e2e.md, P3-Z1). So it goes now. A chat
        # being deleted is marked at once; an account's deletion first disables the person
        if not await self._wanted(env):
            await environments.remove(self.runtime.settings, [sandbox_id])
            raise ApplicationError(
                "The chat or its account was deleted while its environment started",
                non_retryable=True,
            )
        return sandbox_id

    async def _wanted(self, env: CreateEnvironment) -> bool:
        async with self.runtime.engine.connect() as conn:
            alive = await conn.scalar(
                select(Thread.id).where(
                    Thread.id == env.thread_id, Thread.deleted_at.is_(None)
                )
            )
        return alive is not None and await self.runtime.standing.active(
            env.user_sub, fresh=True
        )

    @activity.defn(name=REFRESH_ENVIRONMENT_SECRETS)
    async def refresh_environment_secrets(self, user_sub: str) -> int:
        """The person's running environments, rewritten to their current secrets. One that can't
        be updated is removed: closed, never left with a secret they took away. How many were."""
        settings = self.runtime.settings
        if not settings.sandbox_url:
            return 0
        secrets = await environments.secrets_of(
            self.runtime.engine, self.runtime.vault, user_sub
        )
        updated = 0
        for sandbox_id in await environments.of_user(settings, user_sub):
            try:
                await environments.apply_secrets(settings, sandbox_id, secrets)
                updated += 1
            except Exception as e:  # noqa: BLE001 (any failure closes it, whatever its kind)
                activity.logger.warning(
                    "environment %s not updated, so removed: %s", sandbox_id, e
                )
                await environments.remove(settings, [sandbox_id])
        return updated

    @activity.defn(name=CHECK_ENVIRONMENT_SECRETS)
    async def check_environment_secrets(self, env: CheckEnvironment) -> bool:
        """A running environment's vault and rules put back to the person's secrets if they no
        longer match (its egress sidecar restarted: environments.drifted). Whether they had. One
        that can't be checked is left to its next command, which finds it gone if it is."""
        settings = self.runtime.settings
        try:
            secrets = await environments.secret_hosts_of(
                self.runtime.engine, env.user_sub
            )
            if not await environments.drifted(settings, env.sandbox_id, secrets):
                return False
            activity.logger.warning(
                "environment %s: its vault or rules no longer matched the person's secrets "
                "(its egress restarted); rewritten",
                env.sandbox_id,
            )
            opened = await environments.secrets_of(
                self.runtime.engine, self.runtime.vault, env.user_sub
            )
            await environments.apply_secrets(settings, env.sandbox_id, opened)
            return True
        except Exception as e:  # noqa: BLE001 (the command goes on either way)
            activity.logger.warning(
                "environment %s: secrets not checked: %s", env.sandbox_id, e
            )
            return False

    @activity.defn(name=RENEW_ENVIRONMENT)
    async def renew_environment(self, env: RenewEnvironment) -> None:
        await environments.renew(self.runtime.settings, env.sandbox_id, env.timeout_s)

    @activity.defn(name=REMOVE_ENVIRONMENT)
    async def remove_environment(self, sandbox_id: str) -> None:
        await environments.remove(self.runtime.settings, [sandbox_id])

    @activity.defn(name=REMOVE_THREAD_ENVIRONMENT)
    async def remove_thread_environment(self, thread_id: str) -> int:
        """A deleted chat's environment: its workflow ended (no new commands start one there), its
        sandbox removed. None when environments are off."""
        handle = self.temporal.get_workflow_handle(environment_workflow_id(thread_id))
        try:
            await handle.signal(END_SIGNAL)
            # Ending, the workflow removes its sandbox: wait for it, so the list below finds only
            # what none removes. Removing it here as well raced that removal, and Docker refused
            # the second one ("removal … already in progress", a 500 from OpenSandbox 1.1.0): a
            # failed attempt on every such chat's deletion (manual-e2e.md, P3-C13)
            await asyncio.wait_for(handle.result(), ENDING_S)
        except RPCError as e:
            if e.status != RPCStatusCode.NOT_FOUND:
                raise
        except (TimeoutError, WorkflowFailureError):
            pass  # its sandbox is removed below instead
        if not self.runtime.settings.sandbox_url:
            return 0
        found = await environments.of_thread(self.runtime.settings, thread_id)
        return await environments.remove(self.runtime.settings, found)

    @activity.defn(name=REMOVE_USER_ENVIRONMENTS)
    async def remove_user_environments(self, user_sub: str) -> int:
        """Every sandbox of a person, for their account's deletion: none when environments are off."""
        if not self.runtime.settings.sandbox_url:
            return 0
        found = await environments.of_user(self.runtime.settings, user_sub)
        return await environments.remove(self.runtime.settings, found)

    def all(self) -> list:
        return [
            self.check_environment_secrets,
            self.create_environment,
            self.refresh_environment_secrets,
            self.renew_environment,
            self.remove_environment,
            self.remove_thread_environment,
            self.remove_user_environments,
        ]
