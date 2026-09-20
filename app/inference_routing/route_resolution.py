"""
Route resolution — the decision itself
=======================================

What this file is for
---------------------
One class (InferenceRouteResolver) with one public method
(resolve_route). It turns a ResolutionRequest into a ResolvedRoute by
answering, in this exact order:

    1. Is the tenant real and active?             (else: not found /
                                                    suspended)
    2. Which route wins: the user's personal key  (entitlement first),
       or the tenant's shared deployment?          (deployment second)
    3. Does the tenant's allow-list permit that   (else: provider not
       provider?                                   allowed)
    4. Does the provider catalog know that model, (else: model not
       and can that model do this operation?       supported / operation
                                                   not supported)
    5. Build the final route with every "which    (the effective settings)
       setting wins?" question answered.

The setting cascade (which value wins)
--------------------------------------
For a TENANT DEPLOYMENT route (shared key):
    timeout    -> the deployment's own setting if set, otherwise the
                  provider's default.
    max tokens -> the deployment's own setting if set, otherwise the
                  model's maximum.

For a PERSONAL ENTITLEMENT route (user's own key):
    the provider/model defaults are used as-is, and usage is counted
    against the entitlement's own id (this user pays for this usage).

Why a fingerprint
-----------------
Each built route gets a fixed identity string (a hash of its contents).
Two identical routes produce the same fingerprint; any difference in the
route produces a different one. Downstream code can use it for cache
keys and deduplication without comparing whole route objects.

Author: Shubham Singh
"""

# This line makes every type hint below a lazy string. (Boilerplate.)
from __future__ import annotations

# hashlib = the SHA-256 hash used for route fingerprints.
import hashlib

# json = serialize a route into stable text before hashing it.
import json

# TYPE_CHECKING is only True while a type checker reads the file, never at
# runtime — imports under it exist purely for type hints.
from typing import TYPE_CHECKING

# The typed "no" answers for gates that fail (shared with other layers).
from app.core.exceptions import (
    ConfigurationError,
    DeploymentInactiveError,
    DeploymentNotFoundError,
    ModelNotSupportedError,
    TenantNotFoundError,
    TenantSuspendedError,
)

# ModelCapability = the "what can a model do" vocabulary (chat/embed/...).
from app.core.settings.models.model_config import ModelCapability

# The two route kinds this resolver picks between.
from app.core.settings.models.tenant_config import (
    DeploymentConfig,
    UserEntitlementConfig,
)

# This package's own "no" answers (the error vocabulary in exceptions.py).
from app.inference_routing.exceptions import (
    AmbiguousUserEntitlementError,
    OperationNotSupportedError,
    ProviderNotAllowedError,
)

# The request and answer shapes.
from app.inference_routing.models import (
    ResolutionRequest,
    ResolvedRoute,
)

# Names used only in type hints, so they are imported only for the checker.
if TYPE_CHECKING:
    from uuid import UUID

    from app.core.settings.loader import ConfigLoader
    from app.core.settings.models.model_config import LLMModelSpec
    from app.core.settings.models.provider_config import ProviderStaticConfig
    from app.core.settings.models.tenant_config import TenantConfig
    from app.inference_routing.contracts import InferenceRoutingConfigReader
    from app.schemas.enums import OperationType

# A picked route is either the user's personal key or the tenant's shared
# deployment — this alias names that union.
type RouteConfig = DeploymentConfig | UserEntitlementConfig


