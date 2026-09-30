"""Probe: how narrow can a LiteLLM virtual key be? (gen9-models' pinned v1.102.1)

gen9-agent's API embeds search queries. Can a key be limited to that: the `embed` alias, no other
model, no search tool, no management route? Runs against the gen9-models stack (make up
STACKS=models) with its master key from gen9-models/.env, creates throwaway keys, deletes them.
Run from gen9-agent/: uv run python explore/models/key_scope.py
Prints status codes and error types; secrets never printed.
"""

import asyncio
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[3]
ENV = dict(
    line.split("=", 1)
    for line in (ROOT / "gen9-models/.env").read_text().splitlines()
    if "=" in line and not line.startswith("#")
)
URL = f"http://localhost:{ENV.get('GEN9_MODELS_PORT', '19000')}"
END_USER = "key-scope-probe"


def brief(r: httpx.Response) -> str:
    try:
        error = r.json().get("error", {})
    except ValueError:
        return f"{r.status_code} {r.text[:80]!r}"
    if not error:
        return str(r.status_code)
    return f"{r.status_code} {error.get('type')}: {str(error.get('message'))[:100]}"


async def main() -> None:
    master = {"Authorization": f"Bearer {ENV['LITELLM_MASTER_KEY']}"}
    async with httpx.AsyncClient(base_url=URL, timeout=60) as http:
        aliases = ["probe-models", "probe-no-search", "probe-empty", "probe-later"]
        await http.post("/key/delete", headers=master, json={"key_aliases": aliases})

        async def new(alias: str, **scope) -> str:
            r = await http.post(
                "/key/generate", headers=master, json={"key_alias": alias, **scope}
            )
            r.raise_for_status()
            return r.json()["key"]

        async def search(key: str) -> httpx.Response:
            return await http.post(
                "/v1/search/web",
                headers={"Authorization": f"Bearer {key}"},
                json={"query": "postgres", "max_results": 1},
            )

        try:
            print("1. models=['embed']: which calls pass")
            key = await new("probe-models", models=["embed"])
            h = {"Authorization": f"Bearer {key}", "x-litellm-end-user-id": END_USER}
            calls = {
                "embeddings embed": (
                    "/v1/embeddings",
                    {"model": "embed", "input": ["hi"]},
                ),
                "embeddings embed-local": (
                    "/v1/embeddings",
                    {"model": "embed-local", "input": ["hi"]},
                ),
                "chat completions": (
                    "/v1/chat/completions",
                    {"model": "chat", "messages": [{"role": "user", "content": "hi"}]},
                ),
                "responses": ("/v1/responses", {"model": "chat", "input": "hi"}),
                "images": (
                    "/v1/images/generations",
                    {"model": "image", "prompt": "a dot"},
                ),
                "search tool web": (
                    "/v1/search/web",
                    {"query": "postgres", "max_results": 1},
                ),
                "key/generate": ("/key/generate", {}),
            }
            for name, (path, body) in calls.items():
                print(
                    f"   {name}: {brief(await http.post(path, headers=h, json=body))}"
                )
            print(f"   spend/logs: {brief(await http.get('/spend/logs', headers=h))}")

            print("2. search tools are an allow-list (object_permission.search_tools)")
            none = await new(
                "probe-no-search",
                models=["embed"],
                object_permission={"search_tools": ["none"]},
            )
            print(f"   ['none'] at creation: search {brief(await search(none))}")
            empty = await new(
                "probe-empty", models=["embed"], object_permission={"search_tools": []}
            )
            print(f"   [] at creation: search {brief(await search(empty))}")
            r = await http.post(
                "/key/update",
                headers=master,
                json={"key": empty, "object_permission": {"search_tools": ["none"]}},
            )
            print(
                f"   then updated to ['none'] ({r.status_code}): search {brief(await search(empty))}"
            )
            later = await new("probe-later", models=["embed"])
            print(f"   none at creation, used: search {brief(await search(later))}")
            r = await http.post(
                "/key/update",
                headers=master,
                json={"key": later, "object_permission": {"search_tools": ["none"]}},
            )
            print(
                f"   then updated to ['none'] ({r.status_code}): search {brief(await search(later))}"
            )
        finally:
            await http.post(
                "/key/delete", headers=master, json={"key_aliases": aliases}
            )
            await http.post(
                "/customer/delete", headers=master, json={"user_ids": [END_USER]}
            )


asyncio.run(main())
