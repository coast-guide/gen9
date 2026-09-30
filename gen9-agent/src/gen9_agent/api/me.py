import json
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from .. import audit
from ..auth import CurrentPrincipal, Principal, require_recent_authentication
from ..deletions import ACCOUNT_WAIT_S, finished_or_accepted, start_account_deletion
from ..deps import Admin, Session
from ..keycloak_admin import KeycloakAdminError
from ..memory import MAX_MEMORY_CHARS, erase_memory, read_memory, write_memory
from ..model_router import budget_of
from ..users import CurrentUser

# Deleting the account needs a sign-in from the last 5 minutes (a stolen session isn't enough)
RecentlyAuthenticated = Annotated[
    Principal, Depends(require_recent_authentication(max_age=300))
]

router = APIRouter(prefix="/v1", tags=["me"])


class Me(BaseModel):
    id: UUID
    sub: str
    email: str | None
    name: str | None
    roles: list[str]
    client_id: str
    created_at: datetime


@router.get("/me", summary="The authenticated user, provisioned on first call")
async def me(principal: CurrentPrincipal, user: CurrentUser) -> Me:
    return Me(
        id=user.id,
        sub=user.sub,
        email=user.email,
        name=user.name,
        roles=sorted(r for r in principal.roles if r.startswith("gen9-")),
        client_id=principal.client_id,
        created_at=user.created_at,
    )


class Credential(BaseModel):
    id: str
    label: str | None
    created_at_ms: int | None


class RecoveryCodes(BaseModel):
    id: str
    remaining: int | None
    total: int | None
    created_at_ms: int | None


class Security(BaseModel):
    password_changed_at_ms: int | None
    authenticator_apps: list[Credential]
    passkeys: list[Credential]
    recovery_codes: RecoveryCodes | None


class MemoryOut(BaseModel):
    content: str  # Markdown: what Gen9 remembers about the caller ("" when nothing yet)
    updated_at: datetime | None


class MemoryIn(BaseModel):
    content: str = Field(max_length=MAX_MEMORY_CHARS)


@router.get("/me/memory", summary="What Gen9 remembers about the caller (memory.py)")
async def get_memory(user: CurrentUser, request: Request) -> MemoryOut:
    memory = await read_memory(request.app.state.runtime.store, user.sub)
    return MemoryOut(content=memory.content, updated_at=memory.updated_at)


@router.put("/me/memory", summary="Replace what Gen9 remembers about the caller")
async def put_memory(body: MemoryIn, user: CurrentUser, request: Request) -> MemoryOut:
    memory = await write_memory(request.app.state.runtime.store, user.sub, body.content)
    return MemoryOut(content=memory.content, updated_at=memory.updated_at)


@router.delete(
    "/me/memory",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Forget everything Gen9 remembers about the caller",
)
async def delete_memory(user: CurrentUser, request: Request) -> Response:
    await erase_memory(request.app.state.runtime.store, user.sub)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _emails(request: Request) -> bool:
    """Whether this Gen9 sends email notices: the worker has SMTP_URL, and the API is told
    (EMAIL_NOTICES) without holding it."""
    settings = request.app.state.runtime.settings
    return settings.smtp_url is not None or settings.email_notices == "true"


class NotificationsOut(BaseModel):
    # Which emails about background runs (notices.py): all, needs_you or never
    email: Literal["all", "needs_you", "never"]
    # This Gen9 can send emails (SMTP_URL is set)
    available: bool


class NotificationsIn(BaseModel):
    email: Literal["all", "needs_you", "never"]


@router.get(
    "/me/notifications", summary="Which emails you get about your scheduled tasks"
)
async def get_notifications(user: CurrentUser, request: Request) -> NotificationsOut:
    return NotificationsOut(
        email=user.notify,  # ty: ignore[invalid-argument-type]
        available=_emails(request),
    )


@router.put(
    "/me/notifications",
    summary="Choose which emails you get about your scheduled tasks",
)
async def put_notifications(
    body: NotificationsIn, user: CurrentUser, session: Session, request: Request
) -> NotificationsOut:
    user.notify = body.email
    await session.commit()
    return NotificationsOut(
        email=body.email,
        available=_emails(request),
    )


class Controls(BaseModel):
    """What the caller lets Gen9 do with their chats."""

    # Search and cite their past chats in a chat (past_chats.py)
    search_past_chats: bool
    # Remember things about them: off, memory is neither loaded nor written (memory.py)
    remember: bool


@router.get("/me/controls", summary="What you let Gen9 do with your chats")
async def get_controls(user: CurrentUser) -> Controls:
    return Controls(search_past_chats=user.search_past_chats, remember=user.remember)


class ControlsIn(BaseModel):
    """The controls to change; those left out stay as they are."""

    search_past_chats: bool | None = None
    remember: bool | None = None


