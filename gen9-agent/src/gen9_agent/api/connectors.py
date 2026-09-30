"""A person's connectors: remote MCP servers whose tools join their chats (connectors.py).

Adding one connects to it first, and refuses it with a reason the person can act on (not https,
private network, can't reach it, token refused, not an MCP server). Its token is sealed (vault.py)
and never returned. Only its owner sees or changes it; others get 404.

A server that needs sign-in (MCP's authorization, connector_auth.py) is kept as waiting for it,
with the address to send the person to. Their browser comes back to the web app, which hands the
code, `state` and `iss` to `POST …/sign-in/callback`. A sign-in under way is used once and lasts
ten minutes.

A connector tool's View (MCP Apps, apps.py) runs in the person's browser; the web app hands
its resource reads and tool calls to `POST …/{id}/app/resource` and `POST …/{id}/app/call`.
Only the tools the server lets Views call, under the connector's policy: a call it would ask
about answers 409 until the web app sends it again with `allowed`, once the person has.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import AfterValidator, AnyUrl, BaseModel, Field, ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from .. import audit, connector_auth, connector_setup, plugin_connectors
from ..connector_auth import Registration, SignIn
from ..connector_net import http_client, reach
from ..connectors import NAME, AskFirst, ConnectorError, describe, discover
from ..deps import Session
from ..models import Connector, ConnectorSignIn, Plugin
from ..plugin_skills import has_plugin
from ..users import CurrentUser

router = APIRouter(prefix="/v1/me/connectors", tags=["connectors"])

Policy = Literal["ask", "changes", "never"]
# How long a sign-in under way stays usable
SIGN_IN_TTL = timedelta(minutes=10)
OAUTH_TIMEOUT_S = 15


class ConnectorIn(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=32, pattern=NAME.pattern)]
    url: Annotated[str, Field(min_length=8, max_length=2000)]
    # A token sent with each call ("Authorization: Bearer <token>" unless `header` says otherwise)
    token: Annotated[str | None, Field(max_length=8000)] = None
    header: Annotated[str | None, Field(max_length=64, pattern=r"^[A-Za-z0-9-]+$")] = (
        None
    )
    policy: Policy = "ask"


class ConnectorPatch(BaseModel):
    policy: Policy


class ConnectorOut(BaseModel):
    id: uuid.UUID
    name: str
    url: str
    has_token: bool
    policy: str
    tools: list[dict[str, Any]]
    # ready; sign_in: waits for the person to sign in; reconnect: their sign-in lapsed
    status: str
    created_at: datetime
    # Where to send the person to sign in, when a sign-in was just started
    authorize_url: str | None = None
    # The plugin that brought it (plugin_connectors.py): it goes with the plugin
    plugin: str | None = None
    # Tools the server added or changed since the person kept them: held back until they look
    # (name, description, was, pin; connectors.py)
    changed: list[dict[str, Any]] = []


class KeepToolsIn(BaseModel):
    # The `pin` of each new or changed tool the person looked at, as `changed` gave it
    pins: Annotated[
        list[Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]],
        Field(min_length=1, max_length=200),
    ]


def _resource_uri(value: str) -> str:
    """A resource's address as MCP's client will parse it (AnyUrl, in FastMCP's read_resource): one
    it can't parse failed inside that call, a 500 (Schemathesis, manual-e2e.md, P6-B1). Kept as
    written: a View names its resource exactly."""
    try:
        AnyUrl(value)
    except ValidationError:
        raise ValueError("That isn't a resource's address (a URI).") from None
    return value


class AppResourceIn(BaseModel):
    uri: Annotated[
        str, Field(min_length=1, max_length=2000), AfterValidator(_resource_uri)
    ]


class AppCallIn(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    arguments: dict[str, Any] = {}
    # The person allowed this call in the web app, the connector's policy asking first
    allowed: bool = False


class SignInCallback(BaseModel):
    code: Annotated[str, Field(min_length=1, max_length=4000)]
    state: Annotated[str, Field(min_length=16, max_length=512)]
    # The authorization server's `iss` (RFC 9207), when it sent one
    iss: Annotated[str | None, Field(max_length=2000)] = None


def _out(
    row: Connector, authorize_url: str | None = None, plugin: str | None = None
) -> ConnectorOut:
    return ConnectorOut(
        id=row.id,
        name=row.name,
        url=row.url,
        has_token=row.sealed_token is not None or row.sealed_tokens is not None,
        policy=row.policy,
        tools=row.tools,
        status=row.status,
        created_at=row.created_at,
        authorize_url=authorize_url,
        plugin=plugin,
        changed=row.changed or [],
    )


def _state_hash(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def _redirect_uri(request: Request) -> str:
    return connector_setup.redirect_uri(request.app.state.runtime)


def _sign_in_of(row: Connector) -> tuple[SignIn, Registration]:
    """What was kept of the server's sign-in, and Gen9's client there."""
    return connector_auth.kept(row.sign_in or {})


async def _start_sign_in(
    row: Connector, user_sub: str, session: Session, request: Request
) -> str:
    """A new sign-in under way for `row`: its row of connector_sign_ins, and the address."""
    runtime = request.app.state.runtime
    sign_in, registration = _sign_in_of(row)
    redirect_uri = _redirect_uri(request)
    started = connector_auth.authorization(
        sign_in, registration.client_id, redirect_uri
    )
    secret = json.dumps(
        {"verifier": started.code_verifier, "redirect_uri": redirect_uri}
    )
    session.add(
        ConnectorSignIn(
            state_hash=_state_hash(started.state),
            connector_id=row.id,
            # Bound to its owner and connector: copied elsewhere, it doesn't open
            sealed=runtime.vault.seal(secret, user_sub, str(row.id), "sign-in"),
        )
    )
    return started.url


async def _owned(
    connector_id: uuid.UUID, user: CurrentUser, session: Session, request: Request
) -> Connector:
    row = await session.scalar(
        select(Connector).where(
            Connector.id == connector_id, Connector.user_id == user.id
        )
    )
    if row is None:
        await audit.theirs(request, session, Connector, connector_id, user, "connector")
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such connector")
    return row


@router.get(
    "", summary="The caller's connectors, with the tools each offered when last listed"
)
async def list_connectors(
    user: CurrentUser, session: Session, request: Request
) -> list[ConnectorOut]:
    """Its plugins' connectors are brought in line first (plugin_connectors.py)."""
    await plugin_connectors.reconcile(
        request.app.state.runtime, session, user.id, user.sub
    )
    rows = await session.execute(
        select(Connector, func.coalesce(Plugin.title, Plugin.name))
        .outerjoin(Plugin, Plugin.id == Connector.plugin_id)
        .where(
            Connector.user_id == user.id,
            or_(Connector.plugin_id.is_(None), has_plugin(user.sub)),
        )
        .order_by(Connector.name)
    )
    return [_out(r, plugin=plugin) for r, plugin in rows.tuples()]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Connect a remote MCP server: Gen9 lists its tools first",
    responses={
        409: {"description": "A connector with that name exists"},
        422: {"description": "Gen9 can't use it, and says why"},
    },
)
async def add_connector(
    body: ConnectorIn, user: CurrentUser, session: Session, request: Request
) -> ConnectorOut:
    runtime = request.app.state.runtime
    cap = runtime.settings.connectors_max_per_person
    kept = await session.scalar(
        select(func.count()).where(Connector.user_id == user.id)
    )
    if (kept or 0) >= cap:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"At most {cap} connectors each. Remove one first.",
        )
    try:
        row = await connector_setup.make(
            runtime,
            user.id,
            user.sub,
            body.name,
            body.url,
            header=body.header,
            token=body.token,
            policy=body.policy,
        )
    except connector_setup.CantKeep as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e)) from None
    except ConnectorError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    sign_in = row.status == "sign_in"
    session.add(row)
    try:
        await session.flush()
    except IntegrityError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "You already have a connector with that name."
        ) from None
    authorize_url = (
        await _start_sign_in(row, user.sub, session, request) if sign_in else None
    )
    await session.commit()
    await session.refresh(row)
    await audit.record(
        request,
        user.sub,
        "connector.add",
        target=row.id,
        detail={"name": row.name, "host": urlsplit(row.url).hostname},
    )
    return _out(row, authorize_url)


