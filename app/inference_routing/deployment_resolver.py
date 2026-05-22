"""
Deployment Resolver
===================

Resolves a tenant deployment configuration from Redis, with a PostgreSQL
fallback for cold-start and post-invalidation scenarios.

Resolution order:
    1. Redis cache — sub-millisecond for the hot path.
    2. PostgreSQL (via DeploymentConfigReader) — on cache miss; result is
       re-populated into Redis so subsequent requests hit the cache.

Enterprise Pattern: Cache-Aside Pattern
    The resolver owns the read-through logic so callers never need to know
    whether the data came from cache or database.

Architecture decision:
    This module treats Redis as an optimization and PostgreSQL as the source
    of truth. Cache corruption is surfaced as configuration failure rather than
    silently ignored so data quality issues are visible early.

Author: Shubham Singh
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.core.exceptions import ConfigurationError, DeploymentInactiveError, DeploymentNotFoundError
from app.core.settings.models.tenant_config import DeploymentConfig

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from uuid import UUID

    from app.inference_routing.contracts import DeploymentConfigReader
    from app.infrastructure.redis_cache import RedisCache


class DeploymentResolver:
    """Resolves an active deployment config using a cache-aside strategy.

    On a cache miss the resolver falls back to PostgreSQL via the injected
    DeploymentConfigReader, then re-populates the cache so the next request
    is served from Redis.

    Why this class exists separately:
        Deployment retrieval has nuanced behavior (cache read-through, active
        status enforcement, repopulation). Encapsulating it keeps the pipeline
        focused on precedence, not storage mechanics.
    """

    def __init__(self, cache: RedisCache, db_reader: DeploymentConfigReader) -> None:
        self._cache = cache
        self._db_reader = db_reader

    async def resolve(
        self,
        tenant_id: UUID | str,
        deployment_key: str,
    ) -> DeploymentConfig:
        """Return an active DeploymentConfig or raise a canonical domain exception.

        Raises:
            DeploymentNotFoundError: The deployment key is absent from both cache and DB.
            DeploymentInactiveError: The deployment exists but is not active.
            ConfigurationError: The cached JSON failed Pydantic validation.

        Step-by-step:
            1. Try Redis using tenant/deployment cache key.
            2. Validate cached payload into DeploymentConfig.
            3. If cache miss, read from DB adapter.
            4. Enforce active status.
            5. Attempt cache repopulation for next request.
        """
        tenant_id_str = str(tenant_id)
        cache_key = f"tenant:{tenant_id_str}:deployments:{deployment_key}"

        raw_data = await self._cache.get(cache_key)
        if raw_data is not None:
            try:
                deployment = DeploymentConfig.model_validate_json(raw_data)
            except Exception as exc:
                logger.error(
                    "Corrupted deployment config in cache",
                    extra={
                        "tenant_id": tenant_id_str,
                        "deployment_key": deployment_key,
                        "error": str(exc),
                    },
                )
                raise ConfigurationError(
                    f"Deployment config for key {deployment_key!r} failed validation."
                ) from exc
            if not deployment.is_active:
                raise DeploymentInactiveError(
                    deployment_key=deployment.deployment_key,
                    status=deployment.status.value,
                )
            return deployment

        logger.warning(
            "Deployment cache miss — falling back to database",
            extra={"tenant_id": tenant_id_str, "deployment_key": deployment_key},
        )
        deployment = await self._db_reader.get_deployment_config(tenant_id_str, deployment_key)
        if deployment is None:
            raise DeploymentNotFoundError(tenant_id_str, deployment_key)

        if not deployment.is_active:
            raise DeploymentInactiveError(
                deployment_key=deployment.deployment_key,
                status=deployment.status.value,
            )

        # Repopulate the cache so the next request is served from Redis.
        try:
            await self._cache.set(cache_key, deployment.model_dump_json().encode("utf-8"))
        except Exception:
            logger.warning(
                "Failed to repopulate deployment cache after DB fallback",
                extra={"tenant_id": tenant_id_str, "deployment_key": deployment_key},
                exc_info=True,
            )

        return deployment

