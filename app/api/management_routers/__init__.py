"""
Management Router Aggregation Package.

Architecture:
-------------
    ┌──────────────────────────────┐
    │ app.main / app.api package   │
    └──────────────┬───────────────┘
                   ▼
    ┌──────────────────────────────┐
    │ aggregated management router │
    │ (this package)               │
    └──────────────┬───────────────┘
                   ▼
    ┌──────────────────────────────┐
    │ resource routers             │
    │ tenant/user/deploy/catalog   │
    └──────────────┬───────────────┘
                   ▼
    ┌──────────────────────────────┐
    │ services + persistence        │
    └──────────────────────────────┘

Rationale:
    Keeping one resource per router module improves discoverability and reduces
    merge conflicts. This file acts as a composition point so the rest of the
    application can include one management router without knowing internal file
    layout.

Author: Shubham Singh
"""

from fastapi import APIRouter

from app.api.management_routers.catalog_router import router as catalog_router
from app.api.management_routers.deployment_router import router as deployment_router
from app.api.management_routers.entitlement_router import router as entitlement_router
from app.api.management_routers.tenant_router import router as tenant_router
from app.api.management_routers.user_router import router as user_router

router = APIRouter()
router.include_router(catalog_router)
router.include_router(deployment_router)
router.include_router(entitlement_router)
router.include_router(tenant_router)
router.include_router(user_router)

__all__ = ["router"]
