"""Authorization-cache reliability exceptions.

Architecture: authorization cache -> cache error -> management API handler.
"""

from app.core.exceptions.base import LLMServiceError


class AuthorizationGrantCacheUnavailableError(LLMServiceError):
    """Security-sensitive grant invalidation could not be persisted."""

    error_code = "AUTHORIZATION_GRANT_CACHE_UNAVAILABLE"

    def __init__(self, operation: str) -> None:
        """Initialize the failed operation without exposing cache keys."""
        super().__init__(
            f"Authorization grant cache could not complete operation: {operation}",
            details={"operation": operation},
        )
        self.operation = operation
