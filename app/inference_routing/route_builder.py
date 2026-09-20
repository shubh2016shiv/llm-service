"""Build an immutable execution route from one verified entitlement.

Architecture:
    InferenceRouteResolver -> build_entitlement_route -> ResolvedRoute

The resolver owns policy gates; this module owns construction, defaults, and
the deterministic identity used by downstream provider caches.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from app.inference_routing.models import ResolvedRoute

if TYPE_CHECKING:
    from app.core.settings.models.model_config import LLMModelSpec
    from app.core.settings.models.provider_config import ProviderStaticConfig
    from app.core.settings.models.tenant_config import UserEntitlementConfig


def build_entitlement_route(
    entitlement: UserEntitlementConfig,
    provider_config: ProviderStaticConfig,
    model_spec: LLMModelSpec,
    deployment_key: str,
) -> ResolvedRoute:
    """Resolve catalog defaults into the final downstream contract."""
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
        route_fingerprint=_route_fingerprint(entitlement, deployment_key),
    )


def _route_fingerprint(
    entitlement: UserEntitlementConfig,
    deployment_key: str,
) -> str:
    """Return a stable SHA-256 identity for cache-safe route comparison."""
    payload = {
        "deployment_key": deployment_key,
        "entitlement": entitlement.model_dump(mode="json"),
    }
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
