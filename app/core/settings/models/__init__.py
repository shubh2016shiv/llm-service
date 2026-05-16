"""
Config Models Package — Public API for all configuration model classes.

Architecture:
-------------
    app.core.settings.models
    ├── global_config    → GlobalConfig, HTTPPoolConfig, RetryConfig, LoggingConfig
    ├── model_config     → LLMModelSpec, ModelCapability
    ├── provider_config  → ProviderStaticConfig, ProviderAuthConfig, AuthMode
    ├── cloud_config     → AWSCloudConfig, AzureCloudConfig, GCPCloudConfig
    └── tenant_config    → TenantConfig, DeploymentConfig, UserEntitlementConfig

Author: Engineering Team
Last Updated: 2026-05-16
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
    # cloud_config
    "AWSCloudConfig",
    "AnyCloudConfig",
    # provider_config
    "AuthMode",
    "AzureCloudConfig",
    "CloudVendor",
    # tenant_config
    "DeploymentConfig",
    "DeploymentStatus",
    "GCPCloudConfig",
    # global_config
    "GlobalConfig",
    "HTTPPoolConfig",
    # model_config
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
