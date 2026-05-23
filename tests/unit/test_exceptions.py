"""Unit tests for app.core.exceptions — domain exception hierarchy.

Exception classes are pure data containers with no I/O dependencies.
Testing them validates that error codes, messages, and inheritance are
correct per the specification documented in the module docstring.
"""

from __future__ import annotations

import pytest

from app.core.exceptions import (
    AuthenticationError,
    ConcurrentRequestLimitError,
    ConfigurationError,
    DeploymentError,
    DeploymentInactiveError,
    DeploymentNotFoundError,
    ExpiredTokenError,
    InvalidAPIKeyError,
    InvalidRequestError,
    InvalidStateTransitionError,
    LLMServiceError,
    ManagementError,
    ManagementValidationError,
    ModelNotSupportedError,
    ProviderError,
    ProviderInternalError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
    QuotaError,
    QuotaExceededError,
    RateLimitError,
    RequestsPerMinuteExceededError,
    ResourceConflictError,
    ResourceNotFoundError,
    ServiceDownError,
    TenantAccessDeniedError,
    TenantError,
    TenantNotFoundError,
    TenantSuspendedError,
    TokensPerMinuteExceededError,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Hierarchy
# ═══════════════════════════════════════════════════════════════════════════════


class TestExceptionHierarchy:
    """All domain exceptions must inherit from LLMServiceError."""

    @pytest.mark.parametrize(
        "exception_class",
        [
            ProviderError,
            TenantError,
            QuotaError,
            DeploymentError,
            ConfigurationError,
            ManagementError,
        ],
    )
    def test_mid_level_exceptions_inherit_from_base(self, exception_class: type) -> None:
        """Every mid-level exception must be a subclass of LLMServiceError."""
        assert issubclass(exception_class, LLMServiceError)

    @pytest.mark.parametrize(
        "subclass,parent",
        [
            (AuthenticationError, ProviderError),
            (InvalidAPIKeyError, AuthenticationError),
            (ExpiredTokenError, AuthenticationError),
            (RateLimitError, ProviderError),
            (RequestsPerMinuteExceededError, RateLimitError),
            (TokensPerMinuteExceededError, RateLimitError),
            (ProviderValidationError, ProviderError),
            (InvalidRequestError, ProviderValidationError),
            (ModelNotSupportedError, ProviderValidationError),
            (ProviderUnavailableError, ProviderError),
            (ServiceDownError, ProviderUnavailableError),
            (ProviderTimeoutError, ProviderUnavailableError),
            (ProviderInternalError, ProviderError),
            (TenantNotFoundError, TenantError),
            (TenantSuspendedError, TenantError),
            (QuotaExceededError, QuotaError),
            (ConcurrentRequestLimitError, QuotaError),
            (DeploymentNotFoundError, DeploymentError),
            (DeploymentInactiveError, DeploymentError),
        ],
    )
    def test_leaf_exceptions_inherit_from_correct_parent(
        self, subclass: type, parent: type
    ) -> None:
        """Every leaf exception must be a subclass of its documented parent."""
        assert issubclass(subclass, parent)

    def test_llm_service_error_is_exception(self) -> None:
        """The root error must ultimately inherit from built-in Exception."""
        assert issubclass(LLMServiceError, Exception)


# ═══════════════════════════════════════════════════════════════════════════════
# Structured error codes
# ═══════════════════════════════════════════════════════════════════════════════


class TestExceptionErrorCodes:
    """Every exception must carry a stable, machine-readable error code."""

    def test_base_error_code(self) -> None:
        """LLMServiceError must have a default error code."""
        exc = LLMServiceError("generic failure")

        assert exc.error_code == "LLM_SERVICE_ERROR"

    def test_llm_service_error_stores_details(self) -> None:
        """The optional details dict must be accessible."""
        exc = LLMServiceError("fail", details={"field": "email", "reason": "invalid"})

        assert exc.details == {"field": "email", "reason": "invalid"}

    @pytest.mark.parametrize(
        "exception_class,expected_code",
        [
            (ProviderError, "PROVIDER_ERROR"),
            (AuthenticationError, "AUTHENTICATION_ERROR"),
            (InvalidAPIKeyError, "INVALID_API_KEY"),
            (ExpiredTokenError, "EXPIRED_TOKEN"),
            (RateLimitError, "RATE_LIMIT_ERROR"),
            (RequestsPerMinuteExceededError, "RPM_EXCEEDED"),
            (TokensPerMinuteExceededError, "TPM_EXCEEDED"),
            (ProviderValidationError, "PROVIDER_VALIDATION_ERROR"),
            (InvalidRequestError, "INVALID_REQUEST"),
            (ModelNotSupportedError, "MODEL_NOT_SUPPORTED"),
            (ProviderUnavailableError, "PROVIDER_UNAVAILABLE"),
            (ServiceDownError, "SERVICE_DOWN"),
            (ProviderTimeoutError, "PROVIDER_TIMEOUT"),
            (ProviderInternalError, "PROVIDER_INTERNAL_ERROR"),
            (TenantError, "TENANT_ERROR"),
            (TenantNotFoundError, "TENANT_NOT_FOUND"),
            (TenantSuspendedError, "TENANT_SUSPENDED"),
            (QuotaError, "QUOTA_ERROR"),
            (QuotaExceededError, "QUOTA_EXCEEDED"),
            (ConcurrentRequestLimitError, "CONCURRENT_REQUEST_LIMIT"),
            (DeploymentError, "DEPLOYMENT_ERROR"),
            (DeploymentNotFoundError, "DEPLOYMENT_NOT_FOUND"),
            (DeploymentInactiveError, "DEPLOYMENT_INACTIVE"),
            (ConfigurationError, "CONFIGURATION_ERROR"),
            (ResourceNotFoundError, "RESOURCE_NOT_FOUND"),
            (ResourceConflictError, "RESOURCE_CONFLICT"),
            (ManagementValidationError, "MANAGEMENT_VALIDATION_ERROR"),
            (TenantAccessDeniedError, "TENANT_ACCESS_DENIED"),
            (InvalidStateTransitionError, "INVALID_STATE_TRANSITION"),
        ],
    )
    def test_error_code_matches_specification(
        self, exception_class: type[LLMServiceError], expected_code: str
    ) -> None:
        """Each exception class must expose its documented error code."""
        exc = _minimal_instance(exception_class)

        assert exc.error_code == expected_code


# ═══════════════════════════════════════════════════════════════════════════════
# Structured messages (via str(exc))
# ═══════════════════════════════════════════════════════════════════════════════


class TestExceptionMessages:
    """Exception string representations must include contextual identifiers."""

    def test_invalid_api_key_message_contains_provider_and_masked_key(self) -> None:
        exc = InvalidAPIKeyError(provider_name="openai", masked_key="sk-****abcd")

        assert "openai" in str(exc)
        assert "sk-****abcd" in str(exc)

    def test_expired_token_message_contains_provider_name(self) -> None:
        exc = ExpiredTokenError(provider_name="azure_openai")

        assert "azure_openai" in str(exc)

    def test_model_not_supported_message_contains_provider_and_model(self) -> None:
        exc = ModelNotSupportedError(provider_name="bedrock", model_name="gpt-4o")

        assert "bedrock" in str(exc)
        assert "gpt-4o" in str(exc)

    def test_tenant_not_found_message_contains_tenant_id(self) -> None:
        exc = TenantNotFoundError(tenant_id="tenant-missing")

        assert "tenant-missing" in str(exc)

    def test_tenant_suspended_message_contains_reason(self) -> None:
        exc = TenantSuspendedError(tenant_id="t-1", reason="payment overdue")

        assert "t-1" in str(exc)
        assert "payment overdue" in str(exc)

    def test_deployment_not_found_message_contains_tenant_and_key(self) -> None:
        exc = DeploymentNotFoundError(tenant_id="acme", deployment_key="gpt4-missing")

        assert "acme" in str(exc)
        assert "gpt4-missing" in str(exc)

    def test_service_down_message_contains_status_code(self) -> None:
        exc = ServiceDownError(provider_name="anthropic", status_code=503)

        assert "503" in str(exc)
        assert "anthropic" in str(exc)

    def test_provider_timeout_message_contains_timeout_seconds(self) -> None:
        exc = ProviderTimeoutError(provider_name="openai", timeout_seconds=60.0)

        assert "60.0" in str(exc)
        assert "openai" in str(exc)

    def test_invalid_state_transition_message_contains_from_to_and_current(self) -> None:
        exc = InvalidStateTransitionError(
            resource_name="Deployment",
            current_status="maintenance",
            target_status="deleted",
        )

        assert "Deployment" in str(exc)
        assert "maintenance" in str(exc)
        assert "deleted" in str(exc)

    def test_tenant_access_denied_message_contains_ids_and_role(self) -> None:
        exc = TenantAccessDeniedError(user_id="u-1", tenant_id="t-1", required_role="admin")

        assert "u-1" in str(exc)
        assert "t-1" in str(exc)
        assert "admin" in str(exc)


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _minimal_instance(exception_class: type[LLMServiceError]) -> LLMServiceError:
    """Create a minimal instance of an exception class for testing.

    Some exception classes require constructor args. This helper provides
    sensible defaults so we can test error_code without knowing internals.
    """
    # ManagementError subtypes
    if exception_class is ResourceNotFoundError:
        return exception_class("Tenant", "unknown")
    if exception_class is ResourceConflictError:
        return exception_class("duplicate email")
    if exception_class is ManagementValidationError:
        return exception_class("invalid field")
    if exception_class is TenantAccessDeniedError:
        return exception_class(user_id="u-1", tenant_id="t-1", required_role="admin")
    if exception_class is InvalidStateTransitionError:
        return exception_class(
            resource_name="Deployment", current_status="active", target_status="deleted"
        )
    if exception_class is ConfigurationError:
        return exception_class("invalid config")
    if exception_class is DeploymentError:
        return exception_class("deployment failure")
    if exception_class is QuotaError:
        return exception_class("quota failure")

    # ProviderError-based subtypes: all need provider_name
    if issubclass(exception_class, InvalidAPIKeyError):
        return exception_class("test-provider", "sk-****test")
    if issubclass(exception_class, ExpiredTokenError):
        return exception_class("test-provider")
    if issubclass(exception_class, RequestsPerMinuteExceededError):
        return exception_class("test-provider")
    if issubclass(exception_class, TokensPerMinuteExceededError):
        return exception_class("test-provider")
    if issubclass(exception_class, InvalidRequestError):
        return exception_class("test-provider", "field", "invalid")
    if issubclass(exception_class, ModelNotSupportedError):
        return exception_class("test-provider", "test-model")
    if issubclass(exception_class, ServiceDownError):
        return exception_class("test-provider", 500)
    if issubclass(exception_class, ProviderTimeoutError):
        return exception_class("test-provider", 30.0)

    # ProviderError and generic subclasses that just need provider_name kwarg
    if issubclass(exception_class, ProviderError):
        return exception_class("provider error message", provider_name="test-provider")

    # TenantError-based subtypes
    if issubclass(exception_class, TenantNotFoundError):
        return exception_class("t-unknown")
    if issubclass(exception_class, TenantSuspendedError):
        return exception_class("t-1")
    if issubclass(exception_class, TenantError):
        return exception_class("tenant error", tenant_id="t-1")

    # QuotaError-based subtypes
    if issubclass(exception_class, QuotaExceededError):
        return exception_class("requests", 100, 101)
    if issubclass(exception_class, ConcurrentRequestLimitError):
        return exception_class("t-1", 10)

    # DeploymentError-based subtypes
    if issubclass(exception_class, DeploymentNotFoundError):
        return exception_class("t-1", "dep-key")
    if issubclass(exception_class, DeploymentInactiveError):
        return exception_class("dep-key", "maintenance")

    # Fallback: LLMServiceError just needs a message
    return exception_class("test message")
