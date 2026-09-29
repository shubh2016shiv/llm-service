"""Secret-backend availability exceptions.

Architecture: secret store adapter -> secret backend error -> API handlers.
"""

from app.core.exceptions.base import LLMServiceError


class SecretReadError(LLMServiceError):
    """Base class for a backend answer that cannot produce a usable secret."""

    def __init__(self, message: str, secret_reference: str) -> None:
        """Retain only the safe reference, never backend response details."""
        super().__init__(message, details={"secret_reference": secret_reference})
        self.secret_reference = secret_reference


class SecretReferenceNotFoundError(SecretReadError):
    """The configured provider credential reference does not exist."""

    error_code = "SECRET_REFERENCE_NOT_FOUND"

    def __init__(self, secret_reference: str) -> None:
        """Describe a non-retryable credential configuration defect."""
        super().__init__(
            f"Required provider credential {secret_reference!r} was not found.",
            secret_reference,
        )


class InvalidSecretValueError(SecretReadError):
    """The stored provider credential is corrupt or cannot be decrypted."""

    error_code = "INVALID_SECRET_VALUE"

    def __init__(self, secret_reference: str) -> None:
        """Report unusable secret data without exposing the backend reason."""
        super().__init__(
            f"Stored provider credential {secret_reference!r} is invalid and cannot be used.",
            secret_reference,
        )


class SecretAccessDeniedError(SecretReadError):
    """The secret backend refused access to a required provider credential."""

    error_code = "SECRET_ACCESS_DENIED"

    def __init__(self, secret_reference: str) -> None:
        """Report an operational access outage without leaking backend text."""
        super().__init__(
            f"Access to required provider credential {secret_reference!r} was denied.",
            secret_reference,
        )


class SecretBackendUnavailableError(LLMServiceError):
    """The secret backend could not be reached or failed to answer.

    Distinct from ``SecretReadError`` subclasses, which represent an answer
    that was missing, invalid, or denied. This one means
    no usable answer arrived at all — the host is unreachable, the request
    timed out, or the backend returned a server error.

    The distinction is worth a separate type because the remediation differs.
    A missing secret is a configuration mistake that a retry will never fix.
    An unreachable backend is an infrastructure problem that is very often
    transient, so callers are told 503 and may retry, and operators are
    pointed at the dependency rather than at this service's own code.
    """

    error_code = "SECRET_BACKEND_UNAVAILABLE"

    def __init__(self, backend_name: str, secret_reference: str, reason: str) -> None:
        """Initialize with the backend and reference, never the secret value.

        Args:
            backend_name: Which backend failed, for example ``"vault"``.
            secret_reference: The pointer being read. Safe to include: a
                reference names *where* a secret lives, never the secret.
            reason: Short description of the transport failure.
        """
        super().__init__(
            f"Secret backend {backend_name!r} is unavailable while accessing "
            f"{secret_reference!r}: {reason}",
            details={"backend_name": backend_name, "secret_reference": secret_reference},
        )
        self.backend_name = backend_name
        self.secret_reference = secret_reference
        self.reason = reason
