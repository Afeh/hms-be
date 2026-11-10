"""One router for `/api/v1`: add each route module here."""

from __future__ import annotations

from app.api.routing import ApiRouter
from app.api.v1.routes.health import router as health_router

api_router = ApiRouter()
api_router.include_router(health_router)
