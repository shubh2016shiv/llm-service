"""
Exception Hierarchy — All domain exceptions for the LLM Provider Service.

All exceptions in this service inherit from LLMServiceError. This gives
callers a single catch-all type while allowing fine-grained handling.

Hierarchy:
----------
    LLMServiceError
    ├── ProviderError                 # Failures from the external LLM provider
    │   ├── AuthenticationError
    │   │   ├── InvalidAPIKeyError
    │   │   └── ExpiredTokenError
    │   ├── RateLimitError
    │   │   ├── RequestsPerMinuteExceededError
    │   │   └── TokensPerMinuteExceededError
    │   ├── ProviderValidationError
    │   │   ├── InvalidRequestError
    │   │   └── ModelNotSupportedError
    │   ├── ProviderUnavailableError
    │   │   ├── ServiceDownError
    │   │   └── ProviderTimeoutError
    │   └── ProviderInternalError
    ├── TenantError                   # Tenant identity / status problems
    │   ├── TenantNotFoundError
    │   └── TenantSuspendedError
    ├── QuotaError                    # Quota and concurrency limit violations
    │   ├── QuotaExceededError
    │   └── ConcurrentRequestLimitError
    ├── DeploymentError               # Deployment resolution failures
    │   ├── DeploymentNotFoundError
    │   └── DeploymentInactiveError
    └── ConfigurationError            # Startup / settings loading failures

Architecture:
-------------
    Adapters (providers/) raise ProviderError subtypes.
    Services (services/) raise TenantError, QuotaError, DeploymentError.
    Interfaces (api/) translate these to HTTP responses.

Step-by-step error propagation flow:
    1. Lower layers raise the most specific typed exception possible.
    2. Service/orchestration layers may enrich context and re-raise typed errors.
    3. API exception handlers map ``error_code`` values to stable HTTP responses.
    4. Clients rely on machine-readable codes instead of brittle string parsing.

Dependencies: None — stdlib only.

Author: Shubham Singh
"""

from __future__ import annotations

# ── Base ─────────────────────────────────────────────────────────────────────


class LLMServiceError(Exception):
    """Base exception for all errors originating in the LLM Provider Service.

    Every custom exception in this codebase inherits from this class.
    Catching LLMServiceError catches everything non-stdlib this service raises.

    Example:
        >>> try:
        ...     raise InvalidAPIKeyError(provider_name="openai", masked_key="sk-****")
        ... except LLMServiceError as exc:
        ...     print(exc.error_code)
        'INVALID_API_KEY'
    """

    # Subclasses override this to provide a stable machine-readable code.
    error_code: str = "LLM_SERVICE_ERROR"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        """Initialise with a descriptive message and optional structured details.

        Args:
            message: Human-readable description including offending values.
            details: Optional dict of structured context (logged, not shown to end users).
        """
        super().__init__(message)
        self.details: dict = details or {}


# ── Provider Errors ───────────────────────────────────────────────────────────


class ProviderError(LLMServiceError):
    """Base for all errors originating from an external LLM provider call.

    Adapter code must always wrap low-level httpx/boto3 exceptions in a
    ProviderError subtype before re-raising. The service layer must never
    see httpx.HTTPStatusError or botocore.exceptions.ClientError directly.
    """

    error_code: str = "PROVIDER_ERROR"

    def __init__(
        self,
        message: str,
        *,
        provider_name: str,
        status_code: int | None = None,
        retry_count: int = 0,
        details: dict | None = None,
    ) -> None:
        """Initialise with provider context.

        Args:
            message: Human-readable error description.
            provider_name: Lowercase provider identifier (e.g., 'openai').
            status_code: HTTP status code from the provider response, if applicable.
            retry_count: Number of retry attempts made before this exception.
            details: Additional structured context dict.
        """
        super().__init__(message, details=details)
        self.provider_name = provider_name
        self.status_code = status_code
        self.retry_count = retry_count


class AuthenticationError(ProviderError):
    """Provider rejected the request due to an authentication failure."""

    error_code: str = "AUTHENTICATION_ERROR"


class InvalidAPIKeyError(AuthenticationError):
    """The API key supplied is invalid or has been revoked.

    Example:
        >>> raise InvalidAPIKeyError(
        ...     provider_name="openai", masked_key="sk-****abcd"
        ... )
    """

    error_code: str = "INVALID_API_KEY"

    def __init__(self, provider_name: str, masked_key: str) -> None:
        """Initialise with the provider and a masked (safe-to-log) key snippet.

        Args:
            provider_name: Lowercase provider identifier.
            masked_key: Redacted key shown in logs (never the full key).
        """
        super().__init__(
            f"API key rejected by {provider_name!r}. Key ending: {masked_key!r}.",
            provider_name=provider_name,
            status_code=401,
        )
        self.masked_key = masked_key


