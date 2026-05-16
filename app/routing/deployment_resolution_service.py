"""
Deployment Resolution Service
=============================

Wraps the existing cache-backed deployment resolver with canonical
deployment-status validation for the resolution layer.

Architecture:
-------------
    request_resolution_service.py
        │
        └── deployment_resolution_service.py
                │
                └── app.routing.deployment_resolver.DeploymentResolver

Dependencies:
    - app.routing.deployment_resolver — existing cache-backed deployment lookup
    - app.core.settings.models.tenant_config — DeploymentConfig
    - app.core.exceptions — deployment error types

Author: Engineering Team
Last Updated: 2026-05-16
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.exceptions import DeploymentInactiveError, DeploymentNotFoundError
from app.routing.deployment_resolver import (
    DeploymentNotFoundError as LegacyDeploymentNotFoundError,
)
from app.routing.deployment_resolver import (
    DeploymentResolver,
)

if TYPE_CHECKING:
    from uuid import UUID

    from app.core.settings.models.tenant_config import DeploymentConfig


class DeploymentResolutionService:
    """Resolves and validates tenant deployments by deployment key."""

    def __init__(self, deployment_resolver: DeploymentResolver) -> None:
        self._deployment_resolver = deployment_resolver

    async def resolve_deployment(
        self,
        tenant_id: UUID | str,
        deployment_key: str,
    ) -> DeploymentConfig:
        """Resolve an active deployment or raise a canonical deployment error."""
        try:
            deployment = await self._deployment_resolver.resolve(
                tenant_id=tenant_id,
                deployment_key=deployment_key,
            )
        except LegacyDeploymentNotFoundError as exc:
            raise DeploymentNotFoundError(str(tenant_id), deployment_key) from exc

        if not deployment.is_active:
            raise DeploymentInactiveError(
                deployment_key=deployment.deployment_key,
                status=deployment.status.value,
            )
        return deployment
