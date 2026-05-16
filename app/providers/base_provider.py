"""
app/providers/base_provider.py — Abstract contract for all LLM providers.

Architecture
------------
                    ┌──────────────────────────┐
                    │      BaseProvider         │
                    │       (ABC)               │
                    │  + generate()             │
                    │  + embed()                │
                    │  + rerank()               │
                    │  + stream_generate()      │
                    │  + health_check()         │
                    │  # _build_auth_headers()  │
                    │  # _emit_structured_log() │
                    │  # _handle_provider_error │
                    │  # _effective_timeout()   │
                    └──────┬──────────┬────────┘
                           │          │
              ┌────────────┘          └────────────┐
              │                                    │
    ┌─────────┴─────────┐              ┌──────────┴──────────┐
    │  direct/          │              │  cloud/             │
    │  (REST API)       │              │  (Platform SDKs)    │
    │  OpenAIProvider   │              │  BedrockProvider    │
    │  AnthropicProv.   │              │  AzureOpenAIProv.   │
    │  VLLMProvider     │              │                     │
    └───────────────────┘              └─────────────────────┘

Design Constraints (per implementation_plan.md §6.1)
----------------------------------------------------
- Immutable after construction — all state is settings + shared HTTP client.
- All methods are pure functions over: request payload + frozen settings + shared client.
- NEVER store per-request or per-tenant state on the instance.
- Thread-safe: per-request variables are local to each call frame.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, AsyncIterator

import aiobreaker

if TYPE_CHECKING:
    from app.core.settings.models.provider_config import ProviderStaticConfig
    from app.core.settings.models.tenant_config import DeploymentConfig
    from app.core.exceptions import ProviderError
    from app.schemas.requests import ChatRequest, EmbedRequest, RerankRequest
    from app.schemas.responses import (
        ChatResponse,
        ChatStreamChunk,
        EmbedResponse,
        HealthStatus,
        RerankResponse,
    )

import httpx


class BaseProvider(ABC):
    """Abstract contract for all LLM providers.

    Immutable after construction. All methods are pure functions over:
      request payload + frozen settings + shared HTTP client.

    Never store per-request or per-tenant state on the instance.
    """

    def __init__(
        self,
        static_config: ProviderStaticConfig,
        deployment_config: DeploymentConfig,
        http_client: httpx.AsyncClient,
        circuit_breaker: aiobreaker.CircuitBreaker,
    ) -> None:
        self._static = static_config
        self._deployment = deployment_config
        self._http_client = http_client
        self._circuit_breaker = circuit_breaker
        self._logger = logging.getLogger(self.__class__.__module__)

    # ------------------------------------------------------------------
    # Public Execution Methods (Wrapped with Circuit Breaker)
    # ------------------------------------------------------------------

    async def generate(self, request: ChatRequest) -> ChatResponse:
        """Send a chat completion request through the circuit breaker."""
        return await self._circuit_breaker.call_async(self._generate, request)

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        """Generate embeddings through the circuit breaker."""
        return await self._circuit_breaker.call_async(self._embed, request)

    async def rerank(self, request: RerankRequest) -> RerankResponse:
        """Re-rank documents through the circuit breaker."""
        return await self._circuit_breaker.call_async(self._rerank, request)

    async def stream_generate(self, request: ChatRequest) -> AsyncIterator[ChatStreamChunk]:
        """Stream chat completion chunks through the circuit breaker."""
        # aiobreaker call_async currently doesn't natively support AsyncGenerators directly
        # depending on the version. Assuming it does, or we can wrap it.
        # Often circuit breakers just wrap the initial connection, but since it's an async generator:
        # If call_async fails for generator, we can do manual state checking.
        if self._circuit_breaker.current_state.name == "OPEN":
            raise aiobreaker.CircuitBreakerError("Circuit is OPEN")
        try:
            async for chunk in self._stream_generate(request):
                yield chunk
            self._circuit_breaker.succeed()
        except Exception as e:
            self._circuit_breaker.fail()
            raise e

    # ------------------------------------------------------------------
    # Abstract Provider Implementation Methods
    # ------------------------------------------------------------------

    @abstractmethod
    async def _generate(self, request: ChatRequest) -> ChatResponse:
        """Internal: Send a chat completion request."""
        ...

    @abstractmethod
    async def _embed(self, request: EmbedRequest) -> EmbedResponse:
        """Internal: Generate embeddings for the given input(s)."""
        ...

    @abstractmethod
    async def _rerank(self, request: RerankRequest) -> RerankResponse:
        """Internal: Re-rank a list of documents against a query."""
        ...

    @abstractmethod
    def _stream_generate(self, request: ChatRequest) -> AsyncIterator[ChatStreamChunk]:
        """Internal: Stream chat completion chunks as they arrive."""
        ...

    @abstractmethod
    async def health_check(self) -> HealthStatus:
        """Verify the provider endpoint is reachable and responsive."""
        ...

    # ------------------------------------------------------------------
    # Concrete Helpers
    # ------------------------------------------------------------------

    def _build_auth_headers(self, api_key: str) -> dict[str, str]:
        """Build authentication headers from the resolved API key.

        Override in subclasses for non-standard auth schemes (e.g. SigV4).
        """
        auth = self._static.auth
        header_name = auth.header_name or "Authorization"
        prefix = auth.header_prefix or "Bearer"
        return {header_name: f"{prefix} {api_key}"}

    def _emit_structured_log(
        self,
        operation: str,
        latency_ms: int,
        *,
        status_code: int = 200,
        retry_count: int = 0,
        usage: dict[str, int] | None = None,
        error_type: str | None = None,
    ) -> None:
        """Emit a structured log record for observability.

        Subclasses may enrich with provider-specific fields before calling super().
        """
        extra: dict[str, object] = {
            "provider_name": self._static.provider_name,
            "model_name": self._deployment.model_name,
            "operation": operation,
            "latency_ms": latency_ms,
            "status_code": status_code,
            "retry_count": retry_count,
            "error_type": error_type,
        }
        if usage:
            extra["usage"] = usage
        self._logger.info("Provider call completed", extra=extra)

    def _handle_provider_error(self, exc: Exception) -> ProviderError:
        """Map a provider-specific exception to a canonical ProviderError."""
        from app.providers.error_classifier import classify_error

        return classify_error(exc, provider_name=self._static.provider_name)

    def _effective_timeout(self) -> float:
        """Resolve the effective timeout: deployment override → provider default."""
        return self._deployment.timeout_seconds or self._static.default_timeout_seconds
