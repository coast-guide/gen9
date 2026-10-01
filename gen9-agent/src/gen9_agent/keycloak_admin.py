"""Minimal Keycloak Admin REST API client for user management.

Authenticates as gen9-agent's service account (client credentials). The service account holds
only realm-management roles view-users, manage-users and query-groups, so it can manage users but
not realm settings or clients. Docs: https://www.keycloak.org/docs-api/latest/rest-api/
"""

import asyncio
import time
from typing import Any

import httpx

from .settings import Settings


def contains(search: str) -> str:
    """A user search that matches any part of a name or email, as the admin page promises.
    Keycloak's own is prefix-based (`foo` means `foo*`); `*foo*` finds it anywhere (its Admin REST
    API, UsersResource). An admin's own `*` or `"exact"` query passes as typed."""
    if "*" in search or (len(search) > 1 and search[0] == search[-1] == '"'):
        return search
    return f"*{search}*"


class KeycloakAdminError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class KeycloakAdmin:
    ADMIN_GROUP = "admins"

    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        if settings.keycloak_admin_client_secret is None:
            raise ValueError("KEYCLOAK_ADMIN_CLIENT_SECRET is not set")
        self._http = http
        self._token_url = f"{settings.keycloak_base_url}/realms/{settings.realm}/protocol/openid-connect/token"
        self._admin = f"{settings.keycloak_base_url}/admin/realms/{settings.realm}"
        self._client_id = settings.keycloak_admin_client_id
        self._client_secret = settings.keycloak_admin_client_secret.get_secret_value()
        self._token: str | None = None
        self._token_expires_at = 0.0

    async def access_token(self, min_valid_s: float = 30) -> str:
        """gen9-agent's service-account token (client credentials): the Admin API's, and the one
        gen9-agent shows Temporal (its `permissions` claim grants gen9:write). Reused while it
        stays valid for at least `min_valid_s` more seconds."""
        # Wall time, as the token's own expiry is: a Mac that slept paused Docker's VM, whose
        # monotonic clock then lagged wall time, so an expired token looked fresh and Temporal
        # refused it ("Token is expired") for minutes after waking (M9)
        if self._token and time.time() < self._token_expires_at - min_valid_s:
            return self._token
        response = await self._http.post(
            self._token_url,
            data={"grant_type": "client_credentials"},
            auth=(self._client_id, self._client_secret),
        )
        if response.status_code != 200:
            raise KeycloakAdminError(502, "Could not authenticate to Keycloak")
        body = response.json()
        self._token = body["access_token"]
        self._token_expires_at = time.time() + body["expires_in"]
        return self._token

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        token = await self.access_token()
        response = await self._http.request(
            method,
            f"{self._admin}{path}",
            headers={"Authorization": f"Bearer {token}"},
            **kwargs,
        )
        if response.status_code == 404:
            raise KeycloakAdminError(404, "Not found")
        if response.status_code >= 400:
            raise KeycloakAdminError(502, f"Keycloak returned {response.status_code}")
        return response

    async def _admin_group_id(self) -> str:
        groups = (
            await self._request(
                "GET", "/groups", params={"search": self.ADMIN_GROUP, "exact": "true"}
            )
        ).json()
        for group in groups:
            if group["name"] == self.ADMIN_GROUP:
                return group["id"]
        raise KeycloakAdminError(
            500, f"Group {self.ADMIN_GROUP!r} is missing in the realm"
        )

    async def list_users(
        self, search: str | None, first: int, max_results: int
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "first": first,
            "max": max_results,
            "briefRepresentation": "true",
        }
        if search:
            params["search"] = contains(search)
        users = (await self._request("GET", "/users", params=params)).json()
        admin_ids = await self.admin_ids()
        users = [
            u for u in users if not u.get("username", "").startswith("service-account-")
        ]
        # Brute-force lockout per user (Keycloak keeps it outside the user record)
        lockouts = await asyncio.gather(
            *(
                self._request("GET", f"/attack-detection/brute-force/users/{u['id']}")
                for u in users
            )
        )
        locked = {
            u["id"]: bool(r.json().get("disabled"))
            for u, r in zip(users, lockouts, strict=True)
        }
        return [
            {
                "id": u["id"],
                "email": u.get("email"),
                "first_name": u.get("firstName"),
                "last_name": u.get("lastName"),
                "enabled": u.get("enabled", False),
                "email_verified": u.get("emailVerified", False),
                "created_at_ms": u.get("createdTimestamp"),
                "is_admin": u["id"] in admin_ids,
                "required_actions": u.get("requiredActions", []),
                "locked": locked[u["id"]],
            }
            for u in users
        ]

    async def admin_ids(self) -> set[str]:
        """Members of the admins group (direct members; Gen9 doesn't nest groups)."""
        members = (
            await self._request(
                "GET",
                f"/groups/{await self._admin_group_id()}/members",
                params={"max": 1000, "briefRepresentation": "true"},
            )
        ).json()
        return {m["id"] for m in members}

    async def count_users(self, search: str | None) -> int:
        params = {"search": contains(search)} if search else {}
        return int((await self._request("GET", "/users/count", params=params)).json())

    async def set_enabled(self, user_id: str, enabled: bool) -> None:
        await self._request("PUT", f"/users/{user_id}", json={"enabled": enabled})

    async def realm_roles(self, user_id: str) -> set[str]:
        """The user's realm roles now, their own and their groups' (composite): what a token
        issued now would carry."""
        roles = (
            await self._request(
                "GET",
                f"/users/{user_id}/role-mappings/realm/composite",
                params={"briefRepresentation": "true"},
            )
        ).json()
        return {r["name"] for r in roles}

    async def set_admin(self, user_id: str, is_admin: bool) -> None:
        group_id = await self._admin_group_id()
        await self._request(
            "PUT" if is_admin else "DELETE", f"/users/{user_id}/groups/{group_id}"
        )

    async def unlock(self, user_id: str) -> None:
        """Clear a brute-force lockout (temporary or permanent) so the user can sign in again."""
        await self._request("DELETE", f"/attack-detection/brute-force/users/{user_id}")

    async def logout(self, user_id: str) -> None:
        """Ends every sign-in of the person: their sessions (Keycloak tells gen9-ui by back-channel
        logout), and their offline sessions. An agent that asked for `offline_access` holds one,
        and Keycloak's logout leaves it ("The offline token is valid after a user logout", its
        guide): a refresh worked after "Sign out everywhere" (docs/plans/manual-e2e.md, P7-B1).
        Every client holding one is among the person's consents, with an "Offline Token" grant."""
        await self._request("POST", f"/users/{user_id}/logout")
        consents = (await self._request("GET", f"/users/{user_id}/consents")).json()
        clients = {
            grant["client"]
            for consent in consents
            for grant in consent.get("additionalGrants") or []
            if grant.get("key") == "Offline Token"
        }
        for client in clients:
            sessions = (
                await self._request(
                    "GET", f"/users/{user_id}/offline-sessions/{client}"
                )
            ).json()
            for offline in sessions:
                try:
                    await self._request(
                        "DELETE",
                        f"/sessions/{offline['id']}",
                        params={"isOffline": "true"},
                    )
                except KeycloakAdminError as error:
                    if error.status_code != 404:  # gone meanwhile: what was wanted
                        raise

    async def credentials(self, user_id: str) -> list[dict[str, Any]]:
        """Credential metadata only (type, created date); Keycloak never returns secrets here."""
        return (await self._request("GET", f"/users/{user_id}/credentials")).json()

    async def user_ids(self) -> set[str]:
        """Every user id in the realm, 100 per request."""
        ids: set[str] = set()
        first = 0
        while True:
            page = (
                await self._request(
                    "GET",
                    "/users",
                    params={"first": first, "max": 100, "briefRepresentation": "true"},
                )
            ).json()
            ids.update(u["id"] for u in page)
            if len(page) < 100:
                return ids
            first += 100

    async def user_exists(self, user_id: str) -> bool:
        try:
            await self._request("GET", f"/users/{user_id}")
        except KeycloakAdminError as exc:
            if exc.status_code == 404:
                return False
            raise
        return True

    async def get_user(self, user_id: str) -> dict[str, Any]:
        return (await self._request("GET", f"/users/{user_id}")).json()

    async def delete_user(self, user_id: str) -> None:
        """Deletes the user, their credentials and sessions from Keycloak."""
        await self._request("DELETE", f"/users/{user_id}")

    async def consents(self, user_id: str) -> list[dict[str, Any]]:
        """The apps the user allowed to use their account: each client's id, the scopes it was
        granted and when (`createdDate`, `lastUpdatedDate`)."""
        return (await self._request("GET", f"/users/{user_id}/consents")).json()

    async def events(self, user_id: str, most: int = 10_000) -> list[dict[str, Any]]:
        """The user's sign-in records, newest first, for as long as the realm keeps them (30 days).
        Needs `view-events`."""
        found: list[dict[str, Any]] = []
        while len(found) < most:
            page = (
                await self._request(
                    "GET",
                    "/events",
                    params={"user": user_id, "first": len(found), "max": 100},
                )
            ).json()
            found += page
            if len(page) < 100:
                break
        return found[:most]

    async def delete_credential(self, user_id: str, credential_id: str) -> None:
        await self._request("DELETE", f"/users/{user_id}/credentials/{credential_id}")

    async def send_password_reset(self, user_id: str) -> None:
        await self._request(
            "PUT",
            f"/users/{user_id}/execute-actions-email",
            params={"lifespan": 3600},
            json=["UPDATE_PASSWORD"],
        )
