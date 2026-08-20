from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from prometheus_client import make_asgi_app
from pydantic import BaseModel
from sqlalchemy import text

from ic import __version__
from ic.db import Database
from ic.feedback import Feedback, FeedbackRequest
from ic.read import ReadIntelligence
from ic.select import Select, SelectionScope
from ic.settings import Settings


class DigestRequest(BaseModel):
    snapshot_id: str
    policy_version: str


PACKAGE_DIRECTORY = Path(__file__).parent


def create_app(
    *,
    database: Database | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings()
    resolved_database = database or Database(resolved_settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if resolved_database.is_test:
            resolved_database.create_schema()
        resolved_database.seed_defaults()
        app.state.select = Select(resolved_database)
        app.state.read = ReadIntelligence(resolved_database)
        app.state.feedback = Feedback(resolved_database)
        yield

    app = FastAPI(
        title="反向加载 IC",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )
    app.mount("/static", StaticFiles(directory=PACKAGE_DIRECTORY / "static"), name="static")
    app.mount("/metrics", make_asgi_app())
    templates = Jinja2Templates(directory=PACKAGE_DIRECTORY / "templates")

    @app.exception_handler(KeyError)
    async def key_error_handler(_request: object, exc: KeyError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc).strip("'")})

    @app.exception_handler(ValueError)
    async def value_error_handler(_request: object, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict[str, str]:
        with resolved_database.session() as session:
            session.execute(text("SELECT 1"))
        return {"status": "healthy", "version": __version__}

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> Any:
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={"dashboard": app.state.read.get_dashboard()},
        )

    @app.get("/items/{item_id}", response_class=HTMLResponse)
    def item_page(request: Request, item_id: str) -> Any:
        return templates.TemplateResponse(
            request=request,
            name="item.html",
            context={"item": app.state.read.get_item(item_id)},
        )

    @app.get("/digests/{digest_id}", response_class=HTMLResponse)
    def digest_page(request: Request, digest_id: str) -> Any:
        return templates.TemplateResponse(
            request=request,
            name="digest.html",
            context={"digest": app.state.read.get_digest_page(digest_id)},
        )

    @app.post("/v1/snapshots")
    def create_snapshot(scope: SelectionScope) -> Any:
        return app.state.select.create_candidate_snapshot(scope)

    @app.post("/v1/digests")
    def create_digest(request: DigestRequest) -> Any:
        return app.state.select.select(request.snapshot_id, request.policy_version)

    @app.get("/v1/items/{item_id}")
    def get_item(item_id: str) -> Any:
        return app.state.read.get_item(item_id)

    @app.get("/v1/digests/{digest_id}")
    def get_digest(digest_id: str) -> Any:
        return app.state.read.get_digest(digest_id)

    @app.post("/v1/feedback")
    def record_feedback(request: FeedbackRequest) -> Any:
        return app.state.feedback.record_feedback(request)

    return app


app = create_app()
