"""Where connectors may connect (connector_net.py): the address a connection opens to is the one
checked, so a name that answers differently to the check and to the connection (DNS rebinding)
can't reach a private address, over both HTTP stacks connectors use."""

import asyncio
import socket
from collections.abc import Iterator

import httpcore
import httpcore2
import httpx
import httpx2
import pytest

from gen9_agent.connector_net import (
    ConnectorError,
    Reach,
    check_url,
    http_client,
    is_public,
    mcp_http_client,
)

pytestmark = pytest.mark.asyncio

PUBLIC = "93.184.216.34"
METADATA = "169.254.169.254"


def answers(monkeypatch: pytest.MonkeyPatch, *addresses: str) -> list[str]:
    """Makes the running loop's resolver answer these addresses in turn (the last one again after
    that), as a rebinding name does; returns the names it was asked for."""
    asked: list[str] = []
    turns: Iterator[str] = iter(addresses)
    last = addresses[-1]

    async def getaddrinfo(host, port, *args, **kwargs):
        asked.append(host)
        address = next(turns, last)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", getaddrinfo)
    return asked


class Recorder:
    """An inner network backend that records where it was asked to connect, and connects nowhere."""

    def __init__(self, error: type[Exception]) -> None:
        self.error = error
        self.connected: list[tuple[str, int]] = []

    async def connect_tcp(self, host, port, **kwargs):
        self.connected.append((host, port))
        raise self.error("recorded")

    async def sleep(self, seconds: float) -> None:
        pass


def recording(client, error: type[Exception]) -> Recorder:
    # The checked backend sits on the transport's pool: record behind it
    recorder = Recorder(error)
    client._transport._pool._network_backend.inner = recorder
    return recorder


async def test_a_name_that_turns_private_after_its_check_is_refused_at_connect(
    monkeypatch,
) -> None:
    asked = answers(monkeypatch, PUBLIC, METADATA)
    assert (
        await check_url("https://rebind.test/mcp", False) == "https://rebind.test/mcp"
    )
    async with http_client(False) as http:
        recorder = recording(http, httpcore.ConnectError)
        with pytest.raises(ConnectorError, match="private network"):
            await http.get("https://rebind.test/mcp")
    assert asked == ["rebind.test", "rebind.test"]
    assert recorder.connected == []  # nothing was opened to the metadata address


async def test_the_connection_goes_to_the_address_checked_and_tls_keeps_the_name(
    monkeypatch,
) -> None:
    answers(monkeypatch, PUBLIC)
    async with http_client(False) as http:
        recorder = recording(http, httpcore.ConnectError)
        with pytest.raises(httpx.ConnectError):
            await http.get("https://server.test/mcp")
    assert recorder.connected == [(PUBLIC, 443)]


async def test_the_mcp_transports_client_checks_the_same_way(monkeypatch) -> None:
    answers(monkeypatch, METADATA)
    async with mcp_http_client(False)(headers={"X-Test": "1"}) as http:
        assert (
            not http.follow_redirects
        )  # the MCP transport follows within the origin itself
        recorder = recording(http, httpcore2.ConnectError)
        with pytest.raises(ConnectorError, match="private network"):
            await http.post("https://rebind.test/mcp", json={})
    assert recorder.connected == []

    answers(monkeypatch, PUBLIC)
    async with mcp_http_client(False)() as http:
        recorder = recording(http, httpcore2.ConnectError)
        with pytest.raises(httpx2.ConnectError):
            await http.post("https://server.test/mcp", json={})
    assert recorder.connected == [(PUBLIC, 443)]


async def test_what_the_operator_allows_still_connects(monkeypatch) -> None:
    for reach in (True, Reach(allowed_hosts=frozenset({"notes.internal"}))):
        answers(monkeypatch, "10.0.0.5")
        async with http_client(reach) as http:
            recorder = recording(http, httpcore.ConnectError)
            with pytest.raises(httpx.ConnectError):
                await http.get("http://notes.internal:8080/mcp")
        assert recorder.connected == [("10.0.0.5", 8080)]


async def test_an_ipv6_address_carrying_a_private_ipv4_one_is_private(
    monkeypatch,
) -> None:
    # NAT64's prefix counts as global to Python whatever it carries (gen9-learn.md, M9, F15)
    for address in (
        "64:ff9b::169.254.169.254",
        "64:ff9b::10.0.0.1",
        "::169.254.169.254",
        "::ffff:169.254.169.254",
        "64:ff9b:1::a9fe:a9fe",
    ):
        assert not is_public(address), address
    # A public IPv4 address stays public in them, as DNS64 synthesizes on IPv6-only networks
    for address in ("64:ff9b::8.8.8.8", PUBLIC, "2606:4700:4700::1111"):
        assert is_public(address), address
    answers(monkeypatch, "64:ff9b::169.254.169.254")
    with pytest.raises(ConnectorError, match="private network"):
        await check_url("https://nat64.test/mcp", False)
