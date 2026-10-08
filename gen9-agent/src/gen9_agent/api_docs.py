"""The API's docs page: Swagger UI from files checked in (docs_assets/README.md), so no script from
another site runs on the API's origin, where a person pastes a token into "Authorize". FastAPI's
own /docs and /redoc load theirs from a CDN, any 5.x or 2.x (docs/plans/manual-e2e.md, P8-M1)."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

ASSETS = Path(__file__).parent / "docs_assets"


def mount_docs(app: FastAPI) -> None:
    """`/docs` and the files it loads, `/docs/assets/…`; the app is made with docs_url=None."""
    app.mount("/docs/assets", StaticFiles(directory=ASSETS), name="docs-assets")

    @app.get("/docs", include_in_schema=False)
    async def docs() -> HTMLResponse:
        return get_swagger_ui_html(
            openapi_url=app.openapi_url or "/openapi.json",
            title=f"{app.title} – docs",
            swagger_js_url="/docs/assets/swagger-ui-bundle.js",
            swagger_css_url="/docs/assets/swagger-ui.css",
            swagger_favicon_url="data:,",
            # Swagger UI would send the API's schema to validator.swagger.io for its badge
            swagger_ui_parameters={"validatorUrl": None},
        )
