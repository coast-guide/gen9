"""Whether work may still be done for a person: their Keycloak account exists and is enabled
(docs/plans/harness.md, "Auth across the harness").

Scheduled tasks, API triggers, background tasks and queued runs act for a person who isn't
there. Gen9's workers do that with its own service identity, so nothing else ends the work
when the person's access ends. Microsoft Entra Agent ID's model has the same rule: delegated
background access lasts only while the person's does. So a run, a firing and a notice first
ask this, and a person an admin disabled, in Gen9 or in Keycloak's console, or who was deleted,
gets nothing done for them. Keycloak is the truth. Each process asks it at most once a minute
per person. A Keycloak that doesn't answer raises, so Temporal retries rather than guessing.
The runtime makes it; the API and the worker give it their Keycloak admin client. Without one
(no admin client configured) it can't tell, and says yes.
"""

import time
from typing import Any

import httpx
from langchain.agents.middleware import AgentMiddleware

from .keycloak_admin import KeycloakAdmin, KeycloakAdminError

# How long an answer is kept: disabling someone stops their work within this
TTL_S = 60.0
# What a failure says when Keycloak couldn't be asked: a run waiting for Retry names it so
# (runs/store.py, `retry_reason`), not the model provider (manual-e2e.md, P8-N3)
KEYCLOAK_UNAVAILABLE = "Keycloak didn't answer"


class IdentityUnavailable(RuntimeError):
    """Keycloak couldn't say whether the person may still use Gen9: down, or erring."""


class Standing:
    def __init__(
        self, admin: KeycloakAdmin | None = None, ttl_s: float = TTL_S
    ) -> None:
        self.admin = admin
        self.ttl_s = ttl_s
        self._known: dict[str, tuple[bool, float]] = {}

    def tell(self, sub: str, active: bool) -> None:
        """What this process now knows of a person, without waiting out the minute: an admin
        just disabled or enabled them here."""
        self._known[sub] = (active, time.monotonic())

    async def active(self, sub: str, fresh: bool = False) -> bool:
        """`fresh`: ask Keycloak now, not what it said up to a minute ago (a sandbox made while its
        person's account was being deleted must not outlive it: environment_activities.py)."""
        if self.admin is None:
            return True
        now = time.monotonic()
        known = self._known.get(sub)
        if known and now - known[1] < self.ttl_s and not fresh:
            return known[0]
        try:
            active = bool((await self.admin.get_user(sub)).get("enabled"))
        except KeycloakAdminError as e:
            if e.status_code != 404:
                raise IdentityUnavailable(f"{KEYCLOAK_UNAVAILABLE}: {e}") from None
            active = False
        except httpx.HTTPError as e:
            raise IdentityUnavailable(
                f"{KEYCLOAK_UNAVAILABLE}: {type(e).__name__}: {e}"
            ) from None
        self._known[sub] = (active, now)
        return active


class PersonInactive(Exception):
    """The run's person was disabled or deleted while it ran."""


class StillActive(AgentMiddleware):
    """Before each model call, whether the run's person may still have work done: one disabled or
    deleted mid-turn, in Gen9 or in Keycloak's console, stops within a minute (the answer is kept
    that long), whatever started the turn and in its subagents too (docs/plans/manual-e2e.md,
    P5-C10). The run ends as it does when it starts for such a person."""

    def __init__(self, standing: "Standing") -> None:
        super().__init__()
        self.standing = standing

    async def abefore_model(self, state: Any, runtime: Any) -> None:
        sub = getattr(runtime.context, "user_sub", None)
        if sub and not await self.standing.active(sub):
            raise PersonInactive(sub)
