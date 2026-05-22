"""
Management Routers Package
==========================

This package groups all admin and operations endpoints (tenants, users,
deployments, providers/models, and entitlements).

Enterprise Pattern: Router Aggregation Pattern
    Each resource has its own router module, and this package-level file
    combines them into one ``router`` object for easy registration in ``app.api``.

How request flow works:
    app.main -> app.api.management_router -> specific resource router
        -> dependency-injected service -> persistence layer

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
