"""
Deployment Resolver
===================

Resolves a tenant deployment configuration from Redis and enforces deployment
active status before inference continues.

Enterprise Pattern: Cache-First Resolver Pattern
    Runtime path reads pre-warmed config from Redis for low-latency routing.

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

    from app.infrastructure.redis_cache import RedisCache


class DeploymentResolver:
    """Resolves an active deployment config from the Redis cache."""

    def __init__(self, cache: RedisCache) -> None:
        self._cache = cache

    async def resolve(
        self,
        tenant_id: UUID | str,
        deployment_key: str,
    ) -> DeploymentConfig:
        """Return an active DeploymentConfig or raise a canonical domain exception.

        Raises:
            DeploymentNotFoundError: The deployment key is absent from Redis.
            DeploymentInactiveError: The deployment exists but is not active.
        """
        tenant_id_str = str(tenant_id)
        cache_key = f"tenant:{tenant_id_str}:deployments:{deployment_key}"

        raw_data = await self._cache.get(cache_key)
        if raw_data is None:
            logger.warning(
                "Deployment cache miss",
                extra={"tenant_id": tenant_id_str, "deployment_key": deployment_key},
            )
            raise DeploymentNotFoundError(tenant_id_str, deployment_key)

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

