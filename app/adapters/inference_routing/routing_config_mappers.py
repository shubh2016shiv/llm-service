"""Convert untrusted PostgreSQL projections into validated routing models.

Architecture:
    PostgreSQL row -> mapper -> frozen Pydantic routing model

Database drivers return dictionary-like rows whose values have no useful
static type. Every value crosses a Pydantic validation boundary here before it
can influence route selection. Keeping these conversions together makes schema
drift easy to find and test.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.settings.models.tenant_config import (
    DeploymentConfig,
    TenantConfig,
    TenantRateLimits,
    UserEntitlementConfig,
)
from app.schemas.enums import (
    TenantDeploymentStatus,
    TenantLifecycleStatus,
    TenantSubscriptionTier,
    UserEntitlementStatus,
)

_DEFAULT_TEMPERATURE: float = DeploymentConfig.model_fields["default_temperature"].default


def convert_tenant_row(row: dict[str, Any]) -> TenantConfig:
    """Validate one tenant SQL projection and return the routing model."""
    raw_provider_names = row.get("allowed_provider_names")
    allowed_provider_names = frozenset(raw_provider_names) if raw_provider_names else None
    return TenantConfig(
        tenant_id=UUID(str(row["tenant_id"])),
        tenant_name=str(row["tenant_name"]),
        tenant_slug=str(row["tenant_slug"]),
        status=TenantLifecycleStatus(str(row["status"])),
        tier=TenantSubscriptionTier(str(row["tier"])),
        rate_limits=TenantRateLimits(
            rpm=int(row["rate_limit_requests_per_minute"]),
            tpm=int(row["rate_limit_tokens_per_minute"]),
            concurrent_requests=int(row["rate_limit_concurrent_requests"]),
        ),
        allowed_provider_names=allowed_provider_names,
    )


def convert_deployment_row(row: dict[str, Any]) -> DeploymentConfig:
    """Validate one deployment SQL projection and return the routing model."""
    raw_temperature = row.get("default_temperature")
    return DeploymentConfig(
        deployment_id=UUID(str(row["deployment_id"])),
        tenant_id=UUID(str(row["tenant_id"])),
        deployment_key=str(row["deployment_key"]),
        deployment_name=str(row["deployment_name"]),
        status=TenantDeploymentStatus(str(row["status"])),
        provider_name=str(row["provider_name"]),
        model_name=str(row["model_name"]),
        api_endpoint_url=str(row["api_endpoint_url"]),
        secret_reference=str(row["secret_reference"]),
        cloud_region=row.get("cloud_region"),
        timeout_seconds=row.get("timeout_seconds"),
        max_retries=row.get("max_retries"),
        default_temperature=(
            float(raw_temperature) if raw_temperature is not None else _DEFAULT_TEMPERATURE
        ),
        default_max_tokens=row.get("default_max_output_tokens"),
        extra_headers=dict(row.get("extra_headers") or {}),
        extra_config=dict(row.get("extra_config") or {}),
        is_default=bool(row.get("is_default", False)),
        priority=int(row.get("routing_priority", 0)),
    )


def convert_entitlement_row(row: dict[str, Any]) -> UserEntitlementConfig:
    """Validate one entitlement SQL projection and return the routing model."""
    return UserEntitlementConfig(
        entitlement_id=UUID(str(row["entitlement_id"])),
        user_id=UUID(str(row["user_id"])),
        tenant_id=UUID(str(row["tenant_id"])),
        entitlement_name=str(row["entitlement_name"]),
        provider_name=str(row["provider_name"]),
        model_name=str(row["model_name"]),
        api_endpoint_url=str(row["api_endpoint_url"]),
        secret_reference=str(row["secret_reference"]),
        cloud_provider=row.get("cloud_provider"),
        cloud_region=row.get("cloud_region"),
        extra_config=dict(row.get("extra_config") or {}),
        is_active=UserEntitlementStatus(str(row["status"])) == UserEntitlementStatus.ACTIVE,
    )
