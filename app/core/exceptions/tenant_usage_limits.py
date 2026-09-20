"""Tenant usage-quota and concurrency-limit exceptions.

Architecture: tenant usage/capacity services -> limit errors -> API handlers.
"""

from app.core.exceptions.base import LLMServiceError


class QuotaError(LLMServiceError):
    """Base error for tenant usage and concurrency limit violations."""

    error_code = "QUOTA_ERROR"


class QuotaExceededError(QuotaError):
    """A token or cost quota has been exhausted."""

    error_code = "QUOTA_EXCEEDED"

    def __init__(self, quota_type: str, limit: int, used: int) -> None:
        """Initialize quota usage context."""
        super().__init__(f"Quota exceeded for {quota_type!r}: used={used:,}, limit={limit:,}.")
        self.quota_type, self.limit, self.used = quota_type, limit, used


class ConcurrentRequestLimitError(QuotaError):
    """Tenant has reached its in-flight request limit."""

    error_code = "CONCURRENT_REQUEST_LIMIT"

    def __init__(self, tenant_id: str, limit: int) -> None:
        """Initialize concurrency-limit context."""
        super().__init__(f"Concurrent request limit ({limit}) reached for tenant {tenant_id!r}.")
        self.tenant_id, self.limit = tenant_id, limit
