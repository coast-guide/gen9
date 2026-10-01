"""Connectors: remote MCP servers a person connects, whose tools join their chats (milestone 2,
docs/plans/harness.md).

- **Per person, per run.** The agent is compiled once for everyone, so a connector's tools are not
  compiled in: `ConnectorTools` adds the run's person's tools to each model call and runs them when
  called (LangChain's "runtime tool registration", explore/connectors/NOTES.md). A tool is named
  `<connector>__<tool>`, and a name is only ever looked up among that person's tools.
- **Asking first.** `ConnectorApprovals` makes a call wait for Allow or Deny by the connector's
  policy (`ask`: always, the default; `changes`: unless the server marks the tool read-only;
  `never`) and the chat's mode ("Ask before acting" asks for anything not read-only). MCP asks for
  a human able to deny any call, and treats a tool's own annotations as untrusted unless you trust
  the server.
- **Only public servers.** A connector's URL must be https and resolve to public addresses, unless
  the operator allows private networks (`CONNECTORS_ALLOW_PRIVATE`): the API and workers sit next
  to Gen9's own services (Decision Log, SSRF).
- **Tokens** are sealed (vault.py), bound to their owner and connector, and sent as a header.
  When a signed-in connector goes (removed, or its account deleted), its tokens are revoked at
  its server where the server offers it (RFC 7009), best effort: they're deleted either way.
- **Pinned tools.** What the person saw when they connected it is what the agent gets: each
  tool's name, description, input schema, annotations and app are hashed then (`pin`). A tool the
  server adds or changes later (a "rug pull") is held back from the model and its View until the
  person looks at it in Settings and keeps it (`changes`; OWASP's MCP Security Cheat Sheet:
  "Re-prompt for consent when tool definitions change"; docs/plans/manual-e2e.md, P5-C4).
- **Apps.** Clients advertise MCP Apps (apps.py): a View's own tools stay away from the model, a
  UI tool's result is tagged with its View, and the web app's View reads resources and calls
  tools through `app_resource` and `app_call`, under the connector's policy.
"""

import asyncio
import hashlib
import json
import logging
import re
import ssl
import time
import uuid
import warnings
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from langchain.agents.middleware import (
    AgentMiddleware,
    HumanInTheLoopMiddleware,
    InterruptOnConfig,
    ToolCallRequest,
)
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import Command
from mcp.client.extension import advertise
from mcp.shared.exceptions import MCPError
from pydantic import BaseModel
from sqlalchemy import Row, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from . import apps, connector_auth
from .connector_net import (
    ConnectorError,
    Reach,
    check_url,
    http_client,
    mcp_http_client,
)
from .models import Connector, Plugin, User
from .plugin_skills import has_plugin
from .vault import Vault

with (
    warnings.catch_warnings()
):  # langchain.mcp is marked beta inside the stable langchain 1.4
    warnings.simplefilter("ignore")
    from langchain.mcp import MCPAdapter

log = logging.getLogger(__name__)

# A connector's name: shown to the person and the prefix of its tools' names
NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SEP = "__"
POLICIES = ("ask", "changes", "never")
# Model providers take tool names up to 64 characters (OpenAI: ^[a-zA-Z0-9_-]{1,64}$)
MAX_TOOL_NAME = 64
DISCOVER_TIMEOUT_S = 15
# How long a connector's tools are reused before they are listed again
TOOLS_FRESH_S = 60
# Revoking a connector's tokens as it goes: per request, best effort
REVOKE_TIMEOUT_S = 10
# A View's calls and reads through Gen9, and how large a resource or a call's answer it may relay
APP_TIMEOUT_S = 60
MAX_APP_RESOURCE = 5_000_000
# Refresh a connector's access token when it expires within this, longer than TOOLS_FRESH_S so the
# tools listed with it never outlive it
REFRESH_MARGIN_S = 120


def client(
    url: str, header: str | None, token: str | None, reach: "Reach | bool"
) -> Client:
    """A client for the server, sending the token (if any) in its header on each call. A bare
    token in `Authorization` is sent as a Bearer token. It connects only to addresses `reach`
    allows, checked as each connection opens (connector_net.py)."""
    headers = None
    if token:
        name = header or "Authorization"
        bare = name.lower() == "authorization" and " " not in token
        headers = {name: f"Bearer {token}" if bare else token}
    return Client(
        StreamableHttpTransport(
            url, headers=headers, httpx_client_factory=mcp_http_client(reach)
        ),
        # Gen9 renders Views (apps.py): servers that check offer their UI tools
        extensions=[advertise(apps.EXTENSION_ID, {"mimeTypes": [apps.MIME_TYPE]})],
    )


