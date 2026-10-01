"""Build step: OpenSandbox's credential vault releases a secret only onto a TLS session whose peer
proved a name the secret's binding trusts (its SNI). Without it, the vault chose the secret by the
request's Host header, which the sandbox's code writes: code sending `Host: <one secret's host>`
over a session to another allowed host had that secret added and delivered there. Found in Gen9,
a host that echoes requests handed the code another host's secret (docs/plans/manual-e2e.md,
P4-E4b). OpenSandbox issue #1758; the code below is its fix, PR #1759 (Apache-2.0, unmerged),
carried until an egress release has it. The Dockerfile checks system.py is the pinned release's
first, and each anchor here must occur exactly once, so an upgrade fails the build rather than
patching a file it doesn't know.

Usage: python3 sni-binding.py /var/egress/mitmscripts/system.py
"""

import sys

HELPERS_BEFORE = "def _request_may_be_streamed(flow: http.HTTPFlow) -> bool:\n"
HELPERS = '''def _flow_sni(flow: http.HTTPFlow) -> str | None:
    """Normalized ClientHello SNI of the intercepted connection, or None.

    Read from the client connection rather than the server connection: at
    ``requestheaders`` time the upstream connection is not established yet, and
    mitmproxy carries the client SNI into the upstream handshake, where it
    becomes the hostname the upstream certificate is verified against.
    """
    sni = getattr(getattr(flow, "client_conn", None), "sni", None)
    if not isinstance(sni, str) or not sni:
        return None
    return sni.rstrip(".").lower()


def _sni_outside_binding_scope(flow: http.HTTPFlow, binding: dict[str, Any]) -> bool:
    """True when the TLS endpoint identity falls outside the binding host scope.

    A binding match alone is not enough to release a credential:
    :func:`_binding_matches` runs on ``request.pretty_host``, which is the Host
    (or HTTP/2 ``:authority``) header — unauthenticated client input. A sandbox
    can open a TLS session to any reachable host and still send
    ``Host: api.github.com``, which would hand that binding's credential to a
    peer of its choosing. The SNI is the name mitmproxy verifies the upstream
    certificate against, so requiring it to match ``match.hosts`` as well binds
    the injection decision to an identity the peer had to prove with a
    certificate.

    Returns False when no SNI is available: plaintext HTTP carries none, and
    no-SNI TLS never reaches this hook because :func:`tls_clienthello` passes it
    through. For those flows the egress allow rules stay the only control.
    """
    sni = _flow_sni(flow)
    if sni is None:
        return False
    patterns = (binding.get("match") or {}).get("hosts") or []
    return not any(_host_matches(sni, pattern)[0] for pattern in patterns)


'''

CHECK_AFTER = (
    "    binding = _select_binding(flow, vault)\n    if not binding:\n        return\n"
)
CHECK = """
    # A matched binding is not sufficient: the match ran on the Host header,
    # which the sandbox controls. Release the credential only onto a TLS
    # session whose peer had to prove a name the binding trusts.
    if _sni_outside_binding_scope(flow, binding):
        _reject_request(
            flow, b"request endpoint identity does not match credential binding\\n"
        )
        ctx.log.warn(
            "credential proxy: rejected request whose TLS endpoint identity is "
            f"outside binding={binding.get('name')} scope: sni={_flow_sni(flow)} "
            f"host={_request_host(flow)}"
        )
        return
"""


def main(path: str) -> None:
    with open(path) as f:
        source = f.read()
    for anchor in (HELPERS_BEFORE, CHECK_AFTER):
        if source.count(anchor) != 1:
            sys.exit(f"sni-binding: {anchor.strip()!r} is not in {path} exactly once")
    source = source.replace(HELPERS_BEFORE, HELPERS + HELPERS_BEFORE)
    source = source.replace(CHECK_AFTER, CHECK_AFTER + CHECK)
    compile(source, path, "exec")
    with open(path, "w") as f:
        f.write(source)


if __name__ == "__main__":
    main(sys.argv[1])
