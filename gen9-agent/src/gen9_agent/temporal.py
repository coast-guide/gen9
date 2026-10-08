"""Gen9's side of Temporal: the connection and the search attributes. Task queues, priorities and
activity names are in workflows/names.py, which workflow code can import. Where Gen9 uses
Temporal, and the rules it follows: docs/temporal.md.
"""

import asyncio
import logging
import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from temporalio import activity
from temporalio.client import Client
from temporalio.common import SearchAttributeKey
from temporalio.worker.workflow_sandbox import (
    SandboxedWorkflowRunner,
    SandboxRestrictions,
)

from .codec import data_converter, parse_keys
from .keycloak_admin import KeycloakAdmin
from .settings import Settings

log = logging.getLogger(__name__)

# How workers and replay tests run workflow code. fastmcp's dependency py-key-value calls
# beartype's `beartype_this_package()` on import, which adds an import hook to sys.meta_path; the
# workflow sandbox, re-importing modules in isolation, then trips over that hook ("Failed
# validating workflow"). beartype is passed through, as Temporal's sandbox docs advise for such
# modules (docs/plans/harness.md, Surprises).
WORKFLOW_RUNNER = SandboxedWorkflowRunner(
    restrictions=SandboxRestrictions.default.with_passthrough_modules("beartype")
)

# Custom search attributes of namespace `gen9` (gen9-temporal/scripts/setup-namespace.sh)
GEN9_USER = SearchAttributeKey.for_keyword("Gen9User")
GEN9_THREAD = SearchAttributeKey.for_keyword("Gen9Thread")
GEN9_KIND = SearchAttributeKey.for_keyword("Gen9Kind")
GEN9_RUN_STATE = SearchAttributeKey.for_keyword("Gen9RunState")


async def connect(
    settings: Settings,
    role: str,
    keycloak: KeycloakAdmin | None,
    lazy: bool = False,
) -> Client:
    """`role` names this process in Temporal (pollers, who started or cancelled a workflow):
    `<role>@<host>:<pid>`. `keycloak` provides gen9-agent's service-account token, which Temporal
    requires (gen9:write); keep it fresh with `keep_token_fresh`. `lazy`: connect on first use (the
    API, which serves chats while Temporal or Keycloak is down and only needs Temporal to start and
    stop runs); workers connect at once and exit if they can't."""
    token = None
    if keycloak is not None:
        try:
            token = await keycloak.access_token()
        except Exception:
            if not lazy:
                raise
            log.warning("no Keycloak token for Temporal yet; keep_token_fresh retries")
    return await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        identity=f"{role}@{socket.gethostname()}:{os.getpid()}",
        # Payloads are encrypted before they leave the process (codec.py)
        data_converter=data_converter(
            parse_keys(settings.temporal_payload_keys.get_secret_value())
        ),
        api_key=token,
        # The SDK turns TLS on when an API key is set; gen9-temporal's frontend speaks plain gRPC
        # for now (docs/temporal.md, "Security")
        tls=False,
        lazy=lazy,
    )


# What a renewed token has left at least: half the 5 minutes Keycloak's live. Rounds run on the
# event loop, so a stall of it (a step that holds the CPU) skips them; with 30 s left, a model that
# ran away inside a tool call stalled the worker past its token's end, and Temporal stopped it
# (docs/plans/deploy.md, Z1). Now a stall of up to about 2.5 minutes passes
TOKEN_MARGIN_S = 150


async def keep_token_fresh(
    client: Client, keycloak: KeycloakAdmin, every_s: float = 15
) -> None:
    """Background task: replace the client's token before it expires (Keycloak's live 5 minutes).
    Each round asks for a token valid for at least half its life more (`TOKEN_MARGIN_S`), so calls
    carry a valid one even after the event loop stalled for up to 2.5 minutes; a round only reads
    the cached token until then, so it can be frequent: a host waking from sleep gets a new token
    within `every_s` (its expiry is wall time, keycloak_admin.py). Running calls and pollers pick
    up the new one."""
    while True:
        await asyncio.sleep(every_s)
        try:
            client.api_key = await keycloak.access_token(
                min_valid_s=max(2 * every_s, TOKEN_MARGIN_S)
            )
        except Exception:
            log.warning("could not renew the Temporal token; retrying", exc_info=True)


@asynccontextmanager
async def heartbeating(every_s: float = 10) -> AsyncIterator[None]:
    """Heartbeat from a side task while the body runs, so a long Activity (erasing thousands of
    traces, waiting for a run to stop) stays within its heartbeat timeout and receives cancels."""

    async def beat() -> None:
        while True:
            activity.heartbeat()
            await asyncio.sleep(every_s)

    task = asyncio.create_task(beat())
    try:
        yield
    finally:
        task.cancel()
