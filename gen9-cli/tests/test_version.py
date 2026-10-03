"""Which versions are in play (docs/plans/deploy.md, U7): `gen9 --version` is the terminal's own,
asked of no server; `gen9 whoami` also names the Gen9 it reached, version and commit, and leaves
that out when the API can't say (an older Gen9, without `/v1/version`)."""

from importlib.metadata import version

import httpx2
import pytest

from gen9_cli import main
from gen9_cli.main import parse

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def signed_in(monkeypatch: pytest.MonkeyPatch) -> None:
    async def token(_keycloak) -> str:
        return "token"

    monkeypatch.setattr(main, "access_token", token)


async def test_the_terminals_version_needs_no_server(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit:
        parse(["--version"])
    assert exit.value.code == 0
    assert capsys.readouterr().out.strip() == f"gen9 {version('gen9-cli')}"


async def whoami(server_version: httpx2.Response) -> int:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/v1/me":
            return httpx2.Response(
                200, json={"name": "Alan Turing", "email": "alan@gen9.test"}
            )
        assert request.url.path == "/v1/version"
        return server_version

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        return await main.cmd_whoami(None, http, parse(["whoami"]))  # ty: ignore[invalid-argument-type]


async def test_whoami_names_the_gen9_it_reached(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reply = httpx2.Response(200, json={"version": "0.1.0", "commit": "0123abcdef4567"})
    assert await whoami(reply) == 0
    out = capsys.readouterr().out
    assert "Signed in as Alan Turing (alan@gen9.test)." in out
    assert "Gen9 0.1.0, commit 0123abc." in out


async def test_a_gen9_built_from_local_code_says_so(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        await whoami(httpx2.Response(200, json={"version": "0.1.0", "commit": ""})) == 0
    )
    assert "Gen9 0.1.0, built from local code." in capsys.readouterr().out


async def test_an_older_gen9_without_it_is_left_out(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert await whoami(httpx2.Response(404, json={"detail": "Not Found"})) == 0
    out = capsys.readouterr().out
    assert "Signed in as" in out and "Gen9 " not in out.replace("gen9.test", "")
