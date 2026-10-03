"""The Activities of the deletion workflows (`workflows/deletion.py`), bound to what the worker
holds: the runtime (database, checkpointer), the Temporal client and the Keycloak admin client.
Every one is safe to repeat: deleting something already gone succeeds."""

import logging
import re
import uuid
from datetime import datetime

import httpx
from sqlalchemy import delete, func, select
from temporalio import activity
from temporalio.api.common.v1 import WorkflowExecution
from temporalio.api.workflowservice.v1 import DeleteWorkflowExecutionRequest
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from . import accounts
from .keycloak_admin import KeycloakAdmin, KeycloakAdminError
from .langfuse_erasure import erase_session_traces, erase_user_traces
from .models import AuditEvent, Connector, Thread, User
from .runs.control import stop_thread_runs
from .runtime import Runtime
from .temporal import heartbeating
from .workflows.deletion import DeletedUser, EraseTraces, RunHistories
from .workflows.names import (
    DELETE_KEYCLOAK_USER,
    DELETE_RUN_HISTORIES,
    DELETE_THREAD_DATA,
    DELETE_USER_DATA,
    DISABLE_KEYCLOAK_USER,
    ERASE_MODEL_USAGE,
    ERASE_TRACES,
    FIND_DELETED_USERS,
    REVOKE_CONNECTOR_TOKENS,
    STOP_THREAD_RUNS,
)

log = logging.getLogger(__name__)

# Values interpolated into a visibility query: ids only
_ID = re.compile(r"^[0-9A-Za-z-]{1,64}$")


def _id(value: str) -> str:
    if not _ID.match(value):
        raise ValueError(f"not an id: {value!r}")
    return value


