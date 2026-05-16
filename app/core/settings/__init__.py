"""
Config Package — Public API for the settings subsystem.

Architecture:
-------------
    app.core.settings
    ├── settings  → ApplicationSettings, get_application_settings()
    ├── loader    → ConfigLoader
    └── models/   → All frozen Pydantic settings models

Author: Engineering Team
Last Updated: 2026-05-16
"""

from __future__ import annotations

from app.core.settings.loader import ConfigLoader
from app.core.settings.models import (
    AnyCloudConfig,
    AuthMode,
    AWSCloudConfig,
    AzureCloudConfig,
    CloudVendor,
    DeploymentConfig,
    DeploymentStatus,
    GCPCloudConfig,
    GlobalConfig,
    HTTPPoolConfig,
    LLMModelSpec,
    LoggingConfig,
    ModelCapability,
    ProviderAuthConfig,
    ProviderEndpointConfig,
    ProviderStaticConfig,
    ProviderType,
    RetryConfig,
    ServiceConfig,
    TenantConfig,
    TenantRateLimits,
    TenantStatus,
    TenantTier,
    UserEntitlementConfig,
)
from app.core.settings.settings import ApplicationSettings, get_application_settings

__all__: list[str] = [
    "AWSCloudConfig",
    # models (re-exported for convenience)
    "AnyCloudConfig",
    # settings
    "ApplicationSettings",
    "AuthMode",
    "AzureCloudConfig",
    "CloudVendor",
    # loader
    "ConfigLoader",
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
    "get_application_settings",
]
