"""External LLM-provider exceptions for cloud and direct adapters.

Architecture: cloud/direct LLM adapters -> provider errors -> API exception handlers.
"""

from app.core.exceptions.base import LLMServiceError


class ProviderError(LLMServiceError):
    """Base error for an external LLM provider call."""

    error_code = "PROVIDER_ERROR"

    def __init__(
        self,
        message: str,
        *,
        provider_name: str,
        status_code: int | None = None,
        retry_count: int = 0,
        details: dict[str, object] | None = None,
    ) -> None:
        """Initialize provider failure context without exposing credentials."""
        super().__init__(message, details=details)
        self.provider_name, self.status_code, self.retry_count = (
            provider_name,
            status_code,
            retry_count,
        )


class AuthenticationError(ProviderError):
    """Provider rejected the request credentials."""

    error_code = "AUTHENTICATION_ERROR"


class InvalidAPIKeyError(AuthenticationError):
    """Provider rejected a masked API-key reference."""

    error_code = "INVALID_API_KEY"

    def __init__(self, provider_name: str, masked_key: str) -> None:
        """Initialize a safe-to-log invalid-key failure."""
        super().__init__(
            f"API key rejected by {provider_name!r}. Key ending: {masked_key!r}.",
            provider_name=provider_name,
            status_code=401,
        )
        self.masked_key = masked_key


class ExpiredTokenError(AuthenticationError):
    """Provider OAuth or temporary token expired."""

    error_code = "EXPIRED_TOKEN"

    def __init__(self, provider_name: str) -> None:
        """Initialize token-expiry context."""
        super().__init__(
            f"Access token for {provider_name!r} has expired. Refresh required.",
            provider_name=provider_name,
            status_code=401,
        )


class RateLimitError(ProviderError):
    """Provider rate limit was exceeded."""

    error_code = "RATE_LIMIT_ERROR"


class RequestsPerMinuteExceededError(RateLimitError):
    """Provider requests-per-minute limit was exceeded."""

    error_code = "RPM_EXCEEDED"

    def __init__(self, provider_name: str, retry_after_seconds: int | None = None) -> None:
        """Initialize an RPM limit failure with an optional retry hint."""
        hint = f" Retry after {retry_after_seconds}s." if retry_after_seconds else ""
        super().__init__(
            f"Requests-per-minute limit exceeded for {provider_name!r}.{hint}",
            provider_name=provider_name,
            status_code=429,
        )
        self.retry_after_seconds = retry_after_seconds


class TokensPerMinuteExceededError(RateLimitError):
    """Provider tokens-per-minute limit was exceeded."""

    error_code = "TPM_EXCEEDED"

    def __init__(self, provider_name: str, retry_after_seconds: int | None = None) -> None:
        """Initialize a TPM limit failure with an optional retry hint."""
        hint = f" Retry after {retry_after_seconds}s." if retry_after_seconds else ""
        super().__init__(
            f"Tokens-per-minute limit exceeded for {provider_name!r}.{hint}",
            provider_name=provider_name,
            status_code=429,
        )
        self.retry_after_seconds = retry_after_seconds


class ProviderValidationError(ProviderError):
    """Provider rejected a request payload as invalid."""

    error_code = "PROVIDER_VALIDATION_ERROR"


class InvalidRequestError(ProviderValidationError):
    """Provider rejected an invalid request field."""

    error_code = "INVALID_REQUEST"

    def __init__(self, provider_name: str, field: str, reason: str) -> None:
        """Initialize field-level provider validation context."""
        super().__init__(
            f"Invalid request to {provider_name!r}: field={field!r}, reason={reason!r}.",
            provider_name=provider_name,
            status_code=400,
        )
        self.field, self.reason = field, reason


class ModelNotSupportedError(ProviderValidationError):
    """Requested model is unavailable through the provider deployment."""

    error_code = "MODEL_NOT_SUPPORTED"

    def __init__(self, provider_name: str, model_name: str) -> None:
        """Initialize provider/model compatibility context."""
        super().__init__(
            f"Model {model_name!r} is not supported by provider {provider_name!r}.",
            provider_name=provider_name,
            status_code=400,
        )
        self.model_name = model_name


class ProviderUnavailableError(ProviderError):
    """Provider is temporarily unavailable."""

    error_code = "PROVIDER_UNAVAILABLE"


class ServiceDownError(ProviderUnavailableError):
    """Provider returned a service-degradation response."""

    error_code = "SERVICE_DOWN"

    def __init__(self, provider_name: str, status_code: int) -> None:
        """Initialize provider availability context."""
        super().__init__(
            f"Provider {provider_name!r} returned {status_code} (service down).",
            provider_name=provider_name,
            status_code=status_code,
        )


class ProviderCircuitOpenError(ProviderUnavailableError):
    """The circuit breaker is open, so the call was rejected without dialing out.

    This is the breaker working as designed, not a fault in this service: the
    provider already failed enough times that continuing to call it would only
    add latency and load. Subclassing ProviderUnavailableError is deliberate —
    it inherits that class's 503 mapping through the MRO walk in
    ``_resolve_status``, so no status map needs a new entry.
    """

    error_code = "PROVIDER_CIRCUIT_OPEN"

    def __init__(self, provider_name: str, retry_after_seconds: int | None = None) -> None:
        """Initialize breaker-open context with an optional retry hint.

        Args:
            provider_name: Provider whose circuit is open.
            retry_after_seconds: Configured breaker reset window, surfaced as a
                ``Retry-After`` header. This is the full reset duration rather
                than the remaining time, which errs toward telling callers to
                wait slightly too long — the safe direction, and the only one
                available: ``aiobreaker`` raises ``TypeError`` from
                ``time_until_open`` while ``opens_at`` is unset, which is
                precisely the moment the circuit has just opened.
        """
        hint = f" Retry after {retry_after_seconds}s." if retry_after_seconds else ""
        super().__init__(
            f"Circuit breaker is open for provider {provider_name!r}; "
            f"the request was rejected without calling the provider.{hint}",
            provider_name=provider_name,
            status_code=503,
        )
        self.retry_after_seconds = retry_after_seconds


class ProviderTimeoutError(ProviderUnavailableError):
    """Provider did not respond within its configured timeout."""

    error_code = "PROVIDER_TIMEOUT"

    def __init__(self, provider_name: str, timeout_seconds: float) -> None:
        """Initialize timeout context."""
        super().__init__(
            f"Provider {provider_name!r} timed out after {timeout_seconds}s.",
            provider_name=provider_name,
        )
        self.timeout_seconds = timeout_seconds


class ProviderInternalError(ProviderError):
    """Unexpected provider-communication error."""

    error_code = "PROVIDER_INTERNAL_ERROR"
