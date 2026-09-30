"""Request-scoped dependencies backed by objects created in the app lifespan."""

from collections.abc import AsyncIterator
from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import Principal, forbidden, require_role
from .keycloak_admin import KeycloakAdmin, KeycloakAdminError

ADMIN_ROLE = "gen9-admin"


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def get_keycloak_admin(request: Request) -> KeycloakAdmin:
    admin = request.app.state.keycloak_admin
    if admin is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "User management is not configured"
        )
    return admin


Session = Annotated[AsyncSession, Depends(get_session)]
Admin = Annotated[KeycloakAdmin, Depends(get_keycloak_admin)]


async def require_admin(
    principal: Annotated[Principal, Depends(require_role(ADMIN_ROLE))],
    keycloak: Admin,
) -> Principal:
    """An admin now, not only when the token was issued. A token keeps its roles for its life (5
    minutes), so someone whose admin access was just removed could still act as one until it
    expires (seen live, docs/plans/manual-e2e.md J3). Keycloak is asked on every admin
    request, as OWASP ASVS 5.0 8.3.2 asks (authorization changes apply immediately); when it can't
    answer, the request is refused."""
    try:
        roles = await keycloak.realm_roles(principal.sub)
    except KeycloakAdminError as exc:
        if exc.status_code == 404:
            raise forbidden(ADMIN_ROLE) from exc
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Identity provider unreachable"
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Identity provider unreachable"
        ) from exc
    if ADMIN_ROLE not in roles:
        raise forbidden(ADMIN_ROLE)
    return principal


AdminPrincipal = Annotated[Principal, Depends(require_admin)]
