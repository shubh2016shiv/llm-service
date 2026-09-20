"""Storage contract and key layout for cached authorization grants.

Architecture:
    AuthorizationGrantCache -> AuthorizationGrantCacheBackend -> RedisCache

This module owns representation: backend capabilities, Redis key names, and
serialization. Policy about when a grant is trusted stays in
``authorization_grant_cache.py``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from app.schemas.auth_schema import AuthorizationGrantVersions, CachedAuthorizationGrant

if TYPE_CHECKING:
    from collections.abc import Mapping
    from uuid import UUID

logger = logging.getLogger(__name__)
DEFAULT_GRANT_VERSIONS = AuthorizationGrantVersions()


class AuthorizationGrantCacheBackend(Protocol):
    """Minimal atomic storage operations required by authorization caching."""

    async def get_many(self, keys: tuple[str, ...]) -> list[bytes | None]: ...

    async def set(self, key: str, value: bytes, ttl_seconds: int | None) -> bool: ...

    async def delete(self, key: str) -> bool: ...

    async def set_if_values_match(
        self,
        key: str,
        value: bytes,
        expected_values: Mapping[str, bytes | None],
        ttl_seconds: int,
    ) -> bool: ...


def build_grant_keys(
    tenant_id: UUID,
    user_id: UUID,
    deployment_key: str,
) -> tuple[str, str, str, str, str]:
    """Return grant, tenant, membership, deployment, and route keys in order."""
    prefix = "inference_authz"
    return (
        f"{prefix}:{tenant_id}:{user_id}:{deployment_key}",
        f"{prefix}_version:tenant:{tenant_id}",
        f"{prefix}_version:membership:{tenant_id}:{user_id}",
        f"{prefix}_version:deployment:{tenant_id}:{deployment_key}",
        f"{prefix}_version:route:{tenant_id}:{user_id}:{deployment_key}",
    )


def decode_cached_grant(raw_grant: bytes) -> CachedAuthorizationGrant | None:
    """Decode a grant, treating corrupt or obsolete payloads as a cache miss."""
    try:
        return CachedAuthorizationGrant.model_validate_json(raw_grant)
    except ValueError:
        logger.warning("Invalid inference authorization grant payload")
        return None


def expected_raw_versions(
    version_keys: tuple[str, ...],
    versions: AuthorizationGrantVersions,
) -> dict[str, bytes | None]:
    """Translate decoded versions into exact values for atomic comparison.

    Default markers represent absent Redis keys. Real invalidations always use
    random UUID markers, so defaults are never stored as live versions.
    """
    values = (
        versions.tenant_version,
        versions.membership_version,
        versions.deployment_version,
        versions.route_version,
    )
    defaults = (
        DEFAULT_GRANT_VERSIONS.tenant_version,
        DEFAULT_GRANT_VERSIONS.membership_version,
        DEFAULT_GRANT_VERSIONS.deployment_version,
        DEFAULT_GRANT_VERSIONS.route_version,
    )
    return {
        key: None if value == default else value.encode("utf-8")
        for key, value, default in zip(version_keys, values, defaults, strict=True)
    }
