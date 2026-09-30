"""Which servers a connector may reach, checked before every connection (connectors.py,
connector_auth.py): https and public addresses only, unless the operator allows private networks
or names hosts (Decision Log, SSRF).

The check is made where the connection opens, too. A name can answer one address to a check and
another to the connection that follows (DNS rebinding: a public address, then a private one), so
the HTTP clients that talk to connectors' servers (`http_client`, `mcp_http_client`) resolve the
host when they connect, check the addresses they got by the same rule, and connect to one of
those. TLS still verifies the server's name. Plugin sources pin git to their checked address the
same way (plugin_sources.py)."""

import asyncio
import ipaddress
import socket
import typing
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpcore
import httpcore2
import httpx
import httpx2
from mcp.shared._httpx_utils import MCP_DEFAULT_SSE_READ_TIMEOUT, MCP_DEFAULT_TIMEOUT

from .settings import Settings


class ConnectorError(Exception):
    """What went wrong with a connector, in words for the person."""


@dataclass(frozen=True)
class Reach:
    """Which servers connectors may reach: public https ones, and, as the operator allows, every
    private address (`allow_private`) or only named hosts (`allowed_hosts`, "host" or
    "host:port"), over http too."""

    allow_private: bool = False
    allowed_hosts: frozenset[str] = frozenset()

    def named(self, host: str, port: int | None) -> bool:
        return host in self.allowed_hosts or f"{host}:{port}" in self.allowed_hosts


def reach(settings: Settings) -> Reach:
    """What the operator lets connectors reach (CONNECTORS_ALLOW_PRIVATE, CONNECTORS_ALLOWED_HOSTS)."""
    return Reach(
        settings.connectors_allow_private, frozenset(settings.connectors_allowed_hosts)
    )


def _reach(reach: "Reach | bool") -> Reach:
    return Reach(allow_private=reach) if isinstance(reach, bool) else reach


# IPv6 forms whose last 32 bits are an IPv4 address that a gateway or stack may turn back into
# it: NAT64's well-known prefix (RFC 6052), which Python counts as global whatever it carries, and
# the deprecated IPv4-compatible form (RFC 4291). 64:ff9b::169.254.169.254 passed as public, and
# through a NAT64 gateway would reach a cloud's metadata service (gen9-learn.md, M9, F15). A
# public IPv4 address in them stays public: DNS64 synthesizes these on IPv6-only networks
_CARRY_IPV4 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("::/96"))


def is_public(address: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """A globally reachable address, and so is any IPv4 address it carries."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:  # an address with a zone (fe80::1%eth0) is link-local anyway
        return False
    if not ip.is_global:
        return False
    if ip.version == 6 and any(ip in net for net in _CARRY_IPV4):
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF).is_global
    return True


def _public(address: str) -> bool:
    return is_public(address)


async def address_for(host: str, port: int, reach: "Reach | bool") -> str:
    """The address to connect to for `host`: what it resolves to now, each address public unless
    the operator allows it. Raises ConnectorError."""
    reach = _reach(reach)
    try:
        found = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )
    except socket.gaierror as e:
        raise ConnectorError("Gen9 couldn't find that server.") from e
    addresses = [str(sockaddr[0]) for *_, sockaddr in found]
    lenient = reach.allow_private or reach.named(host, port)
    if not lenient and not all(_public(a) for a in addresses):
        raise ConnectorError(
            "That server is on a private network, which connectors can't reach."
        )
    return addresses[0]


async def check_url(url: str, reach: "Reach | bool") -> str:
    """The URL if a connector may use it: https (http too for a private network or a named host
    the operator allows), no credentials in it, and a host that resolves only to public addresses
    unless the operator allows it. Raises ConnectorError."""
    reach = _reach(reach)
    parts = urlsplit(url.strip())
    named = bool(parts.hostname) and reach.named(parts.hostname or "", parts.port)
    lenient = reach.allow_private or named
    if parts.scheme != "https" and not (lenient and parts.scheme == "http"):
        raise ConnectorError("Use an https:// address.")
    if not parts.hostname or parts.username or parts.password:
        raise ConnectorError("That isn't a server address.")
    default = 443 if parts.scheme == "https" else 80
    await address_for(parts.hostname, parts.port or default, reach)
    return url.strip()


class _Checked:
    """A network backend that connects to the address `address_for` gives for the host, instead
    of letting the connection resolve the name again. httpcore and httpcore2 hand it the host and
    port of each new connection, and start TLS with the request's own name afterwards."""

    def __init__(self, reach: Reach, inner: typing.Any) -> None:
        self.reach = reach
        self.inner = inner

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 (httpcore's interface passes it)
        local_address: str | None = None,
        socket_options: typing.Iterable[typing.Any] | None = None,
    ) -> typing.Any:
        address = await address_for(host, port, self.reach)
        return await self.inner.connect_tcp(
            address,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self, *args: typing.Any, **kwargs: typing.Any
    ) -> typing.Any:
        raise ConnectorError("That isn't a server address.")

    async def sleep(self, seconds: float) -> None:
        await self.inner.sleep(seconds)


class _CheckedBackend(_Checked, httpcore.AsyncNetworkBackend):
    pass


class _CheckedBackend2(_Checked, httpcore2.AsyncNetworkBackend):
    pass


def _checked(
    transport: typing.Any, backend: type[_Checked], reach: Reach
) -> typing.Any:
    # The transports take no network backend of their own; their pool does, and each new
    # connection gets it from there (a private attribute of httpcore's and httpcore2's pools,
    # the same in both: tests/test_connector_net.py fails if it goes)
    pool = transport._pool
    pool._network_backend = backend(reach, pool._network_backend)
    return transport


def http_client(reach: "Reach | bool", **kwargs: typing.Any) -> httpx.AsyncClient:
    """An httpx client for a connector's server (its OAuth metadata, tokens, revocation)."""
    transport = _checked(httpx.AsyncHTTPTransport(), _CheckedBackend, _reach(reach))
    return httpx.AsyncClient(transport=transport, **kwargs)


def mcp_http_client(reach: "Reach | bool") -> typing.Callable[..., httpx2.AsyncClient]:
    """A factory for the MCP transport's httpx2 clients (StreamableHttpTransport's
    `httpx_client_factory`), as the SDK's own `create_mcp_http_client` makes them: its timeouts
    when none is given, and redirects left off (the transport follows those within the
    endpoint's origin itself), whatever FastMCP asks for."""

    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx2.Timeout | None = None,
        auth: httpx2.Auth | None = None,
        **_: typing.Any,
    ) -> httpx2.AsyncClient:
        transport = _checked(
            httpx2.AsyncHTTPTransport(), _CheckedBackend2, _reach(reach)
        )
        return httpx2.AsyncClient(
            transport=transport,
            headers=headers,
            auth=auth,
            timeout=timeout
            or httpx2.Timeout(MCP_DEFAULT_TIMEOUT, read=MCP_DEFAULT_SSE_READ_TIMEOUT),
        )

    return factory
