"""
app/providers/registry.py — Thread-safe singleton cache of provider instances.

Architecture
------------
                    ┌───────────────────────────┐
                    │     ProviderRegistry       │
                    │   (Singleton per process)  │
                    │                           │
                    │  _providers: dict[str,    │
                    │              BaseProvider] │
                    │  _lock: asyncio.Lock       │
                    │                           │
                    │  + get_provider(t, d)     │
                    │  + invalidate(t, d)       │
                    │  - _build_provider(t, d)  │
                    │  - _resolve_class(name)   │
                    └───────────────────────────┘

Thread-Safety (per implementation_plan.md §7)
---------------------------------------------
- Dict reads are GIL-safe and do not require the lock (fast path).
- Dict writes acquire `asyncio.Lock` with double-checked locking (slow path).
- One provider instance per (tenant_id, deployment_id) cache key.
"""

from __future__ import annotations

import asyncio
import importlib
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from app.core.settings.loader import ConfigLoader
    from app.core.settings.models.tenant_config import DeploymentConfig
    from app.infrastructure.cache import RedisCache
    from app.infrastructure.http_client_factory import HTTPClientFactory
    from app.providers.base_provider import BaseProvider

from app.infrastructure.circuit_breaker import get_provider_circuit_breaker


class ProviderRegistry:
    """Thread-safe singleton cache of provider instances.

    One provider instance per (tenant_id, deployment_id).
    Uses double-checked locking via asyncio.Lock for safe creation.
    """

    def __init__(
        self,
        http_client_factory: HTTPClientFactory,
        config_loader: ConfigLoader,
        cache: RedisCache,
    ) -> None:
        self._providers: dict[str, BaseProvider] = {}
        self._lock = asyncio.Lock()
        self._http_client_factory = http_client_factory
        self._config_loader = config_loader
        self._cache = cache

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_provider(self, deployment_config: DeploymentConfig) -> BaseProvider:
        """Return a cached or newly-built provider for the given deployment.

        Fast-path read (no lock) for the common case where the provider is
        already cached. Falls back to double-checked locking for creation.
        """
        cache_key = f"{deployment_config.tenant_id}:{deployment_config.deployment_id}"

        # Fast path — no lock needed for reads (dict reads are GIL-safe)
        if cache_key in self._providers:
            return self._providers[cache_key]

        # Slow path — acquire lock, double-check, build
        async with self._lock:
            if cache_key in self._providers:
                return self._providers[cache_key]
            provider = await self._build_provider(deployment_config)
            self._providers[cache_key] = provider
            return provider

    async def invalidate(self, tenant_id: UUID, deployment_id: UUID) -> None:
        """Remove a cached provider so the next request rebuilds it.

        Called when settings changes propagate via Redis pub/sub event.
        """
        cache_key = f"{tenant_id}:{deployment_id}"
        async with self._lock:
            self._providers.pop(cache_key, None)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _build_provider(self, deployment_config: DeploymentConfig) -> BaseProvider:
        """Build and return a new provider instance.

        Steps:
        1. Load ProviderStaticConfig for the provider name.
        2. Resolve the provider implementation class.
        3. Instantiate and return.
        """
        static_config = self._config_loader.load_provider_config(
            deployment_config.provider_name
        )

        provider_class = self._resolve_implementation_class(static_config.implementation_class)

        http_client = self._http_client_factory.create_client(static_config.provider_type)
        circuit_breaker = await get_provider_circuit_breaker(
            static_config.provider_name, self._cache
        )

        # Inject resolved API key into deployment settings for this instance
        # (secret NEVER stored on the instance — only passed at call time via
        #  request.resolved_api_key, set by the dispatcher)
        return provider_class(
            static_config=static_config,
            deployment_config=deployment_config,
            http_client=http_client,
            circuit_breaker=circuit_breaker,
        )

    @staticmethod
    def _resolve_implementation_class(
        fully_qualified_name: str,
    ) -> type[BaseProvider]:
        """Dynamically import and return the provider class.

        Args:
            fully_qualified_name: e.g. "app.providers.direct.openai_provider.OpenAIProvider"

        Returns:
            The resolved class object (a concrete BaseProvider subclass).
        """
        module_path, class_name = fully_qualified_name.rsplit(".", 1)
        module = importlib.import_module(module_path)
        return getattr(module, class_name)
