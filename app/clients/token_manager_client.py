"""
Token Manager Client
====================

Outbound client for the token-allocation microservice.

Architecture:
-------------
    Gateway Service -> TokenManagerClient.check_quota(...) -> (Allows or Denies)

    Note:
    The actual implementation will communicate with an external Token Manager
    microservice (via gRPC or fast HTTP) to acquire tokens before allowing the
    LLM request to proceed to the provider.
Dependencies:
    - app.schemas.requests — request payload shapes for quota estimation
    - app.core.exceptions — base service errors

Author: Engineering Team
Last Updated: 2026-05-16
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.core.exceptions import LLMServiceError

if TYPE_CHECKING:
    from uuid import UUID

    from app.schemas.requests_schema import ChatRequest, EmbedRequest, RerankRequest

logger = logging.getLogger(__name__)


class QuotaExceededError(LLMServiceError):
    """Raised when the tenant has exceeded their allowed token or request quota."""

    def __init__(self, tenant_id: UUID, message: str) -> None:
        super().__init__(message=message)
        self.tenant_id = tenant_id


class TokenManagerClient:
    """Placeholder client for the external Token Manager microservice."""

    def __init__(self, endpoint_url: str = "http://token-manager:8000") -> None:
        self.endpoint_url = endpoint_url

    async def check_quota(
        self,
        tenant_id: UUID | str,
        deployment_key: str,
        request: ChatRequest | EmbedRequest | RerankRequest,
    ) -> bool:
        """Acquire tokens/quota for the requested operation.

        This is a placeholder that always returns True. In production, this will
        make a network call to the Token Manager to decrement the available quota
        or reject the request if rate-limited.

        Args:
            tenant_id: The UUID of the tenant making the request.
            deployment_key: The deployment being accessed.
            request: The actual request payload to estimate token usage.

        Returns:
            True if quota was acquired and request can proceed.

        Raises:
            QuotaExceededError: If the request should be rejected (HTTP 429).
        """
        # Placeholder: always allow
        logger.debug(
            "TokenManagerClient check_quota passed (placeholder)",
            extra={
                "tenant_id": str(tenant_id),
                "deployment_key": deployment_key,
            },
        )
        return True

    async def report_usage(
        self,
        tenant_id: UUID | str,
        deployment_key: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        """Report actual token usage back to the Token Manager after the provider returns.

        Since `check_quota` only estimates usage, this call reconciles the actual
        billed amount with the Token Manager.
        """
        # Placeholder: do nothing
        logger.debug(
            "TokenManagerClient report_usage (placeholder)",
            extra={
                "tenant_id": str(tenant_id),
                "deployment_key": deployment_key,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
        )
