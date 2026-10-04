"""FastAPI application: Keycloak-protected API in front of the Gen9 Deep Agent."""

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import psycopg
from fastapi import FastAPI, Request, Response, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm.exc import StaleDataError
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import a2a_server, audit, mcp_server
from .api import (
    admin,
    agui,
    apps_host,
    connectors,
    directory,
    environment_secrets,
    export,
    files,
    health,
    me,
    my_plugins,
    plugins,
    runs,
    search,
    tasks,
    threads,
)
from .api.health import VERSION, schema_head, schema_revisions
from .api.temporal_codec import codec_app
from .api_docs import mount_docs
from .auth import TokenVerifier
from .body_limit import BodyLimit
from .chat_files import MAX_FILE
from .codec import EncryptionCodec, parse_keys
from .db_unavailable import database_unavailable
from .headers import SecurityHeaders
from .keycloak_admin import KeycloakAdmin
from .no_nul import NoNul
from .runs.log import EventHub
from .runtime import open_runtime
from .settings import get_settings
from .stale import gone_meanwhile
from .standing import IdentityUnavailable
from .temporal import connect, keep_token_fresh


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    async with open_runtime(settings) as runtime, httpx.AsyncClient(timeout=10) as http:
        app.state.runtime = runtime
        # The Alembic revision this build expects, read once (readyz compares the database to it)
        app.state.schema_head = await schema_head()
        app.state.schema_revisions = await schema_revisions()
        app.state.engine = runtime.engine
        app.state.sessionmaker = runtime.sessionmaker
        app.state.checkpointer = runtime.checkpointer
        app.state.agent = runtime.agent
        app.state.event_hub = EventHub(settings.database_conninfo)
        app.state.event_hub.start()
        app.state.token_verifier = TokenVerifier.from_jwks_url(
            settings.jwks_url,
            issuer=settings.keycloak_issuer,
            audience=settings.keycloak_audience,
            allowed_clients=settings.keycloak_allowed_clients,
        )
        app.state.keycloak_admin = (
            KeycloakAdmin(settings, http)
            if settings.keycloak_admin_client_secret
            else None
        )
        # A task's trigger fires only while its person may still have work done (standing.py)
        app.state.runtime.standing.admin = app.state.keycloak_admin
        # Temporal's web UI decodes payloads here, for Gen9 admins signed in to it (api/temporal_codec.py)
        codec_app.state.verifier = TokenVerifier.from_jwks_url(
            settings.jwks_url,
            issuer=settings.keycloak_issuer,
            audience="temporal",
            allowed_clients=frozenset({"temporal-ui"}),
        )
        codec_app.state.codec = EncryptionCodec(
            parse_keys(settings.temporal_payload_keys.get_secret_value())
        )
        # Starts and stops runs (the workers execute them, worker.py) with gen9-agent's service
        # token. Lazy: chats still open when Temporal is down; starting a run then answers 503
        app.state.temporal = await connect(
            settings, "gen9-agent-api", app.state.keycloak_admin, lazy=True
        )
        renewing = (
            asyncio.create_task(
                keep_token_fresh(app.state.temporal, app.state.keycloak_admin)
            )
            if app.state.keycloak_admin
            else None
        )
        # Users deleted in Keycloak directly are swept by a Temporal Schedule the worker keeps
        try:
            # Gen9 as an MCP server (mcp_server.py): its session manager runs with the API
            async with mcp_app.lifespan(app):
                yield
        finally:
            if renewing:
                renewing.cancel()
            await app.state.event_hub.stop()


app = FastAPI(
    title="Gen9 Agent API",
    # Gen9's own version, as /v1/version gives it (one version everywhere: deploy.md, U7)
    version=VERSION,
    lifespan=lifespan,
    # /docs below, from files of gen9-agent's own; FastAPI's own pages load scripts from a CDN
    docs_url=None,
    redoc_url=None,
    # Browsers never call this API directly (gen9-ui is the BFF), so no CORS is configured, except
    # on the Temporal codec endpoint below, which Temporal's web UI calls from the browser
)
codec_app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().temporal_ui_url],
    allow_methods=["POST"],
    # The UI also sends the ID token as Authorization-Extras (ui src/lib/services/data-encoder.ts)
    allow_headers=[
        "Authorization",
        "Authorization-Extras",
        "Content-Type",
        "X-Namespace",
    ],
)
app.mount("/v1/temporal/codec", codec_app)

# The API's docs page, from files of gen9-agent's own (api_docs.py)
mount_docs(app)
# Text with U+0000 refused before any endpoint or query sees it (no_nul.py); inside BodyLimit below,
# which bounds a body before this reads it
app.add_middleware(NoNul)
# Bodies bounded before anything reads one (body_limit.py): a file up to its own limit, whose
# endpoint says so in its words; the codec's payload batches; everything else JSON of a few KiB
app.add_middleware(
    BodyLimit,
    default=1024 * 1024,
    larger=[
        (re.compile(r"^/v1/threads/[^/]+/files$"), MAX_FILE + 1024 * 1024),
        (re.compile(r"^/v1/temporal/codec(/|$)"), 8 * 1024 * 1024),
    ],
)
# Not cached, framed or sniffed, every response (headers.py), the refusals above included
app.add_middleware(
    SecurityHeaders,
    hsts=get_settings().gen9_api_public_url.startswith("https://"),
)


# A row deleted while its request was under way: 404, not a 500 (stale.py)
app.add_exception_handler(StaleDataError, gone_meanwhile)
# The database not taking a request (read-only, a full disk, gone): 503 in words (db_unavailable.py)
app.add_exception_handler(DBAPIError, database_unavailable)
# LangGraph's store and checkpointer use psycopg itself: its errors come unwrapped
app.add_exception_handler(psycopg.Error, database_unavailable)


@app.exception_handler(IdentityUnavailable)
async def identity_unavailable(request: Request, exc: IdentityUnavailable) -> Response:
    """Keycloak down when a request asked whether a person may still use Gen9 (a task's
    trigger, standing.py): a moment's outage, not a server error."""
    return JSONResponse(
        {"detail": "Gen9's sign-in service didn't answer. Try again in a moment."},
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        headers={"Retry-After": "30"},
    )


@app.exception_handler(StarletteHTTPException)
async def refused_are_recorded(
    request: Request, exc: StarletteHTTPException
) -> Response:
    """Every 403 is recorded with who was refused (audit.py; ASVS 5.0 16.3.2), then answered as
    FastAPI would."""
    if exc.status_code == status.HTTP_403_FORBIDDEN:
        await audit.record(
            request,
            getattr(request.state, "actor", "unknown"),
            "access.refused",
            outcome=audit.DENIED,
            detail={"reason": str(exc.detail)[:200]},
        )
    return await http_exception_handler(request, exc)


app.include_router(health.router)
app.include_router(apps_host.router)
app.include_router(me.router)
app.include_router(export.router)
app.include_router(connectors.router)
app.include_router(environment_secrets.router)
app.include_router(directory.router)
app.include_router(threads.router)
app.include_router(files.router)
app.include_router(runs.router)
app.include_router(search.router)
app.include_router(tasks.router)
app.include_router(admin.router)
app.include_router(plugins.router)
app.include_router(my_plugins.router)
app.include_router(agui.router)
# Gen9 as an A2A agent (a2a_server.py): its Agent Card and `/a2a`
a2a_server.mount(app, get_settings())
# Gen9 as an MCP server (mcp_server.py): `/mcp` and its Protected Resource Metadata. Mounted last,
# so the API's own routes match first
mcp_app = mcp_server.asgi(app, get_settings())
app.mount("/", mcp_app)
