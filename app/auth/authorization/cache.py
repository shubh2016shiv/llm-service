"""
Inference Authorization Cache
=============================

This module stores successful inference authorization decisions in cache so
repeat requests can skip the full database authorization path.

Enterprise Pattern: Cache-Aside Pattern with Versioned Invalidation
    - On read: check cache first.
    - On miss: authorize from source-of-truth, then cache the result.
    - On data change: bump version keys so older cached entries become invalid.

How the flow works:
    TenantAuthorizationService
        |
        +--> Read cached grant and version snapshot
        +--> If cache miss/stale, run full authorization from persistence
        +--> Write fresh grant back to cache
        |
        v
    InferenceAuthorizationCache
        |
        v
    Redis-compatible backend

Dependencies:
    - app.schemas.auth_schema: Defines the cached authorization context model.

Author: Shubham Singh
"""

from __future__ import annotations

import logging
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict

from app.schemas.auth_schema import InferenceAccessContext

logger = logging.getLogger(__name__)

_TENANT_VERSION_ZERO = "tenant-v0"
_MEMBERSHIP_VERSION_ZERO = "membership-v0"
_DEPLOYMENT_VERSION_ZERO = "deployment-v0"
_ROUTE_VERSION_ZERO = "route-v0"


class AuthorizationCacheBackend(Protocol):
    """Minimal Redis-like cache operations used for inference authorization."""

    async def get(self, key: str) -> bytes | None:
        """Return cached bytes for a key, or None on miss."""
        ...

    async def set(self, key: str, value: bytes, ttl_seconds: int | None = 300) -> bool:
        """Store bytes under a key with an optional TTL."""
        ...

    async def delete(self, key: str) -> bool:
        """Delete a key."""
        ...


class AuthorizationVersionSnapshot(BaseModel):
    """Version markers that define whether one authorization grant is still valid."""

    model_config = ConfigDict(frozen=True)

    tenant_version: str
    membership_version: str
    deployment_version: str
    route_version: str


class CachedInferenceAuthorization(BaseModel):
    """Serialized authorization cache entry stored in Redis."""

    model_config = ConfigDict(frozen=True)

    context: InferenceAccessContext
    version_snapshot: AuthorizationVersionSnapshot