class ExpiredTokenError(AuthenticationError):
    """The OAuth / temporary token has expired.

    Example:
        >>> raise ExpiredTokenError(provider_name="azure_openai")
    """

    error_code: str = "EXPIRED_TOKEN"

    def __init__(self, provider_name: str) -> None:
        """Initialise with the provider whose token expired.

        Args:
            provider_name: Lowercase provider identifier.
        """
        super().__init__(
            f"Access token for {provider_name!r} has expired. Refresh required.",
            provider_name=provider_name,
            status_code=401,
        )


class RateLimitError(ProviderError):
    """Provider's rate limit was exceeded. Retry after the suggested delay."""

    error_code: str = "RATE_LIMIT_ERROR"


class RequestsPerMinuteExceededError(RateLimitError):
    """Provider's requests-per-minute limit was exceeded.

    Example:
        >>> raise RequestsPerMinuteExceededError(
        ...     provider_name="openai", retry_after_seconds=30
        ... )
    """

    error_code: str = "RPM_EXCEEDED"

    def __init__(self, provider_name: str, retry_after_seconds: int | None = None) -> None:
        """Initialise with optional retry-after hint from the provider response.

        Args:
            provider_name: Lowercase provider identifier.
            retry_after_seconds: Seconds until the rate limit window resets.
        """
        hint = f" Retry after {retry_after_seconds}s." if retry_after_seconds else ""
        super().__init__(
            f"Requests-per-minute limit exceeded for {provider_name!r}.{hint}",
            provider_name=provider_name,
            status_code=429,
        )
        self.retry_after_seconds = retry_after_seconds


class TokensPerMinuteExceededError(RateLimitError):
    """Provider's tokens-per-minute limit was exceeded.

    Example:
        >>> raise TokensPerMinuteExceededError(provider_name="anthropic")
    """

    error_code: str = "TPM_EXCEEDED"

    def __init__(self, provider_name: str, retry_after_seconds: int | None = None) -> None:
        """Initialise with optional retry-after hint.

        Args:
            provider_name: Lowercase provider identifier.
            retry_after_seconds: Seconds until the rate limit window resets.
        """
        hint = f" Retry after {retry_after_seconds}s." if retry_after_seconds else ""
        super().__init__(
            f"Tokens-per-minute limit exceeded for {provider_name!r}.{hint}",
            provider_name=provider_name,
            status_code=429,
        )
        self.retry_after_seconds = retry_after_seconds


class ProviderValidationError(ProviderError):
    """The request payload was rejected by the provider as invalid."""

    error_code: str = "PROVIDER_VALIDATION_ERROR"


class InvalidRequestError(ProviderValidationError):
    """Provider returned 400 — the request body is malformed or has bad params.

    Example:
        >>> raise InvalidRequestError(
        ...     provider_name="openai",
        ...     field="max_tokens",
        ...     reason="Must be <= 4096",
        ... )
    """

    error_code: str = "INVALID_REQUEST"

    def __init__(self, provider_name: str, field: str, reason: str) -> None:
        """Initialise with the offending field and reason.

        Args:
            provider_name: Lowercase provider identifier.
            field: Name of the request field that caused the rejection.
            reason: Provider's explanation of the rejection.
        """
        super().__init__(
            f"Invalid request to {provider_name!r}: field={field!r}, reason={reason!r}.",
            provider_name=provider_name,
            status_code=400,
        )
        self.field = field
        self.reason = reason


class ModelNotSupportedError(ProviderValidationError):
    """The requested model is not available via this provider deployment.

    Example:
        >>> raise ModelNotSupportedError(provider_name="bedrock", model_name="gpt-4o")
    """

    error_code: str = "MODEL_NOT_SUPPORTED"

    def __init__(self, provider_name: str, model_name: str) -> None:
        """Initialise with the provider and the unsupported model name.

        Args:
            provider_name: Lowercase provider identifier.
            model_name: Model name that was requested but not found.
        """
        super().__init__(
            f"Model {model_name!r} is not supported by provider {provider_name!r}.",
            provider_name=provider_name,
            status_code=400,
        )
        self.model_name = model_name


class ProviderUnavailableError(ProviderError):
    """The provider is temporarily unavailable (5xx, network failure)."""

    error_code: str = "PROVIDER_UNAVAILABLE"


