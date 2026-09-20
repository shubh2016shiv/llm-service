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

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, cast

from aiobreaker import CircuitBreakerError
from pydantic import SecretStr

from app.core.exceptions import LLMServiceError, ProviderCircuitOpenError
from app.providers.circuit_breaker_stream import CircuitBreakerStream
from app.providers.http_errors import classify_error

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Coroutine

    import aiobreaker

    from app.core.exceptions import ProviderError
    from app.core.settings.models.provider_config import ProviderStaticConfig
    from app.inference_routing.models import ResolvedRoute
    from app.schemas.requests_schema import ChatRequest, EmbedRequest, RerankRequest
    from app.schemas.responses_schema import (
        ChatResponse,
        ChatStreamChunk,
        EmbedResponse,
        HealthStatus,
        RerankResponse,
    )


class BaseProvider[TransportT](ABC):
    """Apply one execution/resilience contract to every provider adapter.

    ``TransportT`` preserves the concrete borrowed transport type: REST
    adapters use ``httpx.AsyncClient`` while Bedrock uses an SDK session.
    Instances contain route configuration and a credential but never mutable
    per-request data; payload and response variables stay in each call frame.
    """

    def __init__(
        self,
        context: ResolvedRoute,
        http_client: TransportT,
        circuit_breaker: aiobreaker.CircuitBreaker,
        api_key: SecretStr | None = None,
    ) -> None:
        self._context: ResolvedRoute = context
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
        stream = CircuitBreakerStream(
            source=self._stream_generate(request),
            circuit_breaker=self._circuit_breaker,
            translate_circuit_error=self._circuit_open_error,
            translate_error=self._normalize_provider_error,
        )
        async for chunk in stream.iterate():
            yield chunk

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
        try:
            return cast("ResponseT", await self._circuit_breaker.call_async(func, *args))
        except CircuitBreakerError as exc:
            raise self._circuit_open_error(exc) from exc
        except LLMServiceError:
            raise
        except Exception as exc:
            raise self._handle_provider_error(exc) from exc

    def _circuit_open_error(self, exc: CircuitBreakerError) -> LLMServiceError:
        """Translate aiobreaker's library error into a domain error.

        ``CircuitBreakerError`` inherits from ``Exception``, not
        ``LLMServiceError``, so it bypasses this service's domain-error
        translation entirely and reaches the top-level safety net as an
        untyped failure — reporting a working circuit breaker as a 500.

        Two distinct situations arrive here, and they deserve different answers:

        1. The call that *trips* the breaker. aiobreaker raises
           ``CircuitBreakerError`` from the underlying failure, and because
           provider adapters classify their own transport errors before the
           breaker ever sees them, ``__cause__`` is already a precise domain
           error (a timeout, a 502, a rejected credential). That real cause is
           more informative than "circuit open", so it is preserved — this
           request genuinely did time out or get rejected.
        2. A call arriving while the circuit is *already* open. Nothing was
           dialled and there is no underlying cause, so the honest answer is
           ``ProviderCircuitOpenError`` (503) with a retry hint.
        """
        underlying_cause = exc.__cause__
        if isinstance(underlying_cause, LLMServiceError):
            return underlying_cause
        if isinstance(underlying_cause, Exception):
            return self._handle_provider_error(underlying_cause)
        return ProviderCircuitOpenError(
            provider_name=self._static.provider_name,
            retry_after_seconds=self._breaker_reset_seconds(),
        )

    def _breaker_reset_seconds(self) -> int | None:
        """Return the breaker's configured reset window, in whole seconds.

        Deliberately reads the static ``timeout_duration`` rather than the
        breaker's ``time_until_open``: that property computes
        ``opens_at - now()`` and raises ``TypeError`` while ``opens_at`` is
        unset, which is exactly the moment the circuit has just opened. A
        crash here would convert the 503 this method exists to produce back
        into the 500 it exists to prevent, so the static value is used and
        anything unexpected degrades to "no hint" rather than raising.
        """
        timeout_duration = getattr(self._circuit_breaker, "timeout_duration", None)
        total_seconds = getattr(timeout_duration, "total_seconds", None)
        if total_seconds is None:
            return None
        seconds = int(total_seconds())
        return seconds if seconds > 0 else None

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
        return classify_error(
            exc,
            provider_name=self._static.provider_name,
            timeout_seconds=self._effective_timeout(),
        )

    def _normalize_provider_error(self, exc: Exception) -> Exception:
        """Preserve domain failures and classify raw adapter/transport errors."""
        if isinstance(exc, LLMServiceError):
            return exc
        return self._handle_provider_error(exc)

    @staticmethod
    def _safe_health_error_detail(exc: Exception) -> str:
        """Describe a failed health probe without echoing URLs or credentials."""
        return f"Provider health probe failed ({exc.__class__.__name__})."

    def _effective_timeout(self) -> float:
        """Return the pre-resolved timeout from the execution context."""
        return self._context.effective_timeout_seconds
