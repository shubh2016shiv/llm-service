"""
Provider Registry
=================

Builds and caches provider instances keyed by resolved route fingerprint.

Why this module exists:
    - Provider construction may involve dynamic import, transport setup, breaker
      wiring, and secret lookup. Repeating this per request is expensive.
    - A stable cache per route keeps latency lower and reduces object churn.
    - Concurrency-safe creation avoids duplicate providers under burst traffic.

Rationale for design choices:
    - Fast path: lock-free dict read for common cache hits.
    - Slow path: double-checked locking for safe one-time creation.
    - Fingerprint keying ensures cache separation when provider/model/endpoint/
      credential reference changes.

Enterprise Pattern: Singleton Registry + Double-Checked Locking

Author: Shubham Singh
"""

from __future__ import annotations

import asyncio
import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.inference_routing.models import ResolvedExecutionContext
    from app.infrastructure.http_client_factory import HTTPClientFactory
    from app.infrastructure.redis_cache import RedisCache
    from app.infrastructure.secret_store import SecretStore
    from app.providers.base_provider import BaseProvider

from pydantic import SecretStr

from app.infrastructure.provider_circuit_breaker import get_provider_circuit_breaker


class ProviderRegistry:
    """Thread-safe singleton cache of provider instances.

    One provider instance per unique route fingerprint (provider + model + endpoint +
    credential scope). Uses double-checked locking via asyncio.Lock for safe creation.
    """

    def __init__(
        self,
        http_client_factory: HTTPClientFactory,
        cache: RedisCache,
        secret_store: SecretStore,
    ) -> None:
        self._providers: dict[str, BaseProvider[Any]] = {}
        self._lock = asyncio.Lock()
        self._http_client_factory = http_client_factory
        self._cache = cache
        self._secret_store = secret_store

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_provider(self, context: ResolvedExecutionContext) -> BaseProvider[Any]:
        """Return a cached or newly-built provider for the given execution context.

        Fast-path read (no lock) for the common case where the provider is
        already cached. Falls back to double-checked locking for creation.
        """
        cache_key = context.route_fingerprint

        # Fast path — no lock needed for reads (dict reads are GIL-safe)
        if cache_key in self._providers:
            return self._providers[cache_key]

        # Slow path — acquire lock, double-check, build
        async with self._lock:
            if cache_key in self._providers:
                return self._providers[cache_key]
            provider = await self._build_provider(context)
            self._providers[cache_key] = provider
            return provider

    async def invalidate(self, route_fingerprint: str) -> None:
        """Remove a cached provider so the next request rebuilds it.

        Called when settings changes propagate via Redis pub/sub event.
        The route_fingerprint is the same SHA-256 digest stored on ResolvedExecutionContext.
        """
        async with self._lock:
            self._providers.pop(route_fingerprint, None)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _build_provider(self, context: ResolvedExecutionContext) -> BaseProvider[Any]:
        """Build and return a new provider instance from the resolved execution context.

        The provider_static_config, implementation_class, and secret_reference are
        all pre-resolved by the OrchestrationPipeline — no additional config lookups needed.
        """
        provider_class = self._resolve_implementation_class(
            context.provider_static_config.implementation_class
        )
        http_client = self._http_client_factory.create_client(
            context.provider_static_config.provider_type
        )
        circuit_breaker = await get_provider_circuit_breaker(context.provider_name, self._cache)

        plaintext_api_key = await self._secret_store.get_secret(
            context.secret_reference,
            tenant_id=str(context.tenant_config.tenant_id),
        )

        return provider_class(
            context=context,
            http_client=http_client,
            circuit_breaker=circuit_breaker,
            api_key=SecretStr(plaintext_api_key),
        )

    @staticmethod
    def _resolve_implementation_class(
        fully_qualified_name: str,
    ) -> type[BaseProvider[Any]]:
        """Dynamically import and return the provider class.

        Args:
            fully_qualified_name: e.g. "app.providers.direct.openai_provider.OpenAIProvider"

        Returns:
            The resolved class object (a concrete BaseProvider subclass).
        """
        module_path, class_name = fully_qualified_name.rsplit(".", 1)
        module = importlib.import_module(module_path)
        return getattr(module, class_name)

