"""
Settings Models Package
=======================

Immutable Pydantic models representing all typed configuration contracts.

Why frozen models:
    These objects are shared across requests and components. Immutability
    prevents accidental runtime mutation and makes behavior deterministic.

Model grouping by concern:
    - ``global_config``: service-level defaults (logging, retry, HTTP pool).
    - ``provider_config``: static provider metadata from YAML.
    - ``model_config``: per-model capability, limits, and pricing metadata.
    - ``cloud_config``: cloud-vendor transport defaults.
    - ``tenant_config``: runtime tenant/deployment settings from persistence.

Author: Shubham Singh
"""

from __future__ import annotations

from app.core.settings.models.cloud_config import (
    AnyCloudConfig,
    AWSCloudConfig,
    AzureCloudConfig,
    CloudVendor,
    GCPCloudConfig,
)
from app.core.settings.models.global_config import (
    GlobalConfig,
    HTTPPoolConfig,
    LoggingConfig,
    RetryConfig,
    ServiceConfig,
)
from app.core.settings.models.model_config import LLMModelSpec, ModelCapability
from app.core.settings.models.provider_config import (
    AuthMode,
    ProviderAuthConfig,
    ProviderEndpointConfig,
    ProviderStaticConfig,
    ProviderType,
)
from app.core.settings.models.tenant_config import (
    DeploymentConfig,
    DeploymentStatus,
    TenantConfig,
    TenantRateLimits,
    TenantStatus,
    TenantTier,
    UserEntitlementConfig,
)

__all__: list[str] = [
    "AWSCloudConfig",
    "AnyCloudConfig",
    "AuthMode",
    "AzureCloudConfig",
    "CloudVendor",
    "DeploymentConfig",
    "DeploymentStatus",
    "GCPCloudConfig",
    "GlobalConfig",
    "HTTPPoolConfig",
    "LLMModelSpec",
    "LoggingConfig",
    "ModelCapability",
    "ProviderAuthConfig",
    "ProviderEndpointConfig",
    "ProviderStaticConfig",
    "ProviderType",
    "RetryConfig",
    "ServiceConfig",
    "TenantConfig",
    "TenantRateLimits",
    "TenantStatus",
    "TenantTier",
    "UserEntitlementConfig",
]
