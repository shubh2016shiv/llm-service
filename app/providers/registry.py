"""Build and temporarily cache provider adapters for resolved routes.

Architecture:
    InferenceService -> ProviderRegistry -> provider adapter
                                      |-> transport factory
                                      |-> circuit-breaker registry
                                      '-> secret store

Provider objects contain a ``SecretStr`` with plaintext credential material.
The cache is therefore both size-bounded and time-bounded: TTL makes secret
rotation visible without a process restart, while LRU eviction prevents route
cardinality from becoming unbounded memory growth.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from pydantic import SecretStr

from app.core.exceptions import ConfigurationError
from app.core.settings.models.provider_config import AuthMode, ProviderImplementation
from app.providers.cloud.azure_openai_provider import AzureOpenAIProvider
from app.providers.cloud.bedrock_provider import BedrockProvider
from app.providers.direct.anthropic_provider import AnthropicProvider
from app.providers.direct.openai_provider import OpenAIProvider
from app.providers.direct.vllm_provider import VLLMProvider

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.adapters.provider_transport import (
        ProviderCircuitBreakerRegistry,
        ProviderTransportFactory,
    )
    from app.adapters.secret_management import SecretStore
    from app.inference_routing.models import ResolvedRoute
    from app.providers.base_provider import BaseProvider

ProviderClass = type["BaseProvider[Any]"]

_BUILT_IN_PROVIDER_CLASSES: dict[ProviderImplementation, ProviderClass] = {
    ProviderImplementation.OPENAI: OpenAIProvider,
    ProviderImplementation.ANTHROPIC: AnthropicProvider,
    ProviderImplementation.VLLM: VLLMProvider,
    ProviderImplementation.AZURE_OPENAI: AzureOpenAIProvider,
    ProviderImplementation.BEDROCK: BedrockProvider,
}


@dataclass(frozen=True, slots=True)
class _ProviderCacheEntry:
    """Pair a provider instance with the monotonic time it must be rebuilt."""

    provider: BaseProvider[Any]
    expires_at: float


class ProviderRegistry:
    """Coalesce construction and retain a bounded LRU of provider instances."""

    def __init__(
        self,
        transport_factory: ProviderTransportFactory,
        circuit_breaker_registry: ProviderCircuitBreakerRegistry,
        secret_store: SecretStore,
        *,
        cache_ttl_seconds: float,
        max_cached_providers: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize explicit dependencies and cache safety limits."""
        if cache_ttl_seconds <= 0:
            raise ValueError("cache_ttl_seconds must be greater than zero")
        if max_cached_providers < 1:
            raise ValueError("max_cached_providers must be at least one")
        self._providers: OrderedDict[str, _ProviderCacheEntry] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[BaseProvider[Any]]] = {}
        self._lock = asyncio.Lock()
        self._transport_factory = transport_factory
        self._circuit_breaker_registry = circuit_breaker_registry
        self._secret_store = secret_store
        self._cache_ttl_seconds = cache_ttl_seconds
        self._max_cached_providers = max_cached_providers
        self._clock = clock

    async def get_provider(self, context: ResolvedRoute) -> BaseProvider[Any]:
        """Return a fresh cached provider or share one in-progress construction."""
        cache_key = context.route_fingerprint
        async with self._lock:
            cached = self._read_fresh_entry(cache_key)
            if cached is not None:
                return cached
            task = self._inflight.get(cache_key)
            if task is None:
                task = asyncio.create_task(
                    self._build_and_cache(cache_key, context),
                    name=f"provider-build:{cache_key[:12]}",
                )
                self._inflight[cache_key] = task
                task.add_done_callback(self._observe_build_result)
        return await asyncio.shield(task)

    async def invalidate(self, route_fingerprint: str) -> None:
        """Evict one route so its next call re-reads the credential."""
        async with self._lock:
            self._providers.pop(route_fingerprint, None)

    async def clear(self) -> None:
        """Drop every cached provider during controlled operational refreshes."""
        async with self._lock:
            self._providers.clear()

    def _read_fresh_entry(self, cache_key: str) -> BaseProvider[Any] | None:
        """Return and promote a fresh LRU entry; remove an expired one."""
        entry = self._providers.get(cache_key)
        if entry is None:
            return None
        if entry.expires_at <= self._clock():
            self._providers.pop(cache_key, None)
            return None
        self._providers.move_to_end(cache_key)
        return entry.provider

    async def _build_and_cache(
        self,
        cache_key: str,
        context: ResolvedRoute,
    ) -> BaseProvider[Any]:
        """Build once, publish a fresh cache entry, and clear the ticket."""
        try:
            provider = await self._build_provider(context)
        except Exception:
            async with self._lock:
                self._inflight.pop(cache_key, None)
            raise
        async with self._lock:
            self._inflight.pop(cache_key, None)
            self._providers[cache_key] = _ProviderCacheEntry(
                provider=provider,
                expires_at=self._clock() + self._cache_ttl_seconds,
            )
            self._providers.move_to_end(cache_key)
            while len(self._providers) > self._max_cached_providers:
                self._providers.popitem(last=False)
        return provider

    @staticmethod
    def _observe_build_result(task: asyncio.Task[BaseProvider[Any]]) -> None:
        """Mark background failures observed if the original caller disconnects."""
        if not task.cancelled():
            task.exception()

    async def _build_provider(self, context: ResolvedRoute) -> BaseProvider[Any]:
        """Construct one adapter from validated route dependencies."""
        provider_class = self._resolve_implementation_class(
            context.provider_static_config.implementation_class
        )
        transport = self._transport_factory.create_transport(
            context.provider_static_config.provider_type
        )
        breaker = await self._circuit_breaker_registry.get_breaker(context.provider_name)
        api_key = await self._read_api_key(context)
        return provider_class(
            context=context,
            http_client=transport,
            circuit_breaker=breaker,
            api_key=api_key,
        )

    async def _read_api_key(self, context: ResolvedRoute) -> SecretStr | None:
        """Fetch explicit credentials only for auth modes that require them."""
        auth_mode = context.provider_static_config.auth.mode
        if auth_mode in {AuthMode.AWS_SIGV4, AuthMode.NONE}:
            return None
        plaintext = await self._secret_store.get_secret(
            context.secret_reference,
            tenant_id=str(context.tenant_id),
        )
        return SecretStr(plaintext)

    @staticmethod
    def _resolve_implementation_class(
        implementation: ProviderImplementation,
    ) -> ProviderClass:
        """Resolve only audited built-in adapters, never arbitrary import paths."""
        provider_class = _BUILT_IN_PROVIDER_CLASSES.get(implementation)
        if provider_class is None:
            raise ConfigurationError(
                f"Provider implementation {implementation!r} is not registered."
            )
        return cast("ProviderClass", provider_class)