class InferenceAuthorizationCache:
    """Cache successful inference authorization contexts with scoped invalidation."""

    def __init__(self, backend: AuthorizationCacheBackend | None, ttl_seconds: int) -> None:
        """Initialize the cache wrapper.

        Args:
            backend: Redis-compatible cache backend. None disables caching.
            ttl_seconds: Positive cache TTL bounded by application settings.
        """
        self._backend = backend
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def _build_grant_key(tenant_id: UUID, user_id: UUID, deployment_key: str) -> str:
        """Build the canonical cache key for one authorization grant."""
        return f"inference_authz:{tenant_id}:{user_id}:{deployment_key}"

    @staticmethod
    def _build_tenant_version_key(tenant_id: UUID) -> str:
        """Build the version key for one tenant scope."""
        return f"inference_authz_version:tenant:{tenant_id}"

    @staticmethod
    def _build_membership_version_key(tenant_id: UUID, user_id: UUID) -> str:
        """Build the version key for one tenant membership scope."""
        return f"inference_authz_version:membership:{tenant_id}:{user_id}"

    @staticmethod
    def _build_deployment_version_key(tenant_id: UUID, deployment_key: str) -> str:
        """Build the version key for one deployment scope."""
        return f"inference_authz_version:deployment:{tenant_id}:{deployment_key}"

    @staticmethod
    def _build_route_version_key(tenant_id: UUID, user_id: UUID, deployment_key: str) -> str:
        """Build the version key for one tenant-user-route scope."""
        return f"inference_authz_version:route:{tenant_id}:{user_id}:{deployment_key}"

    async def read_version_snapshot(
        self, tenant_id: UUID, user_id: UUID, deployment_key: str
    ) -> AuthorizationVersionSnapshot | None:
        """Return the current scope versions for one authorization route."""
        if self._backend is None:
            return None
        tenant_version = await self._read_version(
            self._build_tenant_version_key(tenant_id),
            _TENANT_VERSION_ZERO,
        )
        membership_version = await self._read_version(
            self._build_membership_version_key(tenant_id, user_id),
            _MEMBERSHIP_VERSION_ZERO,
        )
        deployment_version = await self._read_version(
            self._build_deployment_version_key(tenant_id, deployment_key),
            _DEPLOYMENT_VERSION_ZERO,
        )
        route_version = await self._read_version(
            self._build_route_version_key(tenant_id, user_id, deployment_key),
            _ROUTE_VERSION_ZERO,
        )
        if (
            tenant_version is None
            or membership_version is None
            or deployment_version is None
            or route_version is None
        ):
            return None
        return AuthorizationVersionSnapshot(
            tenant_version=tenant_version,
            membership_version=membership_version,
            deployment_version=deployment_version,
            route_version=route_version,
        )

    async def get_entry(
        self, tenant_id: UUID, user_id: UUID, deployment_key: str
    ) -> CachedInferenceAuthorization | None:
        """Return a cached authorization grant if present and validly encoded."""
        if self._backend is None:
            return None
        key = self._build_grant_key(tenant_id, user_id, deployment_key)
        raw_value = await self._backend.get(key)
        if raw_value is None:
            return None
        try:
            return CachedInferenceAuthorization.model_validate_json(raw_value)
        except ValueError:
            logger.warning(
                "Invalid inference authorization cache entry",
                extra={"cache_key": key},
            )
            await self._backend.delete(key)
            return None

    async def set(
        self,
        context: InferenceAccessContext,
        version_snapshot: AuthorizationVersionSnapshot,
    ) -> None:
        """Cache an authorized inference context with its version snapshot."""
        if self._backend is None:
            return
        key = self._build_grant_key(context.tenant_id, context.user_id, context.deployment_key)
        value = CachedInferenceAuthorization(
            context=context,
            version_snapshot=version_snapshot,
        ).model_dump_json().encode("utf-8")
        await self._backend.set(key, value, ttl_seconds=self._ttl_seconds)

    async def delete_route_grant(self, tenant_id: UUID, user_id: UUID, deployment_key: str) -> None:
        """Delete one cached authorization grant without changing versions."""
        if self._backend is None:
            return
        await self._backend.delete(self._build_grant_key(tenant_id, user_id, deployment_key))

    async def invalidate_tenant(self, tenant_id: UUID) -> None:
        """Invalidate all grants under one tenant by advancing its tenant scope."""
        await self._bump_version(self._build_tenant_version_key(tenant_id))

    async def invalidate_membership(self, tenant_id: UUID, user_id: UUID) -> None:
        """Invalidate all grants for one tenant membership scope."""
        await self._bump_version(self._build_membership_version_key(tenant_id, user_id))

    async def invalidate_deployment(self, tenant_id: UUID, deployment_key: str) -> None:
        """Invalidate all grants that target one tenant deployment route."""
        await self._bump_version(self._build_deployment_version_key(tenant_id, deployment_key))

    async def invalidate_route(self, tenant_id: UUID, user_id: UUID, deployment_key: str) -> None:
        """Invalidate one tenant-user-deployment authorization route."""
        await self._bump_version(self._build_route_version_key(tenant_id, user_id, deployment_key))
        await self.delete_route_grant(tenant_id, user_id, deployment_key)

    async def _read_version(self, key: str, default_value: str) -> str | None:
        """Read one version key, returning the default marker when unset."""
        if self._backend is None:
            return None
        raw_value = await self._backend.get(key)
        if raw_value is None:
            return default_value
        try:
            return raw_value.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning("Invalid authorization version marker", extra={"cache_key": key})
            return None

    async def _bump_version(self, key: str) -> None:
        """Advance one invalidation scope using a monotonic unique marker."""
        if self._backend is None:
            return
        version_value = f"v:{uuid4()}"
        await self._backend.set(key, version_value.encode("utf-8"), ttl_seconds=None)

