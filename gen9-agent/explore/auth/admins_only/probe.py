"""P2-E3: does the admins-only flow turn a non-admin away both at sign-in and when they already have
a Keycloak session (keycloak/keycloak discussion #38350)? Against setup.sh's throwaway Keycloak.

    uv run --no-project --with httpx python probe.py
"""

import asyncio
import re
from html import unescape

import httpx

KC = "http://127.0.0.1:18099/realms/probe/protocol/openid-connect/auth"
CB = "http://127.0.0.1:18098"
DENY = "Only Gen9 admins can open this."


class Browser:
    """Cookies kept as a browser keeps them on localhost: Keycloak sets them Secure even over http,
    which browsers accept for localhost and httpx's cookie jar doesn't send."""

    def __init__(self, web: httpx.AsyncClient) -> None:
        self.web, self.jar = web, {}

    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        cookie = "; ".join(f"{k}={v}" for k, v in self.jar.items())
        response = await self.web.request(
            method, url, headers={"Cookie": cookie} if cookie else {}, **kwargs
        )
        for header in response.headers.get_list("set-cookie"):
            name, _, rest = header.partition("=")
            value = rest.split(";", 1)[0]
            if "Max-Age=0" in header or not value:
                self.jar.pop(name, None)
            else:
                self.jar[name] = value
        return response


async def authorize(web: "Browser", client_id: str, path: str) -> httpx.Response:
    return await web.request(
        "GET",
        KC,
        params={
            "client_id": client_id,
            "redirect_uri": f"{CB}/{path}",
            "response_type": "code",
            "scope": "openid",
            "state": "s",
        },
    )


def outcome(response: httpx.Response) -> str:
    location = response.headers.get("location", "")
    if response.status_code in (302, 303) and location.startswith(CB):
        return "signed in (code)"
    if DENY in unescape(response.text):
        return f"turned away: {DENY!r} ({response.status_code})"
    if 'id="kc-form-login"' in response.text:
        return "asked to sign in"
    return f"other: {response.status_code} {re.sub(r'<[^>]+>', ' ', response.text)[:120]!r}"


async def sign_in(web: Browser, client_id: str, path: str, user: str) -> str:
    page = await authorize(web, client_id, path)
    if outcome(page) != "asked to sign in":
        return outcome(page)
    action = unescape(re.search(r'action="([^"]+)"', page.text).group(1))
    return outcome(
        await web.request(
            "POST",
            action,
            data={"username": user, "password": f"{user}-probe-not-secret"},
        )
    )


async def case(name: str, steps: list[tuple[str, str, str]]) -> None:
    async with httpx.AsyncClient(follow_redirects=False, timeout=15) as http:
        web = Browser(http)  # one browser for the whole case
        results = [
            f"{client} as {user}: {await sign_in(web, client, path, user)}"
            for client, path, user in steps
        ]
    print(f"{name}\n  " + "\n  ".join(results))


async def main() -> None:
    await case(
        "1. bob (no admin), fresh, at the admins-only client",
        [("probe-ui", "cb", "bob")],
    )
    await case("2. alice (admin), fresh", [("probe-ui", "cb", "alice")])
    await case(
        "3. bob signed in elsewhere first (a Keycloak session), then the admins-only client, then elsewhere again",
        [
            ("other", "other", "bob"),
            ("probe-ui", "cb", "bob"),
            ("other", "other", "bob"),
        ],
    )
    await case(
        "4. alice signed in elsewhere first, then the admins-only client",
        [("other", "other", "alice"), ("probe-ui", "cb", "alice")],
    )


asyncio.run(main())
