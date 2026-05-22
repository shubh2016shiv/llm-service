"""
Token Manager Client
====================

A client wrapper for communicating with the external Token Manager
microservice, which controls how many tokens or requests each tenant is
allowed to consume.

What problem does this solve?
    Before the LLM services application sends a request to an AI provider
    (like OpenAI or Anthropic), it needs to check whether the calling
    tenant has enough quota remaining. Instead of every part of the app
    knowing how to call the Token Manager directly, this class provides a
    single, clean Python interface: call ``check_quota()`` before the
    request, and ``report_usage()`` after the response comes back.

Current status:
    This is a placeholder implementation that always allows requests
    (``check_quota`` returns ``True``). In production, it will be replaced
    with actual network calls (gRPC or HTTP) to a real Token Manager
    service, without changing any code outside this file.

Enterprise Pattern: Adapter Pattern
    This class wraps an external service behind a Python interface. The
    rest of the application only knows about ``TokenManagerClient``
    methods — it never sees the underlying protocol, URL, or
    authentication. When the real Token Manager is deployed, only this
    file needs to change.

Dependencies:
    - app.schemas — request payload shapes used to estimate token counts
    - app.core.exceptions — base error class for quota-exceeded errors

Author: Shubham Singh
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