@router.post(
    "/{connector_id}/sign-in",
    summary="Start signing in to it again: the address to send the person to",
)
async def sign_in_again(
    connector_id: uuid.UUID, user: CurrentUser, session: Session, request: Request
) -> ConnectorOut:
    row = await _owned(connector_id, user, session, request)
    if not row.sign_in:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "That connector doesn't use a sign-in."
        )
    authorize_url = await _start_sign_in(row, user.sub, session, request)
    await session.commit()
    return _out(row, authorize_url)


@router.post(
    "/sign-in/callback",
    summary="Finish a sign-in: the code, state and iss the person's browser brought back",
    responses={
        400: {"description": "Unknown, used or expired state, or the wrong server"}
    },
)
async def finish_sign_in(
    body: SignInCallback, user: CurrentUser, session: Session, request: Request
) -> ConnectorOut:
    runtime = request.app.state.runtime
    under_way = await session.scalar(
        select(ConnectorSignIn)
        .join(Connector, Connector.id == ConnectorSignIn.connector_id)
        .where(
            ConnectorSignIn.state_hash == _state_hash(body.state),
            Connector.user_id == user.id,
        )
    )
    refused = HTTPException(
        status.HTTP_400_BAD_REQUEST, "That sign-in isn't under way. Try again."
    )
    if under_way is None or runtime.vault is None:
        raise refused
    started, sealed = under_way.created_at, under_way.sealed
    row = await _owned(under_way.connector_id, user, session, request)
    # Used once, whatever happens next
    await session.delete(under_way)
    await session.commit()
    if datetime.now(UTC) - started > SIGN_IN_TTL:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "That sign-in took too long. Try again."
        )
    kept = json.loads(runtime.vault.open(sealed, user.sub, str(row.id), "sign-in"))
    sign_in, registration = _sign_in_of(row)
    if row.sealed_client_secret:
        registration = Registration(
            registration.client_id,
            runtime.vault.open(
                row.sealed_client_secret, user.sub, str(row.id), "client"
            ),
            registration.how,
        )
    settings = runtime.settings
    try:
        connector_auth.check_issuer(
            body.iss,
            sign_in.issuer,
            bool(sign_in.metadata.authorization_response_iss_parameter_supported),
        )
        async with http_client(reach(settings), timeout=OAUTH_TIMEOUT_S) as http:
            token = await connector_auth.exchange(
                http,
                sign_in,
                registration,
                body.code,
                kept["verifier"],
                kept["redirect_uri"],
                reach(settings),
            )
        tools = await discover(row.url, None, token.access_token, reach(settings))
    except ConnectorError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None
    row.sealed_tokens = runtime.vault.seal(
        connector_auth.tokens_json(token), user.sub, str(row.id), "tokens"
    )
    # Its first sign-in lists what the person agrees to; a later one (its sign-in lapsed) keeps
    # that, so a server can't get changed tools kept by making them sign in again (P5-C4)
    if not row.tools:
        row.tools = [describe(t) for t in tools]
    row.status = "ready"
    row.updated_at = func.now()
    await session.commit()
    await session.refresh(row)
    return _out(row)


