"""Tenant LLM-deployment resolution exceptions.

Architecture: tenant inference route resolution -> deployment errors -> API handlers.
"""

from app.core.exceptions.base import LLMServiceError


class DeploymentError(LLMServiceError):
    """Base error for deployment resolution failures."""

    error_code = "DEPLOYMENT_ERROR"


class DeploymentNotFoundError(DeploymentError):
    """No deployment matches the requested tenant route key."""

    error_code = "DEPLOYMENT_NOT_FOUND"

    def __init__(self, tenant_id: str, deployment_key: str) -> None:
        """Initialize missing deployment context."""
        super().__init__(f"Deployment {deployment_key!r} not found for tenant {tenant_id!r}.")
        self.tenant_id, self.deployment_key = tenant_id, deployment_key


class DeploymentInactiveError(DeploymentError):
    """Deployment exists but cannot accept traffic."""

    error_code = "DEPLOYMENT_INACTIVE"

    def __init__(self, deployment_key: str, status: str) -> None:
        """Initialize inactive deployment context."""
        super().__init__(f"Deployment {deployment_key!r} is not active (status={status!r}).")
        self.deployment_key, self.status = deployment_key, status
