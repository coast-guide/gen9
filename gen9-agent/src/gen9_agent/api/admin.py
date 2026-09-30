"""User management for Gen9 admins (realm role gen9-admin), backed by the Keycloak Admin API."""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import aliased
from temporalio.common import (
    Priority,
    SearchAttributePair,
    TypedSearchAttributes,
    WorkflowIDConflictPolicy,
)

from .. import audit
from ..auth import Principal, require_recent_authentication
from ..deletions import (
    ACCOUNT_WAIT_S,
    finished_or_accepted,
    start_account_deletion,
    sweep_deleted_users,
)
from ..deps import Admin, AdminPrincipal, Session
from ..keycloak_admin import KeycloakAdminError
from ..models import AuditEvent, User
from ..runs import control
from ..temporal import GEN9_KIND
from ..workflows.directory import SyncDirectoryWorkflow
from ..workflows.names import (
    DIRECTORY_NOW_WORKFLOW_ID,
    PRIORITY_MAINTENANCE,
    REINDEX_NOW_WORKFLOW_ID,
    SYSTEM_QUEUE,
)
from ..workflows.search import ReindexInput, ReindexSearchWorkflow

router = APIRouter(prefix="/v1/admin", tags=["admin"])
_recent = require_recent_authentication(max_age=300)


async def _recent_admin(principal: AdminPrincipal) -> Principal:
    return await _recent(principal)


# Deleting users also needs a sign-in from the last 5 minutes (RFC 9470 step-up otherwise)
RecentAdmin = Annotated[Principal, Depends(_recent_admin)]


class UserOut(BaseModel):
    id: str
    email: str | None
    first_name: str | None
    last_name: str | None
    enabled: bool
    email_verified: bool
    created_at_ms: int | None
    is_admin: bool
    required_actions: list[str]
    locked: bool = False


class UserPage(BaseModel):
    users: list[UserOut]
    total: int


class UserPatch(BaseModel):
    enabled: bool | None = None
    is_admin: bool | None = None


def _raise(exc: KeycloakAdminError) -> NoReturn:
    raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("/users", summary="List users")
async def list_users(
    _: AdminPrincipal,
    keycloak: Admin,
    search: Annotated[str | None, Query(max_length=100)] = None,
    first: Annotated[int, Query(ge=0)] = 0,
    max: Annotated[int, Query(ge=1, le=100)] = 50,
) -> UserPage:
    try:
        users = await keycloak.list_users(search, first, max)
        total = await keycloak.count_users(search)
    except KeycloakAdminError as exc:
        _raise(exc)
    return UserPage(users=[UserOut(**u) for u in users], total=total)


@router.patch(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Enable/disable, grant/revoke admin",
)
async def patch_user(
    user_id: str,
    patch: UserPatch,
    principal: AdminPrincipal,
    keycloak: Admin,
    request: Request,
) -> None:
    # Admins cannot lock themselves out
    if user_id == principal.sub and (patch.enabled is False or patch.is_admin is False):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "You cannot disable or demote your own account"
        )
    try:
        stopped = None
        if patch.enabled is not None:
            await keycloak.set_enabled(user_id, patch.enabled)
            request.app.state.runtime.standing.tell(user_id, patch.enabled)
            if not patch.enabled:
                await keycloak.logout(user_id)
                # Nothing of theirs acts after: their running, waiting and queued runs end now,
                # not when each would next ask (P5-C10; standing.py)
                stopped = await control.stop_runs_of(
                    request.app.state.engine, request.app.state.temporal, user_id
                )
        if patch.is_admin is not None:
            await keycloak.set_admin(user_id, patch.is_admin)
    except KeycloakAdminError as exc:
        _raise(exc)
    changed = {
        "enabled": patch.enabled,
        "admin": patch.is_admin,
        "runs_stopped": stopped,
    }
    await audit.record(
        request,
        principal.sub,
        "admin.user.update",
        target=user_id,
        detail={k: v for k, v in changed.items() if v is not None},
    )


@router.post(
    "/users/{user_id}/unlock",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Clear a sign-in lockout (too many failed attempts)",
)
async def unlock_user(
    user_id: str, principal: AdminPrincipal, keycloak: Admin, request: Request
) -> None:
    try:
        await keycloak.unlock(user_id)
    except KeycloakAdminError as exc:
        _raise(exc)
    await audit.record(request, principal.sub, "admin.user.unlock", target=user_id)


@router.post(
    "/users/{user_id}/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign the user out everywhere",
)
async def logout_user(
    user_id: str, principal: AdminPrincipal, keycloak: Admin, request: Request
) -> None:
    try:
        await keycloak.logout(user_id)
    except KeycloakAdminError as exc:
        _raise(exc)
    await audit.record(request, principal.sub, "admin.user.logout", target=user_id)


