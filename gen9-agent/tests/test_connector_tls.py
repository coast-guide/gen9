"""A connector's server whose TLS fails (connectors.py, tls_failed; manual-e2e.md, P6-C4): logged
as a warning with why, a backend TLS failure being a security event, and told to the person as a
certificate that isn't valid, where it was "That doesn't look like an MCP server". Other failures
are told as before."""

import logging

import pytest

from gen9_agent import connectors
from gen9_agent.connector_net import ConnectorError

pytestmark = pytest.mark.asyncio

EXPIRED = (
    "Client failed to connect: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
    "certificate has expired (_ssl.c:1010)"
)


def server_failing_with(error: BaseException):
    class Adapter:
        def __init__(self, client) -> None:
            pass

        async def list_tools(self):
            raise error

    return Adapter


async def listed(monkeypatch, error: BaseException) -> str:
    async def allowed(url, reach):
        return url

    monkeypatch.setattr(connectors, "check_url", allowed)
    monkeypatch.setattr(connectors, "MCPAdapter", server_failing_with(error))
    monkeypatch.setattr(connectors, "client", lambda *args: None)
    with pytest.raises(ConnectorError) as refused:
        await connectors.discover("https://expired.example/mcp", None, None, False)
    return str(refused.value)


async def test_a_tls_failure_is_logged_and_told_as_a_certificate(
    monkeypatch, caplog
) -> None:
    with caplog.at_level(logging.INFO, logger="gen9_agent.connectors"):
        said = await listed(monkeypatch, RuntimeError(EXPIRED))
    assert said == connectors.TLS_FAILED
    [record] = caplog.records
    assert record.levelno == logging.WARNING
    assert (
        record.getMessage()
        == f"connector https://expired.example/mcp: TLS failed: {EXPIRED}"
    )


async def test_a_call_whose_tls_fails_says_so_too(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="gen9_agent.connectors"):
        said = connectors.failed(RuntimeError(EXPIRED))
    assert str(said) == connectors.TLS_FAILED
    assert [r.levelno for r in caplog.records] == [logging.WARNING]


async def test_other_failures_are_told_as_before(monkeypatch, caplog) -> None:
    with caplog.at_level(logging.INFO, logger="gen9_agent.connectors"):
        said = await listed(monkeypatch, RuntimeError("Connect call failed"))
    assert said == "Gen9 couldn't reach that server."
    assert not caplog.records