@router.post(
    "/{connector_id}/app/resource",
    summary="Read a resource for one of its Views (MCP Apps): a ui:// View with its CSP",
    responses={422: {"description": "The server refused, or it isn't an MCP App"}},
)
async def read_app_resource(
    connector_id: uuid.UUID,
    body: AppResourceIn,
    user: CurrentUser,
    session: Session,
    request: Request,
) -> dict[str, Any]:
    row = await _owned(connector_id, user, session, request)
    await session.commit()  # no transaction held while the server answers
    try:
        return await request.app.state.runtime.connectors.app_resource(
            row, user.sub, body.uri
        )
    except ConnectorError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None


@router.post(
    "/{connector_id}/app/call",
    summary="Call a tool for one of its Views (MCP Apps), under the connector's policy",
    responses={
        409: {"description": "The connector's policy asks the person first"},
        422: {"description": "Not a tool its Views may call, or the server refused"},
    },
)
async def call_app_tool(
    connector_id: uuid.UUID,
    body: AppCallIn,
    user: CurrentUser,
    session: Session,
    request: Request,
) -> dict[str, Any]:
    row = await _owned(connector_id, user, session, request)
    await session.commit()
    try:
        return await request.app.state.runtime.connectors.app_call(
            row, user.sub, body.name, body.arguments, body.allowed
        )
    except AskFirst as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    except ConnectorError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None


@router.post(
    "/{connector_id}/tools",
    summary="Keep the new or changed tools the person looked at: the agent may use them again",
    responses={422: {"description": "The server couldn't be reached"}},
)
async def keep_tools(
    connector_id: uuid.UUID,
    body: KeepToolsIn,
    user: CurrentUser,
    session: Session,
    request: Request,
) -> ConnectorOut:
    row = await _owned(connector_id, user, session, request)
    await session.commit()
    try:
        tools, still = await request.app.state.runtime.connectors.keep(
            row, user.sub, set(body.pins)
        )
    except (ConnectorError, TimeoutError) as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            str(e) or "The server didn't answer in time.",
        ) from None
    row.tools = tools
    row.changed = still or None
    # A new mark of what the person agreed to: listings kept for the old one go
    row.updated_at = func.now()
    await session.commit()
    await session.refresh(row)
    await audit.record(
        request,
        user.sub,
        "connector.tools.keep",
        target=row.id,
        detail={
            "name": row.name,
            "tools": sorted(t["name"] for t in tools if t["pin"] in body.pins),
        },
    )
    return _out(row)


@router.patch("/{connector_id}", summary="Change when Gen9 asks before using it")
async def change_connector(
    connector_id: uuid.UUID,
    body: ConnectorPatch,
    user: CurrentUser,
    session: Session,
    request: Request,
) -> ConnectorOut:
    row = await _owned(connector_id, user, session, request)
    row.policy = body.policy
    row.updated_at = func.now()
    await session.commit()
    await session.refresh(row)
    return _out(row)


@router.delete(
    "/{connector_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disconnect it: its tools leave the next model call, its token is deleted "
    "(and revoked at its server, where it offers that)",
)
async def remove_connector(
    connector_id: uuid.UUID, user: CurrentUser, session: Session, request: Request
) -> Response:
    row = await _owned(connector_id, user, session, request)
    if row.plugin_id is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "It comes with a plugin: remove the plugin in Settings > Plugins.",
        )
    await session.commit()  # no transaction held while its server answers
    # RFC 7009, best effort: removed whether or not its server revoked them
    await request.app.state.runtime.connectors.revoke(row, user.sub)
    await session.delete(row)
    await session.commit()
    await audit.record(
        request,
        user.sub,
        "connector.remove",
        target=connector_id,
        detail={"name": row.name},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