class InferenceRouteResolver:
    """The decider: one request in, one complete route out.

    It owns no data and no SQL — it asks the reader for configuration and
    the catalog loader for provider/model facts, then applies the
    decision rules in the module docstring above.
    """

    def __init__(
        self,
        routing_config_reader: InferenceRoutingConfigReader,
        config_loader: ConfigLoader,
    ) -> None:
        self.routing_config_reader = routing_config_reader  # the three storage questions
        self.config_loader = config_loader  # the provider/model catalog

    async def resolve_route(self, request: ResolutionRequest) -> ResolvedRoute:
        """Turn one request into one complete route, or raise a typed "no".

        Args:
            request: The already-authorized question (who, which tenant,
                which deployment key, what operation).

        Returns:
            The fully built route.

        Raises:
            One of the typed errors per failed gate — see each step's
            comment below.
        """
        # Gate 1: the tenant must exist and be open for business.
        tenant_config = await self._read_active_tenant(request.tenant_id)
        # Gate 2: pick the winner — personal key first, shared deployment
        # second.
        selected_route = await self._read_preferred_route(request, tenant_config)
        # Gate 3: is this tenant even allowed to use that provider?
        if not tenant_config.allows_provider(selected_route.provider_name):
            raise ProviderNotAllowedError(
                str(tenant_config.tenant_id), selected_route.provider_name
            )
        # Gate 4: does the provider catalog know the model, and can the
        # model perform this operation?
        provider_config, model_spec = self._resolve_provider_model(
            selected_route.provider_name,
            selected_route.model_name,
            request.operation,
        )
        # All gates open. Build the route — the two route kinds have
        # slightly different setting cascades, so two builders exist.
        if isinstance(selected_route, UserEntitlementConfig):
            return _build_entitlement_route(
                selected_route, provider_config, model_spec, request.deployment_key
            )
        return _build_deployment_route(selected_route, provider_config, model_spec)

    async def _read_active_tenant(self, tenant_id: UUID) -> TenantConfig:
        """Gate 1: fetch the tenant and require it to be open for business.

        Kept private on purpose: the public API is just resolve_route.
        """
        tenant_config = await self.routing_config_reader.read_tenant_config(tenant_id)
        if tenant_config is None:
            # No such tenant at all.
            raise TenantNotFoundError(str(tenant_id))
        if not tenant_config.is_active:
            # Exists but suspended/expired — refuse, naming the actual
            # status in the message.
            raise TenantSuspendedError(str(tenant_config.tenant_id), tenant_config.status.value)
        return tenant_config

    async def _read_preferred_route(
        self,
        request: ResolutionRequest,
        tenant_config: TenantConfig,
    ) -> RouteConfig:
        """Gate 2: personal key first, shared deployment second.

        Rules in plain words:
          - exactly one active personal key -> use it,
          - more than one                      -> refuse (ambiguous — we
                                                 will not guess which key
                                                 the caller meant),
          - none                               -> fall back to the
                                                 deployment.
        """
        # Fetch the user's personal-key records for this route.
        entitlements = await self.routing_config_reader.find_user_entitlements(request)
        # Keep only the still-active ones (revoked/suspended keys count
        # as "not there").
        active_entitlements = [item for item in entitlements if item.is_active]
        if len(active_entitlements) > 1:
            # Two or more matching keys: ambiguity is a caller-side data
            # problem, so refuse loudly instead of silently guessing.
            raise AmbiguousUserEntitlementError(
                str(tenant_config.tenant_id), str(request.user_id), request.deployment_key
            )
        if active_entitlements:
            # Exactly one personal key -> it wins.
            return active_entitlements[0]
        # No personal key: fall back to the tenant's shared deployment.
        deployment = await self.routing_config_reader.read_deployment_config(
            request.tenant_id, request.deployment_key
        )
        if deployment is None:
            raise DeploymentNotFoundError(str(request.tenant_id), request.deployment_key)
        if not deployment.is_active:
            raise DeploymentInactiveError(deployment.deployment_key, deployment.status.value)
        return deployment

    def _resolve_provider_model(
        self,
        provider_name: str,
        model_name: str,
        operation: OperationType,
    ) -> tuple[ProviderStaticConfig, LLMModelSpec]:
        """Gate 4: prove the provider knows the model and it can do this.

        Returns both catalog objects on success, so the builder can read
        the provider/model defaults without a second lookup.
        """
        try:
            # Read the provider's catalog entry (YAML-backed).
            provider_config = self.config_loader.load_provider_config(provider_name)
        except FileNotFoundError as error:
            # No catalog file for this provider at all.
            raise ConfigurationError(
                f"Provider config not found for provider {provider_name!r}."
            ) from error
        # Does the catalog list this model?
        model_spec = provider_config.get_model_spec(model_name)
        if model_spec is None:
            raise ModelNotSupportedError(provider_name, model_name)
        # Can this model perform this operation (chat/embed/rerank)?
        capability = ModelCapability(operation.value)
        if not model_spec.supports(capability):
            raise OperationNotSupportedError(provider_name, model_name, operation.value)
        return provider_config, model_spec


