"""API process entry point: `uvicorn app.main:app`.

One ASGI app; the worker lives in `app.worker`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.db import TenantConnectionRegistry
from app.db.cache import create_redis

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.registry = TenantConnectionRegistry(
            settings.mongodb_uri,
            settings.mongodb_database,
            timeout_ms=settings.mongodb_server_selection_timeout_ms,
        )
        app.state.redis = create_redis(settings.redis_url)
        logger.info("api started", extra={"environment": settings.environment})
        try:
            yield
        finally:
            await app.state.redis.aclose()
            await app.state.registry.close()

    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description="HMS backend. Every response uses the four-key envelope.",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.add_middleware(RequestContextMiddleware)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()
