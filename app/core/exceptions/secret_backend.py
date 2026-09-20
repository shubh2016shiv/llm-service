"""Secret-backend availability exceptions.

Architecture: secret store adapter -> secret backend error -> API handlers.
"""

from app.core.exceptions.base import LLMServiceError


class SecretBackendUnavailableError(LLMServiceError):
    """The secret backend could not be reached or failed to answer.

    Distinct from the three conditions in ``SecretStore``'s documented
    vocabulary (``KeyError`` for a missing reference, ``ValueError`` for an
    unreadable value, ``PermissionError`` for a denied read). Each of those
    means the backend answered and the answer was unusable. This one means
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
