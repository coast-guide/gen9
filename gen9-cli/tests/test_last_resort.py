"""An error gen9 didn't expect: one line in the terminal, the traceback in a file only the person can
read (manual-e2e.md, P6-B2; clig.dev, "Errors"). An issuer that answered 200 with something other
than JSON printed Python's whole traceback."""

import argparse
import asyncio
import json
import stat

import pytest

from gen9_cli import main

pytestmark = pytest.mark.asyncio


async def test_an_unexpected_error_is_one_line_and_its_details_a_private_file(
    monkeypatch, capsys, tmp_path
) -> None:
    monkeypatch.setenv("GEN9_CONFIG_DIR", str(tmp_path / "gen9"))

    async def discover(issuer, http):
        return json.loads("not json at all")

    monkeypatch.setattr(main.Keycloak, "discover", staticmethod(discover))
    code = await main.run(argparse.Namespace(command="whoami"))

    assert code == 1
    err = capsys.readouterr().err
    details = tmp_path / "gen9" / "last-error.txt"
    assert err.strip().splitlines() == [
        (
            "Something went wrong that gen9 didn't expect (JSONDecodeError). The details are in"
            f" {details}: if it happens again, report it with that file."
        )
    ]
    assert "Traceback" not in err
    # Off the event loop, as every file read in async code (AGENTS.md)
    text = await asyncio.to_thread(details.read_text)
    assert "Traceback (most recent call last)" in text and "JSONDecodeError" in text
    file_mode = (await asyncio.to_thread(details.stat)).st_mode
    folder_mode = (await asyncio.to_thread(details.parent.stat)).st_mode
    assert stat.S_IMODE(file_mode) == 0o600 and stat.S_IMODE(folder_mode) == 0o700
