"""
app/database
------------
Public API of the database persistence layer.

Import from here, not from the individual modules, so internal reorganisation
doesn't ripple through callers.

    from app.database import (
        DatabaseSessionManager,
        UserPersistence,
        UserEntitlementPersistence,
        ProviderCatalogPersistence,
        ModelCatalogPersistence,
        TenantPersistence,
        TenantMembershipPersistence,
        TenantDeploymentPersistence,
    )
"""

from app.database.base import BasePersistence
from app.database.model_catalog import ModelCatalogPersistence
from app.database.provider_catalog import ProviderCatalogPersistence
from app.database.session import DatabaseSessionManager
from app.database.tenant_deployments import TenantDeploymentPersistence
from app.database.tenant_memberships import TenantMembershipPersistence
from app.database.tenants import TenantPersistence
from app.database.user_entitlements import UserEntitlementPersistence
from app.database.users import UserPersistence

__all__ = [
    "BasePersistence",
    "DatabaseSessionManager",
    "ModelCatalogPersistence",
    "ProviderCatalogPersistence",
    "TenantDeploymentPersistence",
    "TenantMembershipPersistence",
    "TenantPersistence",
    "UserEntitlementPersistence",
    "UserPersistence",
]
