"""Versioned Redis storage for validated deployment routing configuration.

Architecture:
    CachedInferenceRoutingConfigReader -> DeploymentConfigCache -> RedisCache

Why wrap the deployment in a versioned envelope?
    Application releases can change ``DeploymentConfig`` while old JSON is
    still present in Redis. The schema number makes that incompatibility
    explicit. An unreadable or old entry is deleted, then the reader reloads
    the authoritative row from PostgreSQL and writes the current format.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.settings.models.tenant_config import DeploymentConfig

if TYPE_CHECKING:
    from app.adapters.inference_routing.contracts import DeploymentCacheBackend

logger = logging.getLogger(__name__)

CACHE_SCHEMA_VERSION: Literal[1] = 1


class DeploymentCacheEnvelope(BaseModel):
    """Describe the on-wire cache format independently of the domain model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = CACHE_SCHEMA_VERSION
    deployment: DeploymentConfig


class DeploymentConfigCache:
    """Read and write versioned deployment payloads on a best-effort basis."""

    def __init__(self, backend: DeploymentCacheBackend) -> None:
        """Wrap a byte-cache backend without taking ownership of its lifecycle."""
        self._backend = backend
        self._stats: defaultdict[str, int] = defaultdict(int)

    async def read(self, cache_key: str) -> DeploymentConfig | None:
        """Return a valid cached deployment, deleting incompatible payloads."""
        payload = await self._backend.get(cache_key)
        if payload is None:
            self._stats["miss"] += 1
            return None
        try:
            envelope = DeploymentCacheEnvelope.model_validate_json(payload)
        except ValidationError:
            self._stats["corrupt"] += 1
            logger.warning(
                "Deleting incompatible deployment routing cache entry",
                extra={"cache_key": cache_key},
            )
            if not await self._backend.delete(cache_key):
                self._stats["delete_failed"] += 1
                logger.warning(
                    "Could not delete incompatible deployment routing cache entry",
                    extra={"cache_key": cache_key},
                )
            return None
        self._stats["hit"] += 1
        return envelope.deployment

    async def write(self, cache_key: str, deployment: DeploymentConfig) -> None:
        """Store the current cache schema without failing the routed request."""
        envelope = DeploymentCacheEnvelope(deployment=deployment)
        cached = await self._backend.set(cache_key, envelope.model_dump_json().encode("utf-8"))
        self._stats["write_ok" if cached else "write_failed"] += 1
        if not cached:
            logger.warning(
                "Could not cache deployment routing configuration",
                extra={"cache_key": cache_key},
            )

    def stats(self) -> dict[str, int]:
        """Return a snapshot suitable for diagnostics and metrics export."""
        return dict(sorted(self._stats.items()))
