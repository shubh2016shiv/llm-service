"""Race-safe cache for successful inference authorization decisions.

Architecture:
    inference authorization -> AuthorizationGrantCache -> atomic cache backend
    management mutations -------------------------------> version invalidation

A cached "yes" is safe only while the tenant, membership, deployment, and
exact route versions still equal those observed during database checks. Reads
take one snapshot. Writes compare all versions and store in one atomic backend
operation, closing the check-then-write race.
"""

from __future__ import annotations

import logging
from uuid import UUID, uuid4

from app.auth.authorization.authorization_grant_storage import (
    DEFAULT_GRANT_VERSIONS,
    AuthorizationGrantCacheBackend,
    build_grant_keys,
    decode_cached_grant,
    expected_raw_versions,
)
from app.core.exceptions import AuthorizationGrantCacheUnavailableError
from app.schemas.auth_schema import (
    AuthorizationGrantLookup,
    AuthorizationGrantVersions,
    CachedAuthorizationGrant,
    InferenceAccessContext,
)

logger = logging.getLogger(__name__)


class AuthorizationGrantCache:
    """Cache grants without allowing stale access after a management change.

    Think of each version as a tamper seal. A management change replaces one
    seal, immediately making every grant carrying the old seal unusable.
    """

    def __init__(
        self,
        backend: AuthorizationGrantCacheBackend | None,
        ttl_seconds: int,
    ) -> None:
        """Bind an optional backend and defense-in-depth grant lifetime."""
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        self._backend = backend
        self._ttl_seconds = ttl_seconds

    async def find_grant(
        self,
        tenant_id: UUID,
        user_id: UUID,
        deployment_key: str,
    ) -> AuthorizationGrantLookup:
        """Return a current cached grant and the version snapshot it used."""
        if self._backend is None:
            return AuthorizationGrantLookup(context=None, observed_versions=None)

        cache_keys = build_grant_keys(tenant_id, user_id, deployment_key)
        cached_values = await self._backend.get_many(cache_keys)
        observed_versions = await self._read_versions(cache_keys[1:], cached_values[1:])
        raw_grant = cached_values[0]
        if raw_grant is None or observed_versions is None:
            return AuthorizationGrantLookup(context=None, observed_versions=observed_versions)

        cached_grant = decode_cached_grant(raw_grant)
        if cached_grant is None or cached_grant.versions != observed_versions:
            await self._delete_stale_grant(cache_keys[0])
            return AuthorizationGrantLookup(context=None, observed_versions=observed_versions)
        return AuthorizationGrantLookup(
            context=cached_grant.context,
            observed_versions=observed_versions,
        )

    async def store_grant_if_unchanged(
        self,
        context: InferenceAccessContext,
        observed_versions: AuthorizationGrantVersions,
    ) -> bool:
        """Atomically save a grant only if no dependency changed mid-check."""
        if self._backend is None:
            return False
        cache_keys = build_grant_keys(
            context.tenant_id,
            context.user_id,
            context.deployment_key,
        )
        serialized_grant = (
            CachedAuthorizationGrant(
                context=context,
                versions=observed_versions,
            )
            .model_dump_json()
            .encode("utf-8")
        )
        return await self._backend.set_if_values_match(
            cache_keys[0],
            serialized_grant,
            expected_raw_versions(cache_keys[1:], observed_versions),
            self._ttl_seconds,
        )

    async def invalidate_tenant(self, tenant_id: UUID) -> None:
        """Invalidate every grant depending on one tenant."""
        await self._advance_version(f"inference_authz_version:tenant:{tenant_id}", "tenant")

    async def invalidate_membership(self, tenant_id: UUID, user_id: UUID) -> None:
        """Invalidate grants depending on one tenant membership."""
        key = f"inference_authz_version:membership:{tenant_id}:{user_id}"
        await self._advance_version(key, "membership")

    async def invalidate_deployment(self, tenant_id: UUID, deployment_key: str) -> None:
        """Invalidate grants depending on one deployment."""
        key = f"inference_authz_version:deployment:{tenant_id}:{deployment_key}"
        await self._advance_version(key, "deployment")

    async def invalidate_route(
        self,
        tenant_id: UUID,
        user_id: UUID,
        deployment_key: str,
    ) -> None:
        """Invalidate and remove one exact tenant-user-deployment grant."""
        cache_keys = build_grant_keys(tenant_id, user_id, deployment_key)
        await self._advance_version(cache_keys[4], "route")
        if self._backend is not None and not await self._backend.delete(cache_keys[0]):
            raise AuthorizationGrantCacheUnavailableError("delete route grant")

    async def _read_versions(
        self,
        version_keys: tuple[str, ...],
        raw_versions: list[bytes | None],
    ) -> AuthorizationGrantVersions | None:
        """Decode a version snapshot; repair corrupt markers and miss safely."""
        if self._backend is None:
            return None
        field_names = (
            "tenant_version",
            "membership_version",
            "deployment_version",
            "route_version",
        )
        decoded_versions: dict[str, str] = {}
        for field_name, version_key, raw_version in zip(
            field_names,
            version_keys,
            raw_versions,
            strict=True,
        ):
            if raw_version is None:
                decoded_versions[field_name] = str(getattr(DEFAULT_GRANT_VERSIONS, field_name))
                continue
            if not await self._decode_or_repair_version(
                decoded_versions,
                field_name,
                version_key,
                raw_version,
            ):
                return None
        return AuthorizationGrantVersions.model_validate(decoded_versions)

    async def _decode_or_repair_version(
        self,
        decoded_versions: dict[str, str],
        field_name: str,
        version_key: str,
        raw_version: bytes,
    ) -> bool:
        """Decode one marker, replacing corrupt bytes with a fresh marker."""
        try:
            decoded_versions[field_name] = raw_version.decode("utf-8")
            return True
        except UnicodeDecodeError:
            logger.warning("Invalid authorization grant version", extra={"field": field_name})
        if self._backend is not None:
            repaired = await self._backend.set(
                version_key,
                f"v:{uuid4()}".encode(),
                ttl_seconds=self._ttl_seconds,
            )
            if not repaired:
                logger.warning("Could not repair corrupt authorization grant version")
        return False

    async def _delete_stale_grant(self, grant_key: str) -> None:
        """Best-effort delete an already-rejected grant."""
        if self._backend is not None and not await self._backend.delete(grant_key):
            logger.warning("Could not delete unusable inference authorization grant")

    async def _advance_version(self, version_key: str, scope_name: str) -> None:
        """Replace one version marker, failing closed when Redis refuses."""
        if self._backend is None:
            return
        was_written = await self._backend.set(
            version_key,
            f"v:{uuid4()}".encode(),
            ttl_seconds=self._ttl_seconds,
        )
        if not was_written:
            raise AuthorizationGrantCacheUnavailableError(f"invalidate {scope_name}")
