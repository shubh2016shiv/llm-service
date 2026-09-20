"""Management API and tenant-access exceptions.

Architecture: management API services -> management errors -> API handlers.
"""

from app.core.exceptions.base import LLMServiceError


class ManagementError(LLMServiceError):
    """Base error for management API domain failures."""

    error_code = "MANAGEMENT_ERROR"


class ResourceNotFoundError(ManagementError):
    """Requested management resource does not exist."""

    error_code = "RESOURCE_NOT_FOUND"

    def __init__(self, resource_name: str, resource_id: str) -> None:
        """Initialize missing resource context."""
        super().__init__(
            f"{resource_name} not found: {resource_id!r}.",
            details={"resource_name": resource_name, "resource_id": resource_id},
        )
        self.resource_name, self.resource_id = resource_name, resource_id


class ResourceConflictError(ManagementError):
    """Create or update violates a resource invariant."""

    error_code = "RESOURCE_CONFLICT"


class ManagementValidationError(ManagementError):
    """Management input fails domain validation."""

    error_code = "MANAGEMENT_VALIDATION_ERROR"


class TenantAccessDeniedError(ManagementError):
    """Caller lacks required tenant-scoped authority."""

    error_code = "TENANT_ACCESS_DENIED"

    def __init__(self, user_id: str, tenant_id: str, required_role: str) -> None:
        """Initialize caller, tenant, and required-role context."""
        super().__init__(
            f"User {user_id!r} lacks {required_role!r} access for tenant {tenant_id!r}.",
            details={"user_id": user_id, "tenant_id": tenant_id, "required_role": required_role},
        )
        self.user_id, self.tenant_id, self.required_role = user_id, tenant_id, required_role


class InvalidStateTransitionError(ManagementError):
    """Lifecycle endpoint cannot apply the requested state transition."""

    error_code = "INVALID_STATE_TRANSITION"

    def __init__(self, resource_name: str, current_status: str, target_status: str) -> None:
        """Initialize lifecycle transition context."""
        super().__init__(
            f"Cannot move {resource_name!r} from {current_status!r} to {target_status!r}.",
            details={
                "resource_name": resource_name,
                "current_status": current_status,
                "target_status": target_status,
            },
        )