@router.put("/me/controls", summary="Choose what Gen9 may do with your chats")
async def put_controls(
    body: ControlsIn, user: CurrentUser, session: Session
) -> Controls:
    if body.search_past_chats is not None:
        user.search_past_chats = body.search_past_chats
    if body.remember is not None:
        user.remember = body.remember
    await session.commit()
    return Controls(search_past_chats=user.search_past_chats, remember=user.remember)


class Limit(BaseModel):
    # Whether a limit applies to the caller's model use
    limited: bool
    # How much of it this period has used, 0 to 1 (a little more when the last call went over)
    used: float | None
    # When the period ends and it resets (UTC), the same for everyone on the default limit
    resets_at: datetime | None


@router.get(
    "/me/limit",
    summary="How much of your model usage limit you've used, and when it resets",
    responses={503: {"description": "The model router's admin API can't be reached"}},
)
async def get_limit(user: CurrentUser, request: Request) -> Limit:
    """From gen9-models' admin API (docs/plans/manual-e2e.md, P5-D1)."""
    budget = await budget_of(request.app.state.runtime.settings, user.sub)
    if budget is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Your usage can't be read right now."
        )
    limit = budget.get("max_usd")
    resets = budget.get("resets_at")
    return Limit(
        limited=limit is not None,
        used=None if not limit else round(budget.get("spent_usd", 0) / limit, 4),
        resets_at=datetime.fromisoformat(resets) if resets else None,
    )


@router.get("/me/security", summary="The caller's sign-in methods (no secrets)")
async def my_security(principal: CurrentPrincipal, keycloak: Admin) -> Security:
    try:
        credentials = await keycloak.credentials(principal.sub)
    except KeycloakAdminError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    password = next((c for c in credentials if c.get("type") == "password"), None)

    def of_type(*types: str) -> list[Credential]:
        return [
            Credential(
                id=c["id"], label=c.get("userLabel"), created_at_ms=c.get("createdDate")
            )
            for c in credentials
            if c.get("type") in types
        ]

    codes = next(
        (c for c in credentials if c.get("type") == "recovery-authn-codes"), None
    )
    # Counts only; the hashed codes stay in Keycloak's secretData, which the API never returns
    counts = json.loads(codes.get("credentialData") or "{}") if codes else {}
    return Security(
        password_changed_at_ms=password.get("createdDate") if password else None,
        authenticator_apps=of_type("otp"),
        passkeys=of_type("webauthn-passwordless", "webauthn"),
        recovery_codes=RecoveryCodes(
            id=codes["id"],
            remaining=counts.get("remainingCodes"),
            total=counts.get("totalCodes"),
            created_at_ms=codes.get("createdDate"),
        )
        if codes
        else None,
    )


class PruneResult(BaseModel):
    removed: bool


@router.post(
    "/me/recovery-codes/prune",
    summary="Remove the caller's recovery codes once no authenticator app is left",
)
async def prune_recovery_codes(
    principal: CurrentPrincipal, keycloak: Admin
) -> PruneResult:
    """Recovery codes back up an authenticator app. On their own they still count as a second
    factor, so Keycloak would ask for one at every sign-in: they go with the last app."""
    try:
        credentials = await keycloak.credentials(principal.sub)
        if any(c.get("type") == "otp" for c in credentials):
            return PruneResult(removed=False)
        codes = [c for c in credentials if c.get("type") == "recovery-authn-codes"]
        for c in codes:
            await keycloak.delete_credential(principal.sub, c["id"])
    except KeycloakAdminError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return PruneResult(removed=bool(codes))


@router.post(
    "/me/sign-out-everywhere",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="End all of the caller's Keycloak sessions (every device and app)",
)
async def sign_out_everywhere(principal: CurrentPrincipal, keycloak: Admin) -> None:
    try:
        await keycloak.logout(principal.sub)
    except KeycloakAdminError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.delete(
    "/me",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete the caller's account: sign-in off, traces, chats, agent memory, then the user",
    responses={202: {"description": "Deleting; it finishes on its own"}},
)
async def delete_me(
    principal: RecentlyAuthenticated,
    user: CurrentUser,
    keycloak: Admin,
    request: Request,
) -> Response:
    """DeleteAccountWorkflow: the Keycloak user is disabled and signed out first, then Langfuse
    traces, chats and memory, the runs' history in Temporal, and the Keycloak user last; each step
    retried until it succeeds (docs/temporal.md)."""
    try:
        # Nobody could manage users afterwards
        if principal.has_role("gen9-admin") and await keycloak.admin_ids() == {
            principal.sub
        }:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "You're the only admin. Make someone else an admin first.",
            )
    except KeycloakAdminError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    handle = await start_account_deletion(
        request.app.state.temporal, principal.sub, user.created_at
    )
    await audit.record(request, principal.sub, "account.delete", target=principal.sub)
    return await finished_or_accepted(handle, ACCOUNT_WAIT_S)
