"""Tenant lifecycle exceptions.

Architecture: tenant authorization -> tenant errors -> API handlers.
"""

from app.core.exceptions.base import LLMServiceError


class TenantError(LLMServiceError):
    """Base error for tenant identity or lifecycle state."""

    error_code = "TENANT_ERROR"

    def __init__(
        self, message: str, *, tenant_id: str, details: dict[str, object] | None = None
    ) -> None:
        """Initialize tenant-scoped diagnostic context."""
        super().__init__(message, details=details)
        self.tenant_id = tenant_id


class TenantNotFoundError(TenantError):
    """No tenant record exists for the supplied identifier."""

    error_code = "TENANT_NOT_FOUND"

    def __init__(self, tenant_id: str) -> None:
        """Initialize the unresolved tenant identifier."""
        super().__init__(f"Tenant not found: {tenant_id!r}.", tenant_id=tenant_id)


class TenantSuspendedError(TenantError):
    """Tenant cannot process requests in its current lifecycle state."""

    error_code = "TENANT_SUSPENDED"

    def __init__(self, tenant_id: str, reason: str | None = None) -> None:
        """Initialize suspension context."""
        detail = f" Reason: {reason}." if reason else ""
        super().__init__(f"Tenant {tenant_id!r} is suspended.{detail}", tenant_id=tenant_id)
        self.reason = reason