def read_only(tool: BaseTool) -> bool:
    """Whether the server marks the tool read-only. `langchain.mcp` keeps annotations under
    pydantic's field names (read_only_hint), not MCP's (readOnlyHint): both are read."""
    annotations = (tool.metadata or {}).get("mcp", {}).get("tool", {}).get(
        "annotations"
    ) or {}
    return (
        annotations.get("read_only_hint") or annotations.get("readOnlyHint")
    ) is True


def pin(tool: BaseTool) -> str:
    """A hash of what the tool is, as the server lists it: its name, description, input schema,
    annotations and app. Any change gives another (OWASP's MCP Security Cheat Sheet: "SHA-256 over
    the canonical JSON of the tool name, description, and input schema"; annotations too, as
    `changes` trusts read-only marks, and its app, as `visibility` keeps a tool from the model)."""
    # An MCP tool's is its inputSchema, a dict; a pydantic model's, its JSON Schema
    schema = tool.args_schema
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        schema = schema.model_json_schema()
    annotations = (
        (tool.metadata or {}).get("mcp", {}).get("tool", {}).get("annotations")
    )
    what = {
        "name": tool.name,
        "description": tool.description or "",
        "input": schema or {},
        "annotations": annotations or {},
        "app": apps.app_of(tool),
    }
    canonical = json.dumps(what, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def describe(tool: BaseTool) -> dict[str, Any]:
    app = apps.app_of(tool)
    return {
        "name": tool.name,
        "description": (tool.description or "")[:500],
        "read_only": read_only(tool),
        **({"app": app} if app else {}),
        "pin": pin(tool),
    }


def changes(
    kept: list[dict[str, Any]], tools: list[BaseTool]
) -> tuple[list[BaseTool], list[dict[str, Any]]]:
    """`tools` as the server lists them now, against those the person kept (`Connector.tools`):
    the ones unchanged, and those new or changed since, each with its description now and before
    (`was`, None for a new one) and its `pin`, which wait for the person. A tool kept before pins
    existed counts as changed: it is looked at once."""
    before = {t["name"]: t for t in kept}
    same: list[BaseTool] = []
    changed: list[dict[str, Any]] = []
    for tool in tools:
        was = before.get(tool.name)
        if was is not None and was.get("pin") == pin(tool):
            same.append(tool)
        else:
            changed.append(
                {
                    "name": tool.name,
                    "description": (tool.description or "")[:500],
                    "was": None if was is None else was.get("description"),
                    "pin": pin(tool),
                }
            )
    return same, changed


class AskFirst(ConnectorError):
    """A View's call the connector's policy asks the person about, not allowed yet."""


def asks(policy: str, tool: BaseTool) -> bool:
    """Whether the connector's policy asks the person before this tool runs."""
    return policy == "ask" or (policy == "changes" and not read_only(tool))


TLS_FAILED = "That server's certificate isn't valid, so Gen9 didn't connect."


def tls_failed(url: str | None, e: BaseException) -> bool:
    """Whether the server's TLS failed (a certificate expired, for another name, or not trusted),
    logged with why when it did. It reaches the person as a plain sentence, and a backend TLS
    failure is a security event (ASVS 5.0 16.3.4; manual-e2e.md, P6-C4). FastMCP raises it as
    "Client failed to connect: [SSL: CERTIFICATE_VERIFY_FAILED] …", which "couldn't reach" missed
    (lower case) and "doesn't look like an MCP server" answered."""
    chain: list[BaseException] = [e]
    while (cause := chain[-1].__cause__ or chain[-1].__context__) and len(chain) < 6:
        chain.append(cause)
    for x in chain:
        if isinstance(x, ssl.SSLError) or "[SSL" in str(x):
            log.warning("connector %s: TLS failed: %s", url or "call", str(x)[:300])
            return True
    return False


async def discover(
    url: str, header: str | None, token: str | None, reach: "Reach | bool"
) -> list[BaseTool]:
    """The server's tools, or ConnectorError saying why not."""
    await check_url(url, reach)
    try:
        async with asyncio.timeout(DISCOVER_TIMEOUT_S):
            return await MCPAdapter(client(url, header, token, reach)).list_tools()
    except TimeoutError as e:
        raise ConnectorError("The server didn't answer in time.") from e
    except Exception as e:
        text = f"{type(e).__name__}: {e}"
        if tls_failed(url, e):
            raise ConnectorError(TLS_FAILED) from e
        if "401" in text or "403" in text or "nauthorized" in text:
            raise ConnectorError("The server refused the token.") from e
        if "Connect" in text or "resolve" in text:
            raise ConnectorError("Gen9 couldn't reach that server.") from e
        log.info("connector %s: not listed: %s", url, text[:300])
        raise ConnectorError("That doesn't look like an MCP server.") from e


def failed(e: Exception) -> ConnectorError:
    """What a connected server's failure tells the person: its own refusal (an MCP error, such as
    "Method not found"), a token it refused, or that it couldn't be reached. An app's read answered
    500 when the server refused it, and would have when the server was down (Schemathesis,
    manual-e2e.md, P6-B1; ASVS 5.0 16.5.2)."""
    if isinstance(e, TimeoutError):
        return ConnectorError("The server didn't answer in time.")
    if isinstance(e, MCPError):
        return ConnectorError(f"The server refused: {e.message[:200]}")
    if tls_failed(None, e):
        return ConnectorError(TLS_FAILED)
    # With its causes: FastMCP says "Client failed to connect" over httpx's ConnectError
    chain: list[BaseException] = [e]
    while (cause := chain[-1].__cause__) is not None and len(chain) < 5:
        chain.append(cause)
    text = "; ".join(f"{type(x).__name__}: {x}" for x in chain)
    low = text.lower()
    if "401" in text or "403" in text or "unauthorized" in low:
        return ConnectorError("The server refused the token.")
    if "connect" in low or "resolve" in low:
        return ConnectorError("Gen9 couldn't reach that server.")
    log.info("connector call failed: %s", text[:300])
    return ConnectorError("The server failed to answer.")


@dataclass(frozen=True)
class _Loaded:
    tool: BaseTool  # renamed <connector>__<tool>
    policy: str
    connector_id: uuid.UUID
    connector: str


@dataclass
class _Listed:
    at: float
    updated_at: datetime
    tools: list[BaseTool]


class ConnectorTools(AgentMiddleware):
    """Adds the run's person's connector tools to each model call and runs them when called."""

    def __init__(
        self, engine: AsyncEngine, vault: Vault | None, allow_private: "Reach | bool"
    ) -> None:
        super().__init__()
        self.engine = engine
        self.vault = vault
        self.allow_private = allow_private
        self._listed: dict[uuid.UUID, _Listed] = {}
        # The last tools loaded for each person (by sub): what their approvals and calls see
        self._people: dict[str, dict[str, _Loaded]] = {}

    async def tools_of(self, sub: str) -> dict[str, _Loaded]:
        """This person's connector tools by their names here, listing any not fresh."""
        async with self.engine.connect() as conn:
            rows = list(
                await conn.execute(
                    select(Connector)
                    .join(User, User.id == Connector.user_id)
                    .outerjoin(Plugin, Plugin.id == Connector.plugin_id)
                    # A plugin's connector only while the person has the plugin
                    .where(
                        User.sub == sub,
                        or_(Connector.plugin_id.is_(None), has_plugin(sub)),
                    )
                    .order_by(Connector.name)
                )
            )
        loaded: dict[str, _Loaded] = {}
        for row in rows:
            # Waiting for the person to sign in (again): no tools until they do
            if row.status != "ready":
                continue
            listed = self._listed.get(row.id)
            if (
                listed is None
                or listed.updated_at != row.updated_at
                or time.monotonic() - listed.at > TOOLS_FRESH_S
            ):
                try:
                    if row.sealed_tokens:
                        token = await self._access_token(row.id, sub)
                        if token is None:
                            continue
                    else:
                        token = self._header_token(row, sub)
                    tools = await discover(
                        row.url, row.header, token, self.allow_private
                    )
                except (ConnectorError, ValueError) as e:
                    log.warning("connector %s of %s left out: %s", row.name, sub, e)
                    continue
                listed = _Listed(time.monotonic(), row.updated_at, tools)
                self._listed[row.id] = listed
            same, changed = changes(row.tools, listed.tools)
            if changed != (row.changed or []):
                await self._record_changes(row.id, changed)
            for tool in same:
                name = f"{row.name}{SEP}{tool.name}"
                if len(name) <= MAX_TOOL_NAME:
                    loaded[name] = _Loaded(
                        tool.model_copy(update={"name": name}),
                        row.policy,
                        row.id,
                        row.name,
                    )
        self._people[sub] = loaded
        return loaded

    async def _record_changes(
        self, connector_id: uuid.UUID, changed: list[dict[str, Any]]
    ) -> None:
        """What Settings shows the person to look at. `updated_at` stays: it marks what they
        agreed to, and a listing kept for it stays good."""
        log.warning(
            "connector %s: tools new or changed since they were kept, held back: %s",
            connector_id,
            ", ".join(c["name"] for c in changed) or "none now",
        )
        async with self.engine.begin() as conn:
            await conn.execute(
                update(Connector)
                .where(Connector.id == connector_id)
                .values(changed=changed or None)
            )

    def _header_token(self, row: Connector | Row[Any], sub: str) -> str | None:
        return (
            self.vault.open(row.sealed_token, sub, str(row.id))
            if row.sealed_token and self.vault
            else None
        )

    async def revoke(self, row: Connector | Row[Any], sub: str) -> list[str]:
        """Revokes a signed-in connector's tokens at its authorization server (RFC 7009), as it
        goes: the kinds revoked. Best effort: nothing to revoke, no revocation offered, or a
        failure (logged) all return []; the sealed tokens are deleted with the connector."""
        if not (row.sealed_tokens and row.sign_in and self.vault):
            return []
        belongs = (sub, str(row.id))
        try:
            tokens = json.loads(self.vault.open(row.sealed_tokens, *belongs, "tokens"))
            sign_in, registration = connector_auth.kept(row.sign_in)
            if row.sealed_client_secret:
                registration = connector_auth.Registration(
                    registration.client_id,
                    self.vault.open(row.sealed_client_secret, *belongs, "client"),
                    registration.how,
                )
            async with http_client(
                self.allow_private, timeout=REVOKE_TIMEOUT_S
            ) as http:
                revoked = await connector_auth.revoke(
                    http, sign_in, registration, tokens, self.allow_private
                )
        except (ConnectorError, httpx.HTTPError, ValueError, KeyError) as e:
            log.warning(
                "connector %s of %s: tokens not revoked at its server: %s",
                row.name,
                sub,
                e,
            )
            return []
        if revoked:
            log.info("connector %s of %s: revoked its %s", row.name, sub, revoked)
        return revoked

    async def _client(self, row: Connector, sub: str) -> Client:
        """A client for the person's connector, with its token (refreshed when due)."""
        await check_url(row.url, self.allow_private)
        if row.status != "ready":
            raise ConnectorError("Sign in to this connector again.")
        if row.sealed_tokens:
            token = await self._access_token(row.id, sub)
            if token is None:
                raise ConnectorError("Sign in to this connector again.")
        else:
            token = self._header_token(row, sub)
        return client(row.url, row.header, token, self.allow_private)

    async def app_resource(self, row: Connector, sub: str, uri: str) -> dict[str, Any]:
        """A resource a View of this connector reads: a `ui://` View's HTML with its `_meta.ui`,
        or another of the server's resources, as MCP's `ReadResourceResult`."""
        mcp = await self._client(row, sub)
        try:
            async with asyncio.timeout(APP_TIMEOUT_S), mcp:
                read = await mcp.read_resource_mcp(uri)
                contents = [
                    c.model_dump(by_alias=True, exclude_none=True)
                    for c in read.contents
                ]
                if uri.startswith("ui://") and not any(
                    (c.get("_meta") or {}).get("ui") for c in contents
                ):
                    listed = next(
                        (r for r in await mcp.list_resources() if str(r.uri) == uri),
                        None,
                    )
                    ui = apps.view_meta(None, listed.meta if listed else None)
                    if ui:
                        for c in contents:
                            c["_meta"] = {**(c.get("_meta") or {}), "ui": ui}
        except Exception as e:
            raise failed(e) from e
        if len(json.dumps(contents)) > MAX_APP_RESOURCE:
            raise ConnectorError("That resource is too large to show.")
        if uri.startswith("ui://") and any(
            c.get("mimeType") != apps.MIME_TYPE for c in contents
        ):
            raise ConnectorError("That isn't an MCP App.")
        return {"contents": contents}

    async def app_call(
        self,
        row: Connector,
        sub: str,
        name: str,
        arguments: dict[str, Any],
        allowed: bool,
    ) -> dict[str, Any]:
        """A View's call to one of its server's tools, as MCP's `CallToolResult`. Only a tool
        the server lets Views call; AskFirst when the connector's policy asks the person and they
        haven't allowed it yet."""
        tools = {t.name: t for t in await self._tools_for_app(row, sub)}
        tool = tools.get(name)
        if tool is None and name in {c["name"] for c in row.changed or []}:
            raise ConnectorError(
                f"{name} changed since you connected {row.name}: look at it in Settings first."
            )
        if tool is None or not apps.for_app(tool):
            raise ConnectorError(f"{row.name} doesn't let its app use {name}.")
        if asks(row.policy, tool) and not allowed:
            raise AskFirst(f"Allow {row.name}'s app to use {name}?")
        log.info("the app of connector %s (%s) calls %s", row.name, row.id, name)
        mcp = await self._client(row, sub)
        try:
            async with asyncio.timeout(APP_TIMEOUT_S), mcp:
                result = await mcp.call_tool_mcp(name, arguments)
        except Exception as e:
            raise failed(e) from e
        answer = result.model_dump(by_alias=True, exclude_none=True)
        # Bounded as a resource is: the browser gets it all (gen9-learn.md, M9, F16)
        if len(json.dumps(answer)) > MAX_APP_RESOURCE:
            raise ConnectorError(f"{name}'s answer is too large to show.")
        return answer

    async def keep(
        self, row: Connector, sub: str, pins: set[str]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """The connector's tools as listed now, kept: those unchanged since, and those new or
        changed that the person looked at (`pins`, as Settings showed them). Any other change,
        one made after they looked included, still waits."""
        mcp = await self._client(row, sub)
        try:
            async with asyncio.timeout(DISCOVER_TIMEOUT_S):
                tools = await MCPAdapter(mcp).list_tools()
        except Exception as e:
            raise failed(e) from e
        same, changed = changes(row.tools, tools)
        kept = [describe(t) for t in tools if t in same or pin(t) in pins]
        still = [c for c in changed if c["pin"] not in pins]
        # A tool still waiting keeps what the person agreed to before: it's what it changed from
        waiting = {c["name"] for c in still}
        return [*kept, *(t for t in row.tools if t["name"] in waiting)], still

    async def _tools_for_app(self, row: Connector, sub: str) -> list[BaseTool]:
        listed = self._listed.get(row.id)
        if (
            listed
            and listed.updated_at == row.updated_at
            and (time.monotonic() - listed.at <= TOOLS_FRESH_S)
        ):
            return changes(row.tools, listed.tools)[0]
        mcp = await self._client(row, sub)
        async with asyncio.timeout(DISCOVER_TIMEOUT_S):
            tools = await MCPAdapter(mcp).list_tools()
        self._listed[row.id] = _Listed(time.monotonic(), row.updated_at, tools)
        # A View gets only what the person kept, as the model does
        return changes(row.tools, tools)[0]

    async def _access_token(self, connector_id: uuid.UUID, sub: str) -> str | None:
        """The connector's access token, refreshed first when it expires within
        REFRESH_MARGIN_S. The refresh holds the connector's row (SELECT … FOR UPDATE), so two
        workers never spend one refresh token: a server that rotates them may treat reuse as
        theft. A failed refresh marks it `reconnect` and returns None."""
        if self.vault is None:
            return None
        async with self.engine.begin() as conn:
            row = (
                await conn.execute(
                    select(Connector)
                    .where(Connector.id == connector_id)
                    .with_for_update()
                )
            ).one()
            belongs = (sub, str(row.id))
            tokens = json.loads(self.vault.open(row.sealed_tokens, *belongs, "tokens"))
            expires_at = tokens.get("expires_at")
            if expires_at is None or expires_at - time.time() > REFRESH_MARGIN_S:
                return tokens["access_token"]
            sign_in, registration = connector_auth.kept(row.sign_in or {})
            if row.sealed_client_secret:
                secret = self.vault.open(row.sealed_client_secret, *belongs, "client")
                registration = connector_auth.Registration(
                    registration.client_id, secret, registration.how
                )
            try:
                if not tokens.get("refresh_token"):
                    raise ConnectorError("No refresh token")
                async with http_client(
                    self.allow_private, timeout=DISCOVER_TIMEOUT_S
                ) as http:
                    token = await connector_auth.refresh(
                        http,
                        sign_in,
                        registration,
                        tokens["refresh_token"],
                        self.allow_private,
                    )
            except ConnectorError as e:
                log.warning(
                    "connector %s of %s needs a new sign-in: %s", row.name, sub, e
                )
                await conn.execute(
                    update(Connector)
                    .where(Connector.id == row.id)
                    .values(status="reconnect", updated_at=func.now())
                )
                return None
            sealed = self.vault.seal(
                connector_auth.tokens_json(token, tokens.get("refresh_token")),
                *belongs,
                "tokens",
            )
            await conn.execute(
                update(Connector)
                .where(Connector.id == row.id)
                .values(sealed_tokens=sealed, updated_at=func.now())
            )
            return token.access_token

    async def awrap_model_call(self, request, handler):
        sub = getattr(request.runtime.context, "user_sub", None)
        extra = await self.tools_of(sub) if sub else {}
        if not extra:
            return await handler(request)
        # A View's own tools never reach the model (MCP Apps, `visibility`)
        visible = [e.tool for e in extra.values() if apps.for_model(e.tool)]
        return await handler(request.override(tools=[*request.tools, *visible]))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        name = request.tool_call["name"]
        if SEP not in name:
            return await handler(request)
        sub = getattr(request.runtime.context, "user_sub", "")
        found = (self._people.get(sub) or await self.tools_of(sub)).get(name)
        if found is None or not apps.for_model(found.tool):
            return ToolMessage(
                content=f"{name} isn't available: its connector was removed or can't be reached.",
                tool_call_id=request.tool_call.get("id") or "",
                name=name,
                status="error",
            )
        result = await handler(request.override(tool=found.tool))
        app = apps.app_of(found.tool)
        if app and app["resource_uri"] and isinstance(result, ToolMessage):
            return apps.tagged(
                result,
                str(found.connector_id),
                found.connector,
                app["resource_uri"],
                request.tool_call.get("args"),
            )
        return result

    def needs_approval(self, request: ToolCallRequest) -> bool:
        """Whether a connector call waits for Allow or Deny (the connector's policy and the
        chat's mode). A tool this person doesn't have asks, to be safe."""
        context = request.runtime.context
        found = self._people.get(getattr(context, "user_sub", ""), {}).get(
            request.tool_call["name"]
        )
        if found is None:
            return True
        if getattr(context, "permission_mode", "auto") == "ask" and not read_only(
            found.tool
        ):
            return True
        return asks(found.policy, found.tool)


class _ConnectorPolicy(dict):
    """An `interrupt_on` answering for any connector tool's name, which is known only at run
    time. HumanInTheLoopMiddleware reads it with `.get(name)` and `[name]` (pinned by a test)."""

    def __init__(self, decide: Callable[[ToolCallRequest], bool]) -> None:
        super().__init__()
        self.config = InterruptOnConfig(
            allowed_decisions=["approve", "reject"], when=decide
        )

    def get(self, name, default=None):
        return self.config if SEP in name else default

    def __getitem__(self, name):
        if SEP in name:
            return self.config
        raise KeyError(name)


class ConnectorApprovals(HumanInTheLoopMiddleware):
    """Approvals for connector calls, next to the memory approvals Deep Agents builds from
    `interrupt_on` (approvals.py); a class of its own, since an agent takes each middleware once."""

    def __init__(self, tools: ConnectorTools) -> None:
        super().__init__(interrupt_on={})
        self.interrupt_on = _ConnectorPolicy(tools.needs_approval)
