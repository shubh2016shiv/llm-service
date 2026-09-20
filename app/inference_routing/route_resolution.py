"""Apply routing policy to one authorization-approved entitlement.

Architecture:
    Authorized API request
             |
             v
    InferenceRouteResolver -> config reader (PostgreSQL)
             |             -> provider catalog (startup-validated YAML)
             v
       route_builder -> ResolvedRoute

Authorization answers *may this user use this exact grant?* Routing answers
*can that grant execute this operation now?* Keeping those jobs separate is
important: this resolver never searches for or substitutes another grant.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.exceptions import (
    ConfigurationError,
    ModelNotSupportedError,
    TenantNotFoundError,
    TenantSuspendedError,
)
from app.core.settings.models.model_config import ModelCapability
from app.inference_routing.exceptions import (
    AuthorizedEntitlementUnavailableError,
    OperationNotSupportedError,
    ProviderNotAllowedError,
)
from app.inference_routing.route_builder import build_entitlement_route

if TYPE_CHECKING:
    from uuid import UUID

    from app.core.settings.models.model_config import LLMModelSpec
    from app.core.settings.models.provider_config import ProviderStaticConfig
    from app.core.settings.models.tenant_config import TenantConfig, UserEntitlementConfig
    from app.inference_routing.contracts import (
        InferenceRoutingConfigReader,
        ProviderConfigCatalog,
    )
    from app.inference_routing.models import ResolutionRequest, ResolvedRoute
    from app.schemas.enums import OperationType


class InferenceRouteResolver:
    """Validate an approved grant and produce one execution-ready route."""

    def __init__(
        self,
        routing_config_reader: InferenceRoutingConfigReader,
        config_loader: ProviderConfigCatalog,
    ) -> None:
        """Store the two read-only boundaries used during resolution."""
        self._routing_config_reader = routing_config_reader
        self._provider_catalog = config_loader

    async def resolve_route(self, request: ResolutionRequest) -> ResolvedRoute:
        """Resolve an exact authorized grant, failing closed on any drift."""
        tenant = await self._read_active_tenant(request.tenant_id)
        entitlement = await self._read_authorized_entitlement(request)
        self._require_provider_allowed(tenant, entitlement.provider_name)
        provider, model = self._resolve_provider_model(
            entitlement.provider_name,
            entitlement.model_name,
            request.operation,
        )
        return build_entitlement_route(entitlement, provider, model, request.deployment_key)

    async def _read_active_tenant(self, tenant_id: UUID) -> TenantConfig:
        """Read current tenant policy and reject missing or suspended tenants."""
        tenant = await self._routing_config_reader.read_tenant_config(tenant_id)
        if tenant is None:
            raise TenantNotFoundError(str(tenant_id))
        if not tenant.is_active:
            raise TenantSuspendedError(str(tenant.tenant_id), tenant.status.value)
        return tenant

    async def _read_authorized_entitlement(
        self,
        request: ResolutionRequest,
    ) -> UserEntitlementConfig:
        """Re-read the approved grant so revocation takes effect immediately."""
        entitlement = await self._routing_config_reader.read_entitlement_config(request)
        if entitlement is None or not entitlement.is_active:
            raise AuthorizedEntitlementUnavailableError(str(request.entitlement_id))
        if entitlement.entitlement_id != request.entitlement_id:
            raise ConfigurationError(
                "Routing reader returned an entitlement other than the authorized record."
            )
        return entitlement

    @staticmethod
    def _require_provider_allowed(tenant: TenantConfig, provider_name: str) -> None:
        """Enforce the tenant allow-list against the selected entitlement."""
        if not tenant.allows_provider(provider_name):
            raise ProviderNotAllowedError(str(tenant.tenant_id), provider_name)

    def _resolve_provider_model(
        self,
        provider_name: str,
        model_name: str,
        operation: OperationType,
    ) -> tuple[ProviderStaticConfig, LLMModelSpec]:
        """Require a known provider/model with the requested capability."""
        try:
            provider = self._provider_catalog.load_provider_config(provider_name)
        except KeyError as error:
            raise ConfigurationError(
                f"Provider config not loaded for provider {provider_name!r}."
            ) from error
        model = provider.get_model_spec(model_name)
        if model is None:
            raise ModelNotSupportedError(provider_name, model_name)
        if not model.supports(ModelCapability(operation.value)):
            raise OperationNotSupportedError(provider_name, model_name, operation.value)
        return provider, model
