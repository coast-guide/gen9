"""The API's /docs page takes nothing from another site: Swagger UI's files come from gen9-agent
itself, as they were checked in (gen9_agent/docs_assets/README.md; docs/plans/manual-e2e.md,
P8-M1)."""

import hashlib
import re

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from gen9_agent.api_docs import ASSETS, mount_docs

pytestmark = pytest.mark.asyncio

SUMS = {
    "swagger-ui-bundle.js": "62df541529080464a7660adc793eab7128c6193ce3be24ddc1e0e0a4a63edc2f",
    "swagger-ui.css": "1ac324f7dcd27e4b9386b4bd6421271ec147e922a22c05ba24b11515e9aa6321",
}


async def test_the_files_are_the_ones_checked_in() -> None:
    for name, digest in SUMS.items():
        assert hashlib.sha256((ASSETS / name).read_bytes()).hexdigest() == digest, name


async def test_the_page_loads_nothing_from_another_site() -> None:
    app = FastAPI(docs_url=None, redoc_url=None)
    mount_docs(app)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://api"
    ) as client:
        page = await client.get("/docs")
        assert page.status_code == 200
        loaded = re.findall(r'(?:src|href)="([^"]+)"', page.text)
        assert loaded, page.text
        assert all(
            not re.match(r"[a-z]+:", url) or url.startswith("data:") for url in loaded
        ), loaded
        for url in loaded:
            if url.startswith("/"):
                asset = await client.get(url)
                assert asset.status_code == 200, url
                assert asset.headers["content-type"].startswith(
                    ("text/javascript", "application/javascript", "text/css")
                ), url
