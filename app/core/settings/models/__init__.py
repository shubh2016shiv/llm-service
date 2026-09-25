"""
Settings Models Package
=======================

Typed Pydantic models representing all configuration contracts.

YAML configuration models are frozen. Environment settings are composed into
``ApplicationSettings`` and follow Pydantic Settings' standard lifecycle.

Model grouping by concern:
    - ``global_config``: service-level defaults (logging, retry, HTTP pool).
    - ``provider_config``: static provider metadata from YAML.
    - ``model_config``: per-model capability, limits, and pricing metadata.
    - ``cloud_config``: cloud-vendor transport defaults.
    - ``tenant_config``: runtime tenant/deployment settings from persistence.
    - ``*_config``: focused environment-backed configuration groups.

Author: Shubham Singh
"""

from __future__ import annotations

from app.core.settings.models.auth_session_config import AuthSessionConfig
from app.core.settings.models.circuit_breaker_config import (
    CircuitBreakerPolicyConfig,
    ProviderCircuitBreakerConfig,
)
from app.core.settings.models.cloud_config import (
    AnyCloudConfig,
    AWSCloudConfig,
    AzureCloudConfig,
    CloudVendor,
    GCPCloudConfig,
)
from app.core.settings.models.environment_config import DeploymentEnvironment, EnvironmentConfig
from app.core.settings.models.global_config import (
    GlobalConfig,
    HTTPPoolConfig,
    LoggingConfig,
    RetryConfig,
    ServiceConfig,
)
from app.core.settings.models.infrastructure_config import (
    CacheConfig,
    DatabaseConfig,
    StreamingConfig,
    TokenManagerConfig,
)
from app.core.settings.models.model_config import LLMModelSpec, ModelCapability
from app.core.settings.models.observability_config import ObservabilityConfig
from app.core.settings.models.provider_config import (
    AuthMode,
    ProviderAuthConfig,
    ProviderEndpointConfig,
    ProviderStaticConfig,
    ProviderType,
)
from app.core.settings.models.security_config import SecurityConfig
from app.core.settings.models.tenant_config import (
    DeploymentConfig,
    TenantConfig,
    TenantRateLimits,
    UserEntitlementConfig,
)
from app.core.settings.models.vault_config import VaultConfig

__all__: list[str] = [
    "AWSCloudConfig",
    "AnyCloudConfig",
    "AuthMode",
    "AuthSessionConfig",
    "AzureCloudConfig",
    "CacheConfig",
    "CircuitBreakerPolicyConfig",
    "CloudVendor",
    "DatabaseConfig",
    "DeploymentConfig",
    "DeploymentEnvironment",
    "EnvironmentConfig",
    "GCPCloudConfig",
    "GlobalConfig",
    "HTTPPoolConfig",
    "LLMModelSpec",
    "LoggingConfig",
    "ModelCapability",
    "ObservabilityConfig",
    "ProviderAuthConfig",
    "ProviderCircuitBreakerConfig",
    "ProviderEndpointConfig",
    "ProviderStaticConfig",
    "ProviderType",
    "RetryConfig",
    "SecurityConfig",
    "ServiceConfig",
    "StreamingConfig",
    "TenantConfig",
    "TenantRateLimits",
    "TokenManagerConfig",
    "UserEntitlementConfig",
    "VaultConfig",
]
