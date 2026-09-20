"""Behavioral tests for circuit-breaker error translation in BaseProvider.

Regression coverage for the gap where ``aiobreaker.CircuitBreakerError``
inherits from ``Exception`` rather than ``LLMServiceError``, so it bypassed
domain-error translation and surfaced a working circuit breaker as an
untyped 500 instead of a 503.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import httpx
import pytest
from aiobreaker import CircuitBreaker

from app.api.exception_handlers import _INFERENCE_EXCEPTION_STATUS, _resolve_status
from app.core.exceptions import (
    LLMServiceError,
    ProviderCircuitOpenError,
    ProviderTimeoutError,
)
from app.providers.base_provider import BaseProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class _StubProvider(BaseProvider[object]):
    """Minimal concrete provider whose failure mode each test controls."""

    def __init__(self, circuit_breaker: CircuitBreaker, failure: Exception) -> None:
        self._circuit_breaker = circuit_breaker  # type: ignore[assignment]
        self._failure = failure
        self._static = _StubStatic()  # type: ignore[assignment]
        self._context = _StubContext()  # type: ignore[assignment]
        self._http_client = object()
        self._api_key = None  # type: ignore[assignment]
        self._logger = None  # type: ignore[assignment]

    async def _generate(self, request: object) -> object:  # type: ignore[override]
        raise self._failure

    async def _embed(self, request: object) -> object:  # type: ignore[override]
        raise self._failure

    async def _rerank(self, request: object) -> object:  # type: ignore[override]
        raise self._failure

    async def _stream_generate(self, request: object) -> AsyncIterator[object]:  # type: ignore[override]
        raise self._failure
        yield  # pragma: no cover - unreachable, marks this a generator

    async def health_check(self) -> object:  # type: ignore[override]
        raise NotImplementedError


class _StubStatic:
    """Stand-in for ProviderStaticConfig, supplying only the name used here."""

    provider_name = "openai"


class _StubContext:
    """Supply the resolved timeout used during raw error classification."""

    effective_timeout_seconds = 30.0


def _breaker(reset_seconds: int = 60) -> CircuitBreaker:
    """Return a breaker that opens after a single failure."""
    return CircuitBreaker(fail_max=1, timeout_duration=timedelta(seconds=reset_seconds))


@pytest.mark.asyncio
async def test_already_open_circuit_raises_domain_error_not_library_error() -> None:
    """REQ: a call arriving while the circuit is already open produces a typed
    domain error, not aiobreaker's untyped CircuitBreakerError.
    """
    breaker = _breaker()
    provider = _StubProvider(breaker, ProviderTimeoutError("openai", 30.0))

    # First call trips the breaker.
    with pytest.raises(LLMServiceError):
        await provider.generate(None)  # type: ignore[arg-type]

    # Second call is rejected by the open circuit without dialling out.
    with pytest.raises(ProviderCircuitOpenError) as exc_info:
        await provider.generate(None)  # type: ignore[arg-type]

    assert exc_info.value.provider_name == "openai"
    assert "circuit breaker is open" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_open_circuit_maps_to_503_not_500() -> None:
    """REQ: the breaker-open error resolves to 503 through the existing
    ProviderUnavailableError mapping, rather than the unmapped-type 500 default.
    """
    breaker = _breaker()
    provider = _StubProvider(breaker, ProviderTimeoutError("openai", 30.0))

    with pytest.raises(LLMServiceError):
        await provider.generate(None)  # type: ignore[arg-type]
    with pytest.raises(ProviderCircuitOpenError) as exc_info:
        await provider.generate(None)  # type: ignore[arg-type]

    assert _resolve_status(exc_info.value, _INFERENCE_EXCEPTION_STATUS) == 503


@pytest.mark.asyncio
async def test_open_circuit_carries_retry_after_hint() -> None:
    """REQ: the breaker's configured reset window is surfaced so callers can
    back off instead of retrying immediately into an open circuit.
    """
    breaker = _breaker(reset_seconds=45)
    provider = _StubProvider(breaker, ProviderTimeoutError("openai", 30.0))

    with pytest.raises(LLMServiceError):
        await provider.generate(None)  # type: ignore[arg-type]
    with pytest.raises(ProviderCircuitOpenError) as exc_info:
        await provider.generate(None)  # type: ignore[arg-type]

    assert exc_info.value.retry_after_seconds == 45


@pytest.mark.asyncio
async def test_tripping_call_preserves_the_real_underlying_cause() -> None:
    """REQ: the call that trips the breaker reports what actually went wrong.

    Adapters classify transport failures before the breaker sees them, so the
    tripping call's __cause__ is already a precise domain error. Reporting that
    request as merely "circuit open" would lose the real reason it failed.
    """
    breaker = _breaker()
    provider = _StubProvider(breaker, ProviderTimeoutError("openai", 30.0))

    with pytest.raises(ProviderTimeoutError):
        await provider.generate(None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_raw_transport_timeout_is_normalized_at_shared_boundary() -> None:
    """Every adapter receives domain-safe timeout mapping even if it forgets a catch."""
    provider = _StubProvider(_breaker(), httpx.ReadTimeout("upstream stalled"))

    with pytest.raises(ProviderTimeoutError):
        await provider.generate(None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_streaming_path_translates_open_circuit_too() -> None:
    """REQ: the streaming path carries errors across a queue to the consumer,
    so it needs the same translation as the non-streaming path — otherwise a
    stream request during an outage still surfaces an untranslated 500.
    """
    breaker = _breaker()
    provider = _StubProvider(breaker, ProviderTimeoutError("openai", 30.0))

    with pytest.raises(LLMServiceError):
        async for _ in provider.stream_generate(None):  # type: ignore[arg-type]
            pass

    with pytest.raises(ProviderCircuitOpenError):
        async for _ in provider.stream_generate(None):  # type: ignore[arg-type]
            pass


def test_reset_hint_degrades_to_none_when_breaker_lacks_timeout() -> None:
    """REQ: reading the reset window must never raise.

    aiobreaker's ``time_until_open`` raises TypeError while ``opens_at`` is
    unset — exactly when the circuit has just opened. A crash while building
    the 503 would produce the very 500 this translation exists to prevent, so
    an unreadable value degrades to "no hint".
    """

    class _BreakerWithoutTimeout:
        pass

    provider = _StubProvider(_breaker(), ProviderTimeoutError("openai", 30.0))
    provider._circuit_breaker = _BreakerWithoutTimeout()  # type: ignore[assignment]

    assert provider._breaker_reset_seconds() is None
