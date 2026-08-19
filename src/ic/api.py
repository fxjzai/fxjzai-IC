from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
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
    app.mount("/metrics", make_asgi_app())

    @app.exception_handler(KeyError)
    async def key_error_handler(_request: object, exc: KeyError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc).strip("'")})

    @app.get("/health")
    def health() -> dict[str, str]:
        with resolved_database.session() as session:
            session.execute(text("SELECT 1"))
        return {"status": "healthy", "version": __version__}

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
