"""Reusable routing fakes for resolver behavior tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.inference_routing.models import ResolutionRequest
from app.inference_routing.route_resolution import InferenceRouteResolver
from app.schemas.enums import OperationType
from tests.unit.inference_routing.conftest import (
    DEPLOYMENT_KEY,
    ENTITLEMENT_ID,
    PROVIDER_NAME,
    TENANT_ID,
    USER_ID,
    FakeConfigLoader,
    build_provider_static_config,
)

if TYPE_CHECKING:
    from uuid import UUID

    from app.core.settings.models.tenant_config import (
        TenantConfig,
        UserEntitlementConfig,
    )


class FakeInferenceRoutingConfigReader:
    """Return controlled routing records without database or cache access."""

    def __init__(
        self,
        tenant: TenantConfig | None,
        entitlement: UserEntitlementConfig | None = None,
    ) -> None:
        self.tenant = tenant
        self.entitlement = entitlement

    async def read_tenant_config(self, tenant_id: UUID) -> TenantConfig | None:
        """Return the configured tenant."""
        return self.tenant

    async def read_entitlement_config(
        self,
        request: ResolutionRequest,
    ) -> UserEntitlementConfig | None:
        """Return the configured authorization-approved entitlement."""
        return self.entitlement


def build_resolution_request(operation: OperationType = OperationType.CHAT) -> ResolutionRequest:
    """Build a valid route resolution request."""
    return ResolutionRequest(
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        deployment_key=DEPLOYMENT_KEY,
        operation=operation,
        entitlement_id=ENTITLEMENT_ID,
    )


def build_route_resolver(
    reader: FakeInferenceRoutingConfigReader,
    config_loader: FakeConfigLoader | None = None,
) -> InferenceRouteResolver:
    """Build a resolver with an in-memory provider catalog."""
    loader = config_loader or FakeConfigLoader({PROVIDER_NAME: build_provider_static_config()})
    return InferenceRouteResolver(reader, loader)
