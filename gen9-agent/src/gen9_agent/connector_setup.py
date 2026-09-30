"""Making a connector (connectors.py): Gen9 connects to the server first, and keeps it ready with
its tools, or waiting for the person to sign in (connector_auth.py). Used when a person adds a
connector (api/connectors.py) and when a plugin they have brings one (plugin_connectors.py)."""

import uuid

from . import connector_auth
from .connector_net import http_client, reach
from .connectors import ConnectorError, describe, discover
from .models import Connector
from .runtime import Runtime

OAUTH_TIMEOUT_S = 15


class CantKeep(Exception):
    """The connector needs something this Gen9 can't keep (no GEN9_SECRET_KEYS)."""


def redirect_uri(runtime: Runtime) -> str:
    """Where a connector's sign-in returns: the web app's callback."""
    return f"{runtime.settings.gen9_ui_url.rstrip('/')}/settings/connectors/callback"


async def make(
    runtime: Runtime,
    user_id: uuid.UUID,
    user_sub: str,
    name: str,
    url: str,
    *,
    header: str | None = None,
    token: str | None = None,
    policy: str = "ask",
) -> Connector:
    """A new connector row (not added to a session): ready with the tools it lists, or waiting
    for sign-in when its server asks for one (with Gen9 registered there). Raises
    ConnectorError with a reason the person can act on, or CantKeep."""
    settings = runtime.settings
    if token and runtime.vault is None:
        raise CantKeep("Tokens can't be kept: this Gen9 has no GEN9_SECRET_KEYS.")
    connector_id = uuid.uuid4()
    sign_in = registration = None
    if not token:
        async with http_client(reach(settings), timeout=OAUTH_TIMEOUT_S) as http:
            sign_in = await connector_auth.sign_in_needed(http, url, reach(settings))
            if sign_in is not None:
                if runtime.vault is None:
                    raise CantKeep(
                        "Sign-ins can't be kept: this Gen9 has no GEN9_SECRET_KEYS."
                    )
                registration = await connector_auth.register(
                    http,
                    sign_in,
                    redirect_uri(runtime),
                    reach(settings),
                    preregistered={
                        issuer: (c["client_id"], c.get("client_secret"))
                        for issuer, c in settings.connectors_oauth_clients.items()
                    },
                    metadata_url=f"{settings.gen9_ui_url.rstrip('/')}/oauth/client.json",
                )
    tools = [] if sign_in else await discover(url, header, token, reach(settings))
    row = Connector(
        id=connector_id,
        user_id=user_id,
        name=name,
        url=url.strip(),
        header=header,
        # Bound to its owner and this connector: copied onto another row, it doesn't open
        sealed_token=runtime.vault.seal(token, user_sub, str(connector_id))
        if token and runtime.vault
        else None,
        policy=policy,
        tools=[describe(t) for t in tools],
        status="sign_in" if sign_in else "ready",
    )
    if sign_in and registration and runtime.vault:
        row.sign_in = {
            "resource": sign_in.resource,
            "issuer": sign_in.issuer,
            "metadata": sign_in.metadata.model_dump(mode="json", exclude_none=True),
            "scope": sign_in.scope,
            "client_id": registration.client_id,
            "how": registration.how,
        }
        if registration.client_secret:
            row.sealed_client_secret = runtime.vault.seal(
                registration.client_secret, user_sub, str(connector_id), "client"
            )
    return row


__all__ = ["CantKeep", "ConnectorError", "make", "redirect_uri"]