@router.post(
    "/users/{user_id}/password-reset",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Email a password reset link",
)
async def reset_password(
    user_id: str, principal: AdminPrincipal, keycloak: Admin, request: Request
) -> dict[str, Any]:
    try:
        await keycloak.send_password_reset(user_id)
    except KeycloakAdminError as exc:
        _raise(exc)
    await audit.record(
        request, principal.sub, "admin.user.password_reset", target=user_id
    )
    return {"status": "sent"}


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a user and all their data (needs a sign-in from the last 5 minutes)",
    responses={202: {"description": "Deleting; it finishes on its own"}},
)
async def delete_user(
    user_id: str,
    principal: RecentAdmin,
    keycloak: Admin,
    session: Session,
    request: Request,
) -> Response:
    """The same DeleteAccountWorkflow as `DELETE /v1/me` (docs/temporal.md)."""
    if user_id == principal.sub:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Delete your own account from Settings."
        )
    try:
        target = await keycloak.get_user(user_id)
    except KeycloakAdminError as exc:
        _raise(exc)
    # Their traces can't be older than their first Gen9 visit, or else their Keycloak account
    since = await session.scalar(
        select(User.created_at).where(User.sub == user_id)
    ) or datetime.fromtimestamp(target["createdTimestamp"] / 1000, UTC)
    handle = await start_account_deletion(request.app.state.temporal, user_id, since)
    await audit.record(request, principal.sub, "admin.user.delete", target=user_id)
    return await finished_or_accepted(handle, ACCOUNT_WAIT_S)


class SweepResult(BaseModel):
    removed: int


@router.post(
    "/users/remove-deleted",
    summary="Remove Gen9's data of users deleted in Keycloak directly (a Schedule also runs it)",
)
async def remove_deleted(principal: AdminPrincipal, request: Request) -> SweepResult:
    """Runs SweepDeletedUsersWorkflow now and waits for it: it starts a DeleteAccountWorkflow for
    each missing user and answers how many (`removed`); each goes on by itself, its data gone in
    seconds unless a step waits on a service that is down, its late trace passes for ten minutes."""
    removed = await sweep_deleted_users(request.app.state.temporal)
    await audit.record(
        request,
        principal.sub,
        "admin.users.remove_deleted",
        detail={"removed": removed},
    )
    return SweepResult(removed=removed)


class ReindexStarted(BaseModel):
    workflow_id: str
    run_id: str | None


@router.post(
    "/search/reindex",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Make older chats searchable by meaning now (a Schedule also runs it)",
)
async def reindex_search(principal: AdminPrincipal, request: Request) -> ReindexStarted:
    """Starts ReindexSearchWorkflow and returns at once: after a change of the router's `embed`
    model it re-embeds every chat, which takes a while. One at a time: asking again while it runs
    returns the same workflow."""
    handle = await request.app.state.temporal.start_workflow(
        ReindexSearchWorkflow.run,
        ReindexInput(),
        id=REINDEX_NOW_WORKFLOW_ID,
        task_queue=SYSTEM_QUEUE,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        search_attributes=TypedSearchAttributes(
            [SearchAttributePair(GEN9_KIND, "reindex")]
        ),
        priority=Priority(priority_key=PRIORITY_MAINTENANCE),
    )
    await audit.record(request, principal.sub, "admin.search.reindex")
    return ReindexStarted(workflow_id=handle.id, run_id=handle.result_run_id)


@router.post(
    "/directory/sync",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Update the connector directory's copy of the MCP registry now (a Schedule also does)",
)
async def sync_directory(principal: AdminPrincipal, request: Request) -> ReindexStarted:
    """Starts SyncDirectoryWorkflow and returns at once. One at a time: asking again while it runs
    returns the same workflow."""
    handle = await request.app.state.temporal.start_workflow(
        SyncDirectoryWorkflow.run,
        id=DIRECTORY_NOW_WORKFLOW_ID,
        task_queue=SYSTEM_QUEUE,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        search_attributes=TypedSearchAttributes(
            [SearchAttributePair(GEN9_KIND, "directory")]
        ),
        priority=Priority(priority_key=PRIORITY_MAINTENANCE),
    )
    await audit.record(request, principal.sub, "admin.directory.sync")
    return ReindexStarted(workflow_id=handle.id, run_id=handle.result_run_id)


class AuditEventOut(BaseModel):
    id: int
    at: datetime
    # The person's sub (or who else acted), and their email when Gen9 knows them
    actor: str
    actor_email: str | None
    action: str
    outcome: str
    # What it was done to, and the person's email when it's someone Gen9 knows
    target: str | None
    target_email: str | None
    where: str | None
    detail: dict[str, Any]


@router.get(
    "/audit", summary="Who did what: admin actions, security actions, access refused"
)
async def list_audit(
    _: AdminPrincipal,
    session: Session,
    actor: str | None = None,
    action: str | None = None,
    outcome: Literal["success", "denied"] | None = None,
    before: int | None = Query(default=None, description="Events older than this id"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[AuditEventOut]:
    """Newest first (audit.py). `action` matches a prefix (`admin.` for every admin action)."""
    target = aliased(User)
    query = (
        select(AuditEvent, User.email, target.email)
        .outerjoin(User, User.sub == AuditEvent.actor)
        .outerjoin(target, target.sub == AuditEvent.target)
        .order_by(AuditEvent.id.desc())
        .limit(limit)
    )
    if actor:
        query = query.where(AuditEvent.actor == actor)
    if action:
        query = query.where(AuditEvent.action.startswith(action, autoescape=True))
    if outcome:
        query = query.where(AuditEvent.outcome == outcome)
    if before is not None:
        query = query.where(AuditEvent.id < before)
    rows = (await session.execute(query)).all()
    return [
        AuditEventOut(
            id=e.id,
            at=e.at,
            actor=e.actor,
            actor_email=email,
            action=e.action,
            outcome=e.outcome,
            target=e.target,
            target_email=target_email,
            where=e.where,
            detail=e.detail,
        )
        for e, email, target_email in rows
    ]
