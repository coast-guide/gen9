"""Whether a connector app's host may have a certificate: gen9-edge's on-demand TLS asks here first
(Caddy's `ask`; docs/plans/deploy.md, U5b-5). gen9-ui gives each connector's Views a host of their
own, `<id>.apps.<domain>`, the connector's id without its hyphens (gen9-ui/lib/apps.ts), so only a
connector that exists gets one, and nobody spends the domain's ACME limits on made-up names.

Unauthenticated, as Caddy asks: the answer is a yes or no for one guessed 128-bit id (connectors'
ids are random), never whose; and gen9-edge doesn't serve `/internal/` from outside. Caddy wants it
in milliseconds: one lookup by primary key."""

import re
import uuid

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import select

from ..deps import Session
from ..models import Connector

router = APIRouter()

# The first label of an app's host: a connector's id, 32 hex digits
APP_HOST = re.compile(r"^([0-9a-f]{32})\.apps\.")


@router.get("/internal/apps-host", include_in_schema=False)
async def apps_host(session: Session, domain: str = Query(max_length=253)) -> Response:
    match = APP_HOST.match(domain.lower())
    found = match is not None and await session.scalar(
        select(Connector.id).where(Connector.id == uuid.UUID(hex=match.group(1)))
    )
    return Response(
        status_code=status.HTTP_200_OK if found else status.HTTP_404_NOT_FOUND
    )
