"""Public exception facade organized by common error theme.

Architecture:
    provider | tenant | quota | deployment | management | configuration
                                 |
                                 v
                           LLMServiceError

Import exception types from this package to keep application imports stable
while definitions remain separated by operational ownership.
"""

from __future__ import annotations

from app.core.exceptions.application_configuration import ConfigurationError
from app.core.exceptions.authorization import AuthorizationGrantCacheUnavailableError
from app.core.exceptions.base import LLMServiceError
from app.core.exceptions.llm_provider import (
    AuthenticationError,
    ExpiredTokenError,
    InvalidAPIKeyError,
    InvalidRequestError,
    ModelNotSupportedError,
    ProviderCircuitOpenError,
    ProviderError,
    ProviderInternalError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
    RateLimitError,
    RequestsPerMinuteExceededError,
    ServiceDownError,
    TokensPerMinuteExceededError,
)
from app.core.exceptions.management_api import (
    InvalidStateTransitionError,
    ManagementError,
    ManagementValidationError,
    ResourceConflictError,
    ResourceNotFoundError,
    TenantAccessDeniedError,
)
from app.core.exceptions.secret_backend import SecretBackendUnavailableError
from app.core.exceptions.streaming import StreamCapacityExceededError
from app.core.exceptions.tenant import TenantError, TenantNotFoundError, TenantSuspendedError
from app.core.exceptions.tenant_deployment import (
    DeploymentError,
    DeploymentInactiveError,
    DeploymentNotFoundError,
)
from app.core.exceptions.tenant_usage_limits import (
    ConcurrentRequestLimitError,
    QuotaError,
    QuotaExceededError,
)

__all__ = [
    "AuthenticationError",
    "AuthorizationGrantCacheUnavailableError",
    "ConcurrentRequestLimitError",
    "ConfigurationError",
    "DeploymentError",
    "DeploymentInactiveError",
    "DeploymentNotFoundError",
    "ExpiredTokenError",
    "InvalidAPIKeyError",
    "InvalidRequestError",
    "InvalidStateTransitionError",
    "LLMServiceError",
    "ManagementError",
    "ManagementValidationError",
    "ModelNotSupportedError",
    "ProviderCircuitOpenError",
    "ProviderError",
    "ProviderInternalError",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
    "ProviderValidationError",
    "QuotaError",
    "QuotaExceededError",
    "RateLimitError",
    "RequestsPerMinuteExceededError",
    "ResourceConflictError",
    "ResourceNotFoundError",
    "SecretBackendUnavailableError",
    "ServiceDownError",
    "StreamCapacityExceededError",
    "TenantAccessDeniedError",
    "TenantError",
    "TenantNotFoundError",
    "TenantSuspendedError",
    "TokensPerMinuteExceededError",
]