class DeletionActivities:
    def __init__(
        self, runtime: Runtime, temporal: Client, keycloak: KeycloakAdmin | None
    ) -> None:
        self.runtime = runtime
        self.temporal = temporal
        self.keycloak = keycloak

    def _keycloak(self) -> KeycloakAdmin:
        if self.keycloak is None:
            raise RuntimeError("KEYCLOAK_ADMIN_CLIENT_SECRET is not set on the worker")
        return self.keycloak

    @activity.defn(name=STOP_THREAD_RUNS)
    async def stop_thread_runs(self, thread_id: str) -> None:
        async with heartbeating():
            await stop_thread_runs(
                self.runtime.engine, self.temporal, [uuid.UUID(thread_id)]
            )

    @activity.defn(name=ERASE_TRACES)
    async def erase_traces(self, request: EraseTraces) -> int:
        since = datetime.fromisoformat(request.since)
        async with heartbeating():
            if request.session:
                return await erase_session_traces(request.session, since=since)
            assert request.user
            return await erase_user_traces(request.user, since=since)

    @activity.defn(name=DELETE_RUN_HISTORIES)
    async def delete_run_histories(self, request: RunHistories) -> int:
        """Deletes the run and environment workflows of a thread or a user from Temporal right
        away, instead of leaving them for the namespace's retention period (a running one, whose
        sandbox is already removed, is ended by its deletion)."""
        if request.thread:
            query = f"Gen9Kind IN ('run', 'environment') AND Gen9Thread = '{_id(request.thread)}'"
        else:
            assert request.user
            query = f"Gen9Kind IN ('run', 'environment', 'task') AND Gen9User = '{_id(request.user)}'"
        deleted = 0
        async with heartbeating():
            async for execution in self.temporal.list_workflows(query):
                try:
                    await self.temporal.workflow_service.delete_workflow_execution(
                        DeleteWorkflowExecutionRequest(
                            namespace=self.temporal.namespace,
                            workflow_execution=WorkflowExecution(
                                workflow_id=execution.id, run_id=execution.run_id
                            ),
                        )
                    )
                    deleted += 1
                except RPCError as e:
                    if e.status != RPCStatusCode.NOT_FOUND:
                        raise
        return deleted

    @activity.defn(name=DELETE_THREAD_DATA)
    async def delete_thread_data(self, thread_id: str) -> None:
        await self.runtime.checkpointer.adelete_thread(thread_id)
        async with self.runtime.engine.begin() as conn:
            # Its runs and their events cascade
            await conn.execute(delete(Thread).where(Thread.id == uuid.UUID(thread_id)))

    @activity.defn(name=DISABLE_KEYCLOAK_USER)
    async def disable_keycloak_user(self, sub: str) -> None:
        """Nobody can sign in while the deletion runs, and every session ends now."""
        keycloak = self._keycloak()
        try:
            await keycloak.set_enabled(sub, False)
            await keycloak.logout(sub)
        except KeycloakAdminError as e:
            if e.status_code != 404:
                raise

    @activity.defn(name=ERASE_MODEL_USAGE)
    async def erase_model_usage(self, sub: str) -> dict:
        """The model router's records of the user: spend logs, daily totals, end-user row
        (gen9-models' admin API, `POST /users/{sub}/erase`). Repeating it deletes nothing more."""
        settings = self.runtime.settings
        async with httpx.AsyncClient(timeout=30) as http:
            response = await http.post(
                f"{settings.gen9_models_admin_url}/users/{_id(sub)}/erase",
                headers={
                    "Authorization": f"Bearer {settings.gen9_models_key.get_secret_value()}"
                },
            )
            response.raise_for_status()
            deleted = response.json()["deleted"]
        log.info("erased the model router's records of %s: %s", sub, deleted)
        return deleted

    @activity.defn(name=REVOKE_CONNECTOR_TOKENS)
    async def revoke_connector_tokens(self, sub: str) -> int:
        """The user's signed-in connectors' tokens, revoked at their servers (RFC 7009) before the
        connectors go with the user's data. Best effort per connector (connectors.py); how many
        had something revoked. Repeating it revokes tokens already revoked, which servers accept."""
        async with self.runtime.engine.connect() as conn:
            rows = list(
                await conn.execute(
                    select(Connector)
                    .join(User, User.id == Connector.user_id)
                    .where(User.sub == sub, Connector.sealed_tokens.is_not(None))
                )
            )
        revoked = 0
        for row in rows:
            if await self.runtime.connectors.revoke(row, sub):
                revoked += 1
        return revoked

    @activity.defn(name=DELETE_USER_DATA)
    async def delete_user_data(self, sub: str) -> None:
        async with heartbeating(), self.runtime.sessionmaker() as session:
            await accounts.delete_user_data(
                sub,
                session,
                self.runtime.engine,
                self.runtime.checkpointer,
                self.temporal,
                self.runtime.store,
            )

    @activity.defn(name=DELETE_KEYCLOAK_USER)
    async def delete_keycloak_user(self, sub: str) -> None:
        try:
            await self._keycloak().delete_user(sub)
        except KeycloakAdminError as e:
            if e.status_code != 404:
                raise

    @activity.defn(name=FIND_DELETED_USERS)
    async def find_deleted_users(self, allow: int = 0) -> list[DeletedUser]:
        """The people to delete, or none while too many are missing at once (accounts.sweep_holds),
        unless an admin allowed that many (gen9-agent-sweep --allow N)."""
        if self.keycloak is None:
            return []
        async with self.runtime.sessionmaker() as session:
            missing = await accounts.find_deleted_users(session, self.keycloak)
            known = await session.scalar(select(func.count()).select_from(User)) or 0
            limit = self.runtime.settings.sweep_max_deletions
            held = accounts.sweep_holds(len(missing), known, limit)
            if held and len(missing) > allow:
                log.error(
                    "deleted-users sweep held, nobody deleted: %s. People deleted on purpose: "
                    "gen9-agent-sweep shows them, gen9-agent-sweep --allow %d deletes them",
                    held,
                    len(missing),
                )
                session.add(
                    AuditEvent(
                        actor="sweep",
                        action="account.sweep.held",
                        outcome="denied",
                        where="SweepDeletedUsersWorkflow",
                        detail={
                            "missing": len(missing),
                            "known": known,
                            "limit": limit,
                        },
                    )
                )
                await session.commit()
                return []
            # Each one's deletion recorded, as the person's or an admin's is: the record `make
            # restore` reads to delete it again, should a backup bring it back (P4-E5). A retried
            # attempt doesn't record one twice
            recorded = set(
                await session.scalars(
                    select(AuditEvent.target).where(
                        AuditEvent.action == "account.sweep",
                        AuditEvent.target.in_([m.sub for m in missing]),
                    )
                )
            )
            for m in missing:
                if m.sub not in recorded:
                    session.add(
                        AuditEvent(
                            actor="sweep",
                            action="account.sweep",
                            outcome="success",
                            target=m.sub,
                            where="SweepDeletedUsersWorkflow",
                            detail={},
                        )
                    )
            await session.commit()
        return [DeletedUser(sub=m.sub, since=m.since.isoformat()) for m in missing]

    def all(self) -> list:
        return [
            self.stop_thread_runs,
            self.erase_traces,
            self.delete_run_histories,
            self.delete_thread_data,
            self.disable_keycloak_user,
            self.revoke_connector_tokens,
            self.delete_user_data,
            self.erase_model_usage,
            self.delete_keycloak_user,
            self.find_deleted_users,
        ]
