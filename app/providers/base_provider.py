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
- Immutable after construction — all state is settings + shared transport.
- All methods are pure functions over: request payload + frozen settings + shared transport.
- NEVER store per-request or per-tenant state on the instance.
- Thread-safe: per-request variables are local to each call frame.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Coroutine

    import aiobreaker

    from app.core.exceptions import ProviderError
    from app.core.settings.models.provider_config import ProviderStaticConfig
    from app.core.settings.models.tenant_config import DeploymentConfig
    from app.schemas.requests import ChatRequest, EmbedRequest, RerankRequest
    from app.schemas.responses import (
        ChatResponse,
        ChatStreamChunk,
        EmbedResponse,
        HealthStatus,
        RerankResponse,
    )


@dataclass(frozen=True)
class _StreamError:
    exception: Exception


class _StreamComplete:
    pass


_STREAM_COMPLETE = _StreamComplete()


class BaseProvider[TransportT](ABC):
    """Abstract contract for all LLM providers.

    Immutable after construction. All methods are pure functions over:
      request payload + frozen settings + shared transport.

    Never store per-request or per-tenant state on the instance.
    """

    def __init__(
        self,
        static_config: ProviderStaticConfig,
        deployment_config: DeploymentConfig,
        http_client: TransportT,
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
        return await self._call_with_breaker(self._generate, request)

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        """Generate embeddings through the circuit breaker."""
        return await self._call_with_breaker(self._embed, request)

    async def rerank(self, request: RerankRequest) -> RerankResponse:
        """Re-rank documents through the circuit breaker."""
        return await self._call_with_breaker(self._rerank, request)

    async def stream_generate(self, request: ChatRequest) -> AsyncIterator[ChatStreamChunk]:
        """Stream chat completion chunks through the circuit breaker.

        aiobreaker guards coroutines, not async generators. To preserve normal
        breaker semantics for stream failures and half-open trial calls, a
        producer coroutine consumes the provider stream under call_async while
        this method yields chunks to the caller through a bounded queue.
        """
        queue: asyncio.Queue[ChatStreamChunk | _StreamError | _StreamComplete] = (
            asyncio.Queue(maxsize=1)
        )

        async def _consume_stream() -> None:
            async for chunk in self._stream_generate(request):
                await queue.put(chunk)

        async def _run_guarded_stream() -> None:
            try:
                await self._circuit_breaker.call_async(_consume_stream)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await queue.put(_StreamError(exc))
            else:
                await queue.put(_STREAM_COMPLETE)

        producer = asyncio.create_task(_run_guarded_stream())

        try:
            while True:
                item = await queue.get()
                if isinstance(item, _StreamComplete):
                    break
                if isinstance(item, _StreamError):
                    raise item.exception
                yield item
        finally:
            if not producer.done():
                producer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await producer

        await producer

    async def _call_with_breaker[ResponseT](
        self,
        func: Callable[..., Coroutine[object, object, ResponseT]],
        *args: object,
    ) -> ResponseT:
        """Call a coroutine through aiobreaker while preserving its return type."""
        # cast: aiobreaker.call_async is not generic in its distributed stubs,
        # but the runtime returns exactly the value produced by `func`.
        return cast("ResponseT", await self._circuit_breaker.call_async(func, *args))

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