class ServiceDownError(ProviderUnavailableError):
    """Provider returned a 5xx response indicating service degradation.

    Example:
        >>> raise ServiceDownError(provider_name="anthropic", status_code=503)
    """

    error_code: str = "SERVICE_DOWN"

    def __init__(self, provider_name: str, status_code: int) -> None:
        """Initialise with the provider and observed HTTP status code.

        Args:
            provider_name: Lowercase provider identifier.
            status_code: HTTP status code received (e.g., 503).
        """
        super().__init__(
            f"Provider {provider_name!r} returned {status_code} (service down).",
            provider_name=provider_name,
            status_code=status_code,
        )


class ProviderTimeoutError(ProviderUnavailableError):
    """The provider did not respond within the configured timeout.

    Example:
        >>> raise ProviderTimeoutError(provider_name="openai", timeout_seconds=60.0)
    """

    error_code: str = "PROVIDER_TIMEOUT"

    def __init__(self, provider_name: str, timeout_seconds: float) -> None:
        """Initialise with the provider and the timeout that elapsed.

        Args:
            provider_name: Lowercase provider identifier.
            timeout_seconds: The configured timeout value that was exceeded.
        """
        super().__init__(
            f"Provider {provider_name!r} timed out after {timeout_seconds}s.",
            provider_name=provider_name,
        )
        self.timeout_seconds = timeout_seconds


class ProviderInternalError(ProviderError):
    """An unexpected internal error occurred during provider communication."""

    error_code: str = "PROVIDER_INTERNAL_ERROR"


# ── Tenant Errors ─────────────────────────────────────────────────────────────


class TenantError(LLMServiceError):
    """Base for errors related to tenant identity or status."""

    error_code: str = "TENANT_ERROR"

    def __init__(self, message: str, *, tenant_id: str, details: dict | None = None) -> None:
        """Initialise with the tenant identifier.

        Args:
            message: Human-readable description.
            tenant_id: The UUID string of the tenant involved.
            details: Optional structured context.
        """
        super().__init__(message, details=details)
        self.tenant_id = tenant_id


class TenantNotFoundError(TenantError):
    """No tenant record exists for the supplied identifier or API key.

    Example:
        >>> raise TenantNotFoundError(tenant_id="unknown-uuid")
    """

    error_code: str = "TENANT_NOT_FOUND"

    def __init__(self, tenant_id: str) -> None:
        """Initialise with the unresolvable tenant identifier.

        Args:
            tenant_id: UUID or API-key-derived identifier that was not found.
        """
        super().__init__(
            f"Tenant not found: {tenant_id!r}.",
            tenant_id=tenant_id,
        )


class TenantSuspendedError(TenantError):
    """The tenant account is suspended and cannot process requests.

    Example:
        >>> raise TenantSuspendedError(tenant_id="acme-uuid", reason="payment overdue")
    """

    error_code: str = "TENANT_SUSPENDED"

    def __init__(self, tenant_id: str, reason: str | None = None) -> None:
        """Initialise with the tenant ID and optional suspension reason.

        Args:
            tenant_id: UUID of the suspended tenant.
            reason: Short description of why the account is suspended.
        """
        detail = f" Reason: {reason}." if reason else ""
        super().__init__(
            f"Tenant {tenant_id!r} is suspended.{detail}",
            tenant_id=tenant_id,
        )
        self.reason = reason


# ── Quota Errors ──────────────────────────────────────────────────────────────


class QuotaError(LLMServiceError):
    """Base for quota and concurrency limit violations."""

    error_code: str = "QUOTA_ERROR"


class QuotaExceededError(QuotaError):
    """A token or cost quota has been exhausted for the period.

    Example:
        >>> raise QuotaExceededError(
        ...     quota_type="monthly_tokens",
        ...     limit=1_000_000,
        ...     used=1_000_001,
        ... )
    """

    error_code: str = "QUOTA_EXCEEDED"

    def __init__(self, quota_type: str, limit: int, used: int) -> None:
        """Initialise with quota type and the limit that was breached.

        Args:
            quota_type: Human label for the quota (e.g., 'monthly_tokens').
            limit: The configured quota ceiling.
            used: The current usage value that exceeded the ceiling.
        """
        super().__init__(
            f"Quota exceeded for {quota_type!r}: used={used:,}, limit={limit:,}."
        )
        self.quota_type = quota_type
        self.limit = limit
        self.used = used


class ConcurrentRequestLimitError(QuotaError):
    """The maximum number of in-flight requests for this tenant is reached.

    Example:
        >>> raise ConcurrentRequestLimitError(tenant_id="acme", limit=10)
    """

    error_code: str = "CONCURRENT_REQUEST_LIMIT"

    def __init__(self, tenant_id: str, limit: int) -> None:
        """Initialise with the tenant ID and concurrent request ceiling.

        Args:
            tenant_id: UUID of the tenant at the limit.
            limit: The concurrent request ceiling for this tenant.
        """
        super().__init__(
            f"Concurrent request limit ({limit}) reached for tenant {tenant_id!r}."
        )
        self.tenant_id = tenant_id
        self.limit = limit


