"""One router for `/api/v1`: add each route module here."""

from __future__ import annotations

from app.api.routing import ApiRouter
from app.api.v1.routes.auth import router as auth_router
from app.api.v1.routes.health import router as health_router
from app.api.v1.routes.patients import router as patients_router
from app.api.v1.routes.tenant import router as tenant_router
from app.api.v1.routes.tenant_config import router as tenant_config_router
from app.api.v1.routes.visits import router as visits_router
from app.api.v1.routes.vitals import router as vitals_router

api_router = ApiRouter()
api_router.include_router(auth_router)
api_router.include_router(health_router)
api_router.include_router(patients_router)
api_router.include_router(tenant_router)
api_router.include_router(tenant_config_router)
api_router.include_router(visits_router)
api_router.include_router(vitals_router)
