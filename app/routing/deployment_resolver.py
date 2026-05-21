"""
Deployment Resolver
===================

Resolves tenant deployment configuration from the shared Redis cache.

Architecture:
-------------
    Request → DeploymentResolver.resolve(tenant_id, deployment_key)
        │
        ├── RedisCache GET tenant:{tenant_id}:deployments:{deployment_key}
        │   └── IF MISS: fallback to DB (omitted here, handled by config loader microservice)
        │
        └── returns DeploymentConfig
Dependencies:
    - app.infrastructure.cache — Redis cache adapter
    - app.core.settings.models.tenant_config — DeploymentConfig

Author: Engineering Team
Last Updated: 2026-05-16
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from app.core.exceptions import LLMServiceError
from app.core.settings.models.tenant_config import DeploymentConfig

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.infrastructure.redis_cache import RedisCache


class DeploymentNotFoundError(LLMServiceError):
    """Raised when the requested deployment cannot be found for the tenant."""

    def __init__(self, tenant_id: UUID, deployment_key: str) -> None:
        super().__init__(
            message=f"Deployment '{deployment_key}' not found for tenant '{tenant_id}'.",
        )
        self.tenant_id = tenant_id
        self.deployment_key = deployment_key


class DeploymentResolver:
    """Resolves deployment configurations from Redis.
    
    In the enterprise architecture, the unified configuration microservice pushes
    tenant and deployment configs to Redis. The LLM Gateway simply reads them.
    """

    def __init__(self, cache: RedisCache) -> None:
        self._cache = cache

    async def resolve(
        self, tenant_id: UUID | str, deployment_key: str
    ) -> DeploymentConfig:
        """Resolve a DeploymentConfig from Redis cache.

        Args:
            tenant_id: The UUID of the tenant.
            deployment_key: The URL-safe deployment identifier.

        Returns:
            The resolved DeploymentConfig.

        Raises:
            DeploymentNotFoundError: If the config is missing from Redis.
            ValueError: If the config fails Pydantic validation.
        """
        tenant_id_str = str(tenant_id)
        cache_key = f"tenant:{tenant_id_str}:deployments:{deployment_key}"
        
        raw_data = await self._cache.get(cache_key)
        if raw_data is None:
            # Note: In a complete implementation, this might trigger an async 
            # fetch from PostgreSQL via the ConfigLoader if missing.
            # For the inference critical path, we assume it's pre-warmed.
            logger.warning(
                "Deployment cache miss", 
                extra={"tenant_id": tenant_id_str, "deployment_key": deployment_key}
            )
            raise DeploymentNotFoundError(UUID(tenant_id_str), deployment_key)
            
        try:
            return DeploymentConfig.model_validate_json(raw_data)
        except Exception as e:
            logger.error(
                "Invalid deployment config in cache", 
                extra={"tenant_id": tenant_id_str, "deployment_key": deployment_key, "error": str(e)}
            )
            raise ValueError(f"Failed to parse DeploymentConfig from cache: {e}") from e