# ── Deployment Errors ─────────────────────────────────────────────────────────


class DeploymentError(LLMServiceError):
    """Base for errors related to deployment resolution."""

    error_code: str = "DEPLOYMENT_ERROR"


class DeploymentNotFoundError(DeploymentError):
    """No deployment matches the requested key for this tenant.

    Example:
        >>> raise DeploymentNotFoundError(
        ...     tenant_id="acme", deployment_key="gpt4-missing"
        ... )
    """

    error_code: str = "DEPLOYMENT_NOT_FOUND"

    def __init__(self, tenant_id: str, deployment_key: str) -> None:
        """Initialise with tenant and the unresolvable deployment key.

        Args:
            tenant_id: UUID of the tenant making the request.
            deployment_key: The deployment_key string that was not found.
        """
        super().__init__(
            f"Deployment {deployment_key!r} not found for tenant {tenant_id!r}."
        )
        self.tenant_id = tenant_id
        self.deployment_key = deployment_key


class DeploymentInactiveError(DeploymentError):
    """The resolved deployment exists but is not in ACTIVE status.

    Example:
        >>> raise DeploymentInactiveError(
        ...     deployment_key="gpt4-prod", status="maintenance"
        ... )
    """

    error_code: str = "DEPLOYMENT_INACTIVE"

    def __init__(self, deployment_key: str, status: str) -> None:
        """Initialise with the deployment key and its current status.

        Args:
            deployment_key: The deployment_key string.
            status: Current lifecycle status (e.g., 'maintenance', 'inactive').
        """
        super().__init__(
            f"Deployment {deployment_key!r} is not active (status={status!r})."
        )
        self.deployment_key = deployment_key
        self.status = status


# ── Management API Errors ─────────────────────────────────────────────────────


class ManagementError(LLMServiceError):
    """Base for CRUD and tenant-access failures in the management API."""

    error_code: str = "MANAGEMENT_ERROR"


class ResourceNotFoundError(ManagementError):
    """Raised when a requested management resource does not exist."""

    error_code: str = "RESOURCE_NOT_FOUND"

    def __init__(self, resource_name: str, resource_id: str) -> None:
        """Initialise with the missing resource name and identifier."""
        super().__init__(
            f"{resource_name} not found: {resource_id!r}.",
            details={"resource_name": resource_name, "resource_id": resource_id},
        )
        self.resource_name = resource_name
        self.resource_id = resource_id


class ResourceConflictError(ManagementError):
    """Raised when a create/update violates an existing resource invariant."""

    error_code: str = "RESOURCE_CONFLICT"

    def __init__(self, message: str) -> None:
        """Initialise with a conflict message containing the offending value."""
        super().__init__(message)


class ManagementValidationError(ManagementError):
    """Raised when management input fails domain-level validation."""

    error_code: str = "MANAGEMENT_VALIDATION_ERROR"

    def __init__(self, message: str) -> None:
        """Initialise with a validation message containing the offending value."""
        super().__init__(message)


class TenantAccessDeniedError(ManagementError):
    """Raised when the caller lacks the required tenant-scoped authority."""

    error_code: str = "TENANT_ACCESS_DENIED"

    def __init__(self, user_id: str, tenant_id: str, required_role: str) -> None:
        """Initialise with caller, tenant, and required role context."""
        super().__init__(
            f"User {user_id!r} lacks {required_role!r} access for tenant {tenant_id!r}.",
            details={
                "user_id": user_id,
                "tenant_id": tenant_id,
                "required_role": required_role,
            },
        )
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.required_role = required_role


class InvalidStateTransitionError(ManagementError):
    """Raised when a lifecycle endpoint cannot apply the requested transition."""

    error_code: str = "INVALID_STATE_TRANSITION"

    def __init__(self, resource_name: str, current_status: str, target_status: str) -> None:
        """Initialise with lifecycle transition details."""
        super().__init__(
            f"Cannot move {resource_name!r} from {current_status!r} to {target_status!r}.",
            details={
                "resource_name": resource_name,
                "current_status": current_status,
                "target_status": target_status,
            },
        )


# ── Config Errors ─────────────────────────────────────────────────────────────


class ConfigurationError(LLMServiceError):
    """Raised during startup when a settings file is missing or invalid.

    Example:
        >>> raise ConfigurationError(
        ...     "providers/openai.yaml missing required field 'auth.mode'"
        ... )
    """

    error_code: str = "CONFIGURATION_ERROR"
