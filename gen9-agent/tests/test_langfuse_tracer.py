import httpx
import pytest

from gen9_agent import langfuse_tracer
from gen9_agent.langfuse_tracer import MediaStoreRoute, loopback, start_tracing


def test_loopback_names_and_addresses() -> None:
    for host in ("localhost", "media.localhost", "127.0.0.1", "127.0.0.2", "::1"):
        assert loopback(host), host
    for host in (
        "gen9-langfuse-media",
        "media.example.com",
        "10.0.0.5",
        "::ffff:10.0.0.5",
    ):
        assert not loopback(host), host


def test_a_loopback_upload_goes_to_the_store_with_its_signed_host() -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    route = MediaStoreRoute(
        "http://gen9-langfuse-media:9000", httpx.MockTransport(answer)
    )
    with httpx.Client(transport=route) as client:
        client.put(
            "http://localhost:13001/langfuse/media/a.png?X-Amz-Signature=s",
            content=b"x",
        )
        client.put(
            "https://media.example.com/langfuse/b.png?X-Amz-Signature=s", content=b"x"
        )
        client.get("http://gen9-langfuse:3000/api/public/health")

    assert str(seen[0].url) == (
        "http://gen9-langfuse-media:9000/langfuse/media/a.png?X-Amz-Signature=s"
    )
    assert seen[0].headers["host"] == "localhost:13001"
    assert (
        str(seen[1].url) == "https://media.example.com/langfuse/b.png?X-Amz-Signature=s"
    )
    assert str(seen[2].url) == "http://gen9-langfuse:3000/api/public/health"


@pytest.mark.parametrize(
    ("keys", "store", "made"),
    [
        (True, "http://gen9-langfuse-media:9000", True),
        (True, None, False),
        (False, "http://x", False),
    ],
)
def test_the_client_is_made_only_with_keys_and_a_store(
    monkeypatch, keys, store, made
) -> None:
    made_with: list[dict] = []
    monkeypatch.setattr("langfuse.Langfuse", lambda **kwargs: made_with.append(kwargs))
    monkeypatch.setattr(langfuse_tracer, "langfuse_configured", lambda: keys)
    if store:
        monkeypatch.setenv("LANGFUSE_MEDIA_INTERNAL_URL", store)
    else:
        monkeypatch.delenv("LANGFUSE_MEDIA_INTERNAL_URL", raising=False)
    monkeypatch.setenv("LANGFUSE_TIMEOUT", "35")

    start_tracing()

    assert bool(made_with) == made
    if made:
        client = made_with[0]["httpx_client"]
        assert isinstance(client._transport, MediaStoreRoute)
        assert client.timeout.read == 35
