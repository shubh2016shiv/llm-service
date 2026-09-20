"""Narrow boundaries used by inference route resolution.

Architecture:
    API -> InferenceRouteResolver -> these Protocols -> adapters

Authorization has already selected one entitlement before this package runs.
The resolver therefore asks storage for that exact record; it never searches
for alternatives or silently changes the authorization decision.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from app.core.settings.models.provider_config import ProviderStaticConfig
    from app.core.settings.models.tenant_config import TenantConfig, UserEntitlementConfig
    from app.inference_routing.models import ResolutionRequest


class InferenceRoutingConfigReader(Protocol):
    """Read fresh tenant policy and the exact authorized entitlement."""

    async def read_tenant_config(self, tenant_id: UUID) -> TenantConfig | None:
        """Return current tenant policy, or ``None`` when absent."""
        ...

    async def read_entitlement_config(
        self,
        request: ResolutionRequest,
    ) -> UserEntitlementConfig | None:
        """Return the authorization-approved entitlement, or ``None`` when absent."""
        ...


class ProviderConfigCatalog(Protocol):
    """Supply the preloaded provider/model catalog used by routing policy."""

    def load_provider_config(self, provider_name: str) -> ProviderStaticConfig:
        """Return one provider configuration.

        Raises:
            KeyError: If the provider was not loaded during startup.
        """
        ...
