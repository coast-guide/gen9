"""`gen9 login` tells the person Gen9 is an AI system before their first question, as the web app
does under its composer (AI Act Art. 50(1) and (5); manual-e2e.md, P5-B1)."""

import pytest

from gen9_cli import main

pytestmark = pytest.mark.asyncio


async def test_signing_in_says_gen9_is_an_ai_system(monkeypatch, capsys) -> None:
    saved: list[object] = []

    class Keycloak:
        async def device_sign_in(self, show):
            return {"access_token": "token"}

    async def save_tokens(tokens) -> None:
        saved.append(tokens)

    async def whoami(keycloak, http, args) -> int:
        print("Signed in as Alan Turing (alan@gen9.test).")
        return 0

    monkeypatch.setattr(main, "save_tokens", save_tokens)
    monkeypatch.setattr(main, "cmd_whoami", whoami)
    assert await main.cmd_login(Keycloak(), None, None) == 0  # ty: ignore[invalid-argument-type]
    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        "Signed in as Alan Turing (alan@gen9.test).",
        "Gen9 is an AI system and can be wrong. Check its work before you rely on it.",
    ]
    assert saved