def _build_deployment_route(
    deployment: DeploymentConfig,
    provider_config: ProviderStaticConfig,
    model_spec: LLMModelSpec,
) -> ResolvedRoute:
    """Build the final route for a TENANT DEPLOYMENT (shared key).

    Setting cascade: the deployment's own overrides win where set,
    otherwise the provider/model defaults fill in. (The "or" below
    relies on the models' rule that these overrides are either None or
    a positive number — a 0 can never appear.)
    """
    # Timeout: the deployment's own setting, else the provider's default.
    timeout_seconds = deployment.timeout_seconds or provider_config.default_timeout_seconds
    # Answer length: the deployment's own setting, else the model's max.
    max_tokens = deployment.default_max_tokens or model_spec.max_output_tokens
    # Fixed identity of this exact route (see the module docstring).
    fingerprint = _route_fingerprint(
        "tenant_deployment",
        str(deployment.tenant_id),
        deployment,
    )
    return ResolvedRoute(
        tenant_id=deployment.tenant_id,
        deployment_key=deployment.deployment_key,
        provider_static_config=provider_config,
        provider_name=deployment.provider_name,
        model_name=deployment.model_name,
        api_endpoint_url=deployment.api_endpoint_url,
        cloud_region=deployment.cloud_region,
        secret_reference=deployment.secret_reference,
        effective_timeout_seconds=timeout_seconds,
        effective_temperature=deployment.default_temperature,
        effective_max_tokens=max_tokens,
        extra_headers=deployment.extra_headers,
        extra_config=deployment.extra_config,
        # Usage is counted against the deployment key.
        quota_key=deployment.deployment_key,
        route_fingerprint=fingerprint,
    )


def _build_entitlement_route(
    entitlement: UserEntitlementConfig,
    provider_config: ProviderStaticConfig,
    model_spec: LLMModelSpec,
    deployment_key: str,
) -> ResolvedRoute:
    """Build the final route for a PERSONAL KEY (user entitlement).

    Personal keys carry no per-route overrides, so the provider/model
    defaults are used as-is, and usage is counted against the
    entitlement's own id (this user pays for this usage).
    """
    fingerprint = _route_fingerprint(
        "user_entitlement",
        str(entitlement.tenant_id),
        entitlement,
    )
    return ResolvedRoute(
        tenant_id=entitlement.tenant_id,
        deployment_key=deployment_key,
        provider_static_config=provider_config,
        provider_name=entitlement.provider_name,
        model_name=entitlement.model_name,
        api_endpoint_url=entitlement.api_endpoint_url,
        cloud_region=entitlement.cloud_region,
        secret_reference=entitlement.secret_reference,
        effective_timeout_seconds=provider_config.default_timeout_seconds,
        effective_temperature=provider_config.default_temperature,
        effective_max_tokens=model_spec.max_output_tokens,
        extra_config=entitlement.extra_config,
        quota_key=str(entitlement.entitlement_id),
        route_fingerprint=fingerprint,
    )


def _route_fingerprint(
    source: str,
    tenant_id: str,
    selected_route: RouteConfig,
) -> str:
    """Return a fixed identity string for one route, hiding the inputs.

    How: turn the route into sorted JSON, hash it (SHA-256), and return
    the hex digest. Same route -> same fingerprint; any difference ->
    different fingerprint. The original data cannot be recovered from the
    hash, so the fingerprint is safe to store in logs and cache keys.
    """
    payload = (source, tenant_id, selected_route.model_dump(mode="json"))
    # sort_keys=True with fixed separators makes the JSON text identical
    # no matter what order the dicts were created in.
    normalized_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized_payload.encode("utf-8")).hexdigest()
