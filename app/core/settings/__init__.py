"""
Settings Package
================

Public API for configuration loading and configuration models.

Two configuration sources are intentionally separated:
    - Environment settings (secrets, connection URLs) from
      ``ApplicationSettings``.
    - YAML settings (provider/model/static defaults) from ``ConfigLoader``.

Step-by-step relation:
    1. Startup calls ``get_application_settings()`` to read env/.env values.
    2. Startup constructs ``ConfigLoader`` with ``config_dir`` + environment.
    3. Loader validates YAML into frozen model objects from ``models/``.
    4. Services consume typed config objects, not raw dicts.

Author: Shubham Singh
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
    "AnyCloudConfig",
    "ApplicationSettings",
    "AuthMode",
    "AzureCloudConfig",
    "CloudVendor",
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
