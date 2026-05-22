"""
Base Provider Contract
======================

This module defines the shared contract for every provider adapter in the
system.

Why this module exists
----------------------
Each provider speaks a different dialect. One may use a bearer token, another
may use an API key header, and another may use the cloud SDK credential chain.
If every service knew those details, the code would be harder to read and much
harder to change.

The base class keeps the rest of the application simple by giving every
provider the same public operations: `generate`, `embed`, `rerank`,
`stream_generate`, and `health_check`. The service layer can call those methods
without caring which provider is underneath.

Why the design matters
----------------------
The circuit breaker lives here so resilience is consistent. That means a
provider outage is handled in the same way no matter which provider failed.
This matters because operators should debug one failure pattern, not five.

The class is immutable after construction. That prevents one request from
accidentally changing state that another request is still using.

Example
-------
An inference request arrives from the API layer. The service asks the provider
for a completion and does not need to know whether the answer comes from
OpenAI, Anthropic, Azure OpenAI, or Bedrock:

    provider.generate(request)

The base class makes that possible by handling the common rules once and
leaving only the provider-specific translation to the subclass.

How to read this file
---------------------
Think of this class as the shared chapter in a book:

    - public methods are the safe entry points
    - private methods hold provider-specific implementation details
    - shared logging happens in one place
    - shared error translation happens in one place

Enterprise Pattern: Template Method + Resilience Boundary
    The base class defines the workflow, and subclasses fill in the provider
    specific pieces.

Step-by-step execution boundary:
    1. Registry injects resolved context, transport, breaker, and credential.
    2. Public methods call provider-specific implementations through breaker.
    3. Provider-specific methods translate payloads and parse responses.
    4. Shared helpers emit structured logs and normalize provider errors.
    5. Upstream services receive stable schema contracts.

Author: Shubham Singh
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from pydantic import SecretStr

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Coroutine

    import aiobreaker

    from app.core.exceptions import ProviderError
    from app.core.settings.models.provider_config import ProviderStaticConfig
    from app.inference_routing.models import ResolvedExecutionContext
    from app.schemas.requests_schema import ChatRequest, EmbedRequest, RerankRequest
    from app.schemas.responses_schema import (
        ChatResponse,
        ChatStreamChunk,
        EmbedResponse,
        HealthStatus,
        RerankResponse,
    )


# --- Internal stream-signalling types ---
#
# stream_generate() works by running the provider's async generator inside a
# background task (the "producer") and passing chunks to the caller through an
# asyncio.Queue. The queue must be able to carry three completely different
# things: real data chunks, an error signal, and a "finished" signal. To do
# that safely we use a discriminated union — three distinct types so that an
# isinstance() check on every item pulled from the queue can tell them apart
# with zero ambiguity.
#
# Why not use None as the "finished" sentinel?
#   None is a valid Python value that could appear anywhere, and the type
#   checker cannot distinguish "this None is a deliberate sentinel" from "this
#   None is accidental data." Mixing None into a typed queue also forces
#   Optional everywhere downstream, which is noise. A dedicated class is
#   unambiguous: nothing can accidentally be a _StreamComplete.
#
# Why not use a plain string like "DONE"?
#   Strings cannot be used with isinstance() to discriminate a union. You would
#   need equality checks (`if item == "DONE"`), which are fragile and bypass the
#   type checker entirely. The type checker would not know that after the check
#   `item` can only be a ChatStreamChunk.
#
# _StreamError — carries the exception from the producer task back to the
#   caller. Implemented as a frozen dataclass because it wraps real data (the
#   exception object) that must be carried across the queue boundary.
#
# _StreamComplete — a pure "end of stream, no error" signal. It carries no
#   data at all, so a minimal class with `pass` is correct. Using @dataclass
#   here would be misleading — it implies structured data where there is none.
#
# _STREAM_COMPLETE — a single pre-created instance of _StreamComplete that is
#   reused every time a stream finishes. Creating a new _StreamComplete() on
#   every request would work but is wasteful. A module-level singleton is the
#   standard Python pattern for sentinels (the stdlib uses `_MISSING = object()`
#   for the same reason). The consumer checks `isinstance(item, _StreamComplete)`
#   rather than identity (`is _STREAM_COMPLETE`) so the code reads as a clean
#   type-based dispatch, consistent with how _StreamError is handled.

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

    Generic parameter — TransportT:
        The `[TransportT]` bracket after the class name is Python 3.12 syntax
        for declaring a generic class. It means: "this class has one type
        parameter called TransportT that will be filled in concretely by each
        subclass." Think of it like a placeholder that says what kind of HTTP
        transport client this provider uses.

        For example:
            - OpenAIProvider extends BaseProvider[httpx.AsyncClient]
              → TransportT is resolved to httpx.AsyncClient
            - BedrockProvider extends BaseProvider[object]
              → TransportT is resolved to object (aioboto3 session)

        This lets the type checker verify that self._http_client is used
        correctly in each subclass without forcing every provider to share
        the same concrete transport type.

    Architecture decision:
        Keep the orchestration contract (`generate`, `embed`, etc.) in this
        base class, and keep wire-format specifics in subclasses. That split
        avoids duplicate resilience and logging logic across providers.
    """

    def __init__(
        self,
        context: ResolvedExecutionContext,
        http_client: TransportT,
        circuit_breaker: aiobreaker.CircuitBreaker,
        api_key: SecretStr | None = None,
    ) -> None:
        self._context: ResolvedExecutionContext = context
        self._static: ProviderStaticConfig = context.provider_static_config
        self._http_client: TransportT = http_client
        self._circuit_breaker: aiobreaker.CircuitBreaker = circuit_breaker
        self._api_key: SecretStr = api_key if api_key is not None else SecretStr("")
        self._logger: logging.Logger = logging.getLogger(self.__class__.__module__)

    # ------------------------------------------------------------------
    # Public Execution Methods (Wrapped with Circuit Breaker)
    # ------------------------------------------------------------------

    async def generate(self, request: ChatRequest) -> ChatResponse:
        """Execute non-streaming chat generation through resilience boundary.

        Subclass implementation performs provider-specific I/O in ``_generate``.
        This wrapper guarantees breaker policy is consistently applied.
        """
        return await self._call_with_breaker(self._generate, request)

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        """Execute embedding request through shared breaker wrapper."""
        return await self._call_with_breaker(self._embed, request)

    async def rerank(self, request: RerankRequest) -> RerankResponse:
        """Execute rerank request through shared breaker wrapper."""
        return await self._call_with_breaker(self._rerank, request)

    async def stream_generate(self, request: ChatRequest) -> AsyncIterator[ChatStreamChunk]:
        """Stream chat completion chunks through the circuit breaker.

        aiobreaker guards coroutines, not async generators. To preserve normal
        breaker semantics for stream failures and half-open trial calls, a
        producer coroutine consumes the provider stream under call_async while
        this method yields chunks to the caller through a bounded queue.

        Rationale:
            This design preserves stream backpressure and error propagation
            while still counting stream failures as breaker-visible failures.
        """
        queue: asyncio.Queue[ChatStreamChunk | _StreamError | _StreamComplete] = asyncio.Queue(
            maxsize=1
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
        """Call a coroutine through aiobreaker while preserving its return type.

        Generic parameter — ResponseT:
            The `[ResponseT]` bracket after the method name is Python 3.12 syntax
            for declaring a generic method. It works like a placeholder that says:
            "whatever return type the caller passes in as `func`, this method will
            return that same type." ResponseT is NOT a fixed class defined somewhere
            — it is created fresh at this method definition and lives only within
            this method's scope.

            For example, when called as:
                self._call_with_breaker(self._generate, request)

            `func` is `self._generate`, which returns ChatResponse. The type
            checker substitutes ResponseT = ChatResponse for that specific call,
            so the return type of _call_with_breaker is also ChatResponse. The
            next call with `self._embed` (which returns EmbedResponse) gets
            ResponseT = EmbedResponse independently. This is how one method can
            serve all operation types without losing type safety.
        """
        # Why cast(ResponseT, ...) is required here — not a bandaid, a deliberate workaround:
        #
        # At runtime, aiobreaker.CircuitBreaker.call_async is a transparent pass-through:
        # it receives a coroutine function, runs it according to the circuit breaker rules
        # (open / half-open / closed), and returns whatever that function returned — nothing
        # more. So if `func` produces a ChatResponse, call_async also hands back a ChatResponse.
        #
        # The problem is on the type-checking side. Third-party libraries ship "type stubs"
        # (*.pyi files) that tell Python's type checker (e.g. mypy, pyright) what types a
        # function accepts and returns. aiobreaker's stubs declare call_async as:
        #
        #     async def call_async(self, func: Callable[..., Coroutine], *args, **kwargs)
        #
        # There is no return type declared. The type checker therefore has no way to figure
        # out on its own that "if you pass a function returning ResponseT, call_async also
        # returns ResponseT." It treats the return as `Any` — a special type that silently
        # turns off type checking for anything downstream.
        #
        # cast(ResponseT, value) is the standard Python tool for exactly this situation.
        # It tells the type checker: "we know from reading aiobreaker's source that the
        # return is always whatever `func` returns; treat it as ResponseT." At runtime,
        # cast() is a complete no-op — it returns its second argument unchanged, with zero
        # conversion, zero checking, zero overhead. It exists solely so the type checker
        # keeps the return type correct through this call boundary.
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

    def _build_auth_headers(self) -> dict[str, str]:
        """Build authentication headers using the provider's stored API key.

        Reads the plaintext only at this call site — it never exists in a
        request object, a log record, or any other serialisable structure.

        Override in subclasses that use a non-Bearer auth scheme (e.g. Anthropic,
        Azure, AWS SigV4).

        Security rationale:
            Secret value is materialized only when constructing outbound headers
            and is not persisted on request payload objects.
        """
        auth = self._static.auth
        header_name = auth.header_name or "Authorization"
        prefix = auth.header_prefix or "Bearer"
        return {header_name: f"{prefix} {self._api_key.get_secret_value()}"}

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

        Why centralized:
            Keeping shared telemetry shape in one method makes dashboards and
            alert queries consistent across providers.
        """
        extra: dict[str, object] = {
            "provider_name": self._context.provider_name,
            "model_name": self._context.model_name,
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
        """Normalize provider-specific exceptions to canonical domain errors.

        This is the anti-corruption boundary between transport/SDK exceptions
        and internal service error contracts.
        """
        from app.providers.http_errors import classify_error

        return classify_error(exc, provider_name=self._static.provider_name)

    def _effective_timeout(self) -> float:
        """Return the pre-resolved timeout from the execution context."""
        return self._context.effective_timeout_seconds
