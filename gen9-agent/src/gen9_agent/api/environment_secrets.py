"""A person's environment secrets: values OpenSandbox's credential vault adds to requests from their
chats' environments to a host, so code there never sees them (environments.py).

The value is sealed (vault.py), bound to its owner and its row, and never returned. Adding or
removing one starts `RefreshEnvironmentsWorkflow`, which rewrites the person's running
environments within seconds; new ones get the current set. Only their owner sees or removes
them; others get 404.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, ValidationInfo, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from temporalio.common import (
    SearchAttributePair,
    TypedSearchAttributes,
    WorkflowIDConflictPolicy,
)

from .. import audit
from ..deps import Session
from ..models import EnvironmentSecret
from ..temporal import GEN9_KIND, GEN9_USER
from ..users import CurrentUser
from ..workflows.environment import RefreshEnvironmentsWorkflow
from ..workflows.names import SYSTEM_QUEUE, refresh_environments_workflow_id

router = APIRouter(prefix="/v1/me/environment-secrets", tags=["environments"])

NAME = r"^[a-z0-9]+(-[a-z0-9]+)*$"
# A host name, or all subdomains of one ("*.example.com"): no scheme, port or path
HOST = r"^(\*\.)?[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
PATH = r"^/[A-Za-z0-9._~%/*-]*$"
HEADER = r"^[A-Za-z0-9-]+$"


class SecretIn(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=32, pattern=NAME)]
    host: Annotated[str, Field(min_length=3, max_length=253, pattern=HOST)]
    path: Annotated[str, Field(min_length=1, max_length=256, pattern=PATH)] = "/*"
    # Authorization: Bearer <value>; <header>: <value>; or Basic, the value being user:password
    auth: Literal["bearer", "header", "basic"] = "bearer"
    header: Annotated[
        str | None, Field(max_length=64, pattern=HEADER, validate_default=True)
    ] = None
    value: Annotated[str, Field(min_length=1, max_length=8000)]
    # Which requests it goes with: "read" (GET, HEAD, OPTIONS), or "all", changes too
    # (environments.METHODS)
    methods: Literal["read", "all"] = "read"

    # Each refusal names its own field (`loc`), so Settings shows it there (P2-K2); fields
    # validate in order, so a later one can read `auth`

    @field_validator("host")
    @classmethod
    def _a_name(cls, host: str) -> str:
        # OpenSandbox's vault binds names only ("must be an FQDN, not an IP address"): one address
        # among a person's secrets fails every environment's vault, and the worker removes them.
        # No top-level domain is all digits (RFC 3696, section 2), so that is an address
        if host.rsplit(".", 1)[-1].isdigit():
            raise ValueError("a host name, not an IP address")
        return host

    @field_validator("header")
    @classmethod
    def _header_goes_with_auth(
        cls, header: str | None, info: ValidationInfo
    ) -> str | None:
        auth = info.data.get("auth")
        if auth == "header" and not header:
            raise ValueError("name the header it goes in")
        if auth != "header" and header:
            raise ValueError("a header name goes only with auth 'header'")
        return header

    @field_validator("value")
    @classmethod
    def _basic_is_user_password(cls, value: str, info: ValidationInfo) -> str:
        if info.data.get("auth") == "basic" and ":" not in value:
            raise ValueError("Basic takes user:password")
        return value


class SecretOut(BaseModel):
    id: uuid.UUID
    name: str
    host: str
    path: str
    auth: str
    header: str | None
    methods: str
    created_at: datetime


def _out(row: EnvironmentSecret) -> SecretOut:
    return SecretOut(
        id=row.id,
        name=row.name,
        host=row.host,
        path=row.path,
        auth=row.auth,
        header=row.header,
        methods=row.methods,
        created_at=row.created_at,
    )


async def _refresh(request: Request, user_sub: str) -> None:
    """The person's running environments, rewritten to their current secrets (a newer change
    replaces a refresh still running)."""
    if not request.app.state.runtime.settings.sandbox_url:
        return
    await request.app.state.temporal.start_workflow(
        RefreshEnvironmentsWorkflow.run,
        user_sub,
        id=refresh_environments_workflow_id(user_sub),
        task_queue=SYSTEM_QUEUE,
        id_conflict_policy=WorkflowIDConflictPolicy.TERMINATE_EXISTING,
        search_attributes=TypedSearchAttributes(
            [
                SearchAttributePair(GEN9_KIND, "environment"),
                SearchAttributePair(GEN9_USER, user_sub),
            ]
        ),
    )


@router.get("", summary="The caller's environment secrets, without their values")
async def list_secrets(user: CurrentUser, session: Session) -> list[SecretOut]:
    rows = await session.scalars(
        select(EnvironmentSecret)
        .where(EnvironmentSecret.user_id == user.id)
        .order_by(EnvironmentSecret.name)
    )
    return [_out(r) for r in rows]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Add a secret the caller's environments send to a host; running ones get it too",
    responses={409: {"description": "A secret with that name exists"}},
)
async def add_secret(
    body: SecretIn, user: CurrentUser, session: Session, request: Request
) -> SecretOut:
    vault = request.app.state.runtime.vault
    if vault is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Secrets can't be kept: this Gen9 has no GEN9_SECRET_KEYS.",
        )
    cap = request.app.state.runtime.settings.secrets_max_per_person
    kept = await session.scalar(
        select(func.count()).where(EnvironmentSecret.user_id == user.id)
    )
    if (kept or 0) >= cap:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"At most {cap} secrets each. Remove one first.",
        )
    secret_id = uuid.uuid4()
    row = EnvironmentSecret(
        id=secret_id,
        user_id=user.id,
        name=body.name,
        host=body.host,
        path=body.path,
        auth=body.auth,
        header=body.header,
        methods=body.methods,
        # Bound to its owner and this row: copied onto another, it doesn't open
        sealed_value=vault.seal(body.value, user.sub, str(secret_id), "environment"),
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "You already have a secret with that name."
        ) from None
    await session.refresh(row)
    await _refresh(request, user.sub)
    # Its name and host only, never its value
    await audit.record(
        request,
        user.sub,
        "environment_secret.add",
        target=row.id,
        detail={"name": row.name, "host": row.host, "methods": row.methods},
    )
    return _out(row)


@router.delete(
    "/{secret_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove it: running environments stop sending it within seconds",
)
async def remove_secret(
    secret_id: uuid.UUID, user: CurrentUser, session: Session, request: Request
) -> Response:
    row = await session.scalar(
        select(EnvironmentSecret).where(
            EnvironmentSecret.id == secret_id, EnvironmentSecret.user_id == user.id
        )
    )
    if row is None:
        await audit.theirs(
            request, session, EnvironmentSecret, secret_id, user, "environment_secret"
        )
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such secret")
    await session.delete(row)
    await session.commit()
    await _refresh(request, user.sub)
    await audit.record(
        request,
        user.sub,
        "environment_secret.remove",
        target=secret_id,
        detail={"name": row.name, "host": row.host},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
