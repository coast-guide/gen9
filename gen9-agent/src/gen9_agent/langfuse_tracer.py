"""The worker's Langfuse client, made once at start: its tracers and the erasure API take it from
`get_client()`.

Media: the SDK uploads a trace's images to presigned URLs on Langfuse's
`LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT`, which must be reachable by both the browser and the SDK
(langfuse.com/self-hosting/deployment/infrastructure/blobstorage). Locally that is
`http://localhost:13001`, the browser's address; inside this container localhost is the container
itself, and every upload failed ("Media upload error … Connection refused", manual-e2e.md, P3-C12).
With `LANGFUSE_MEDIA_INTERNAL_URL` set (compose.yaml: Langfuse's MinIO on the gen9-langfuse
network), an upload to a loopback address goes there instead, with its Host header unchanged, which
the URL's signature covers. A public media endpoint is reached as it is.
"""

import ipaddress
import os

import httpx

from .langfuse_erasure import langfuse_configured


def loopback(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class MediaStoreRoute(httpx.BaseTransport):
    """Sends requests for a loopback address to the media store's internal address."""

    def __init__(self, store: str, inner: httpx.BaseTransport | None = None) -> None:
        self.store = httpx.URL(store)
        self.inner = inner or httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if loopback(request.url.host):
            # The Host header was set from the URL when the request was built, and stays
            request.url = request.url.copy_with(
                scheme=self.store.scheme, host=self.store.host, port=self.store.port
            )
        return self.inner.handle_request(request)

    def close(self) -> None:
        self.inner.close()


def start_tracing() -> None:
    """Makes the Langfuse client when Langfuse is configured and its media store has an internal
    address; otherwise the SDK makes its own on first use."""
    store = os.environ.get("LANGFUSE_MEDIA_INTERNAL_URL")
    if not (langfuse_configured() and store):
        return
    from langfuse import Langfuse

    # Sync, as the SDK takes it: its media uploads run on its own threads, never on the event
    # loop. The timeout is the SDK's own client's (LANGFUSE_TIMEOUT, default 5 s)
    timeout = int(os.environ.get("LANGFUSE_TIMEOUT", "5"))
    Langfuse(
        httpx_client=httpx.Client(timeout=timeout, transport=MediaStoreRoute(store))
    )
