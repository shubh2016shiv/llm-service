"""Unit tests for inference authorization grant caching."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from app.auth.authorization.authorization_grant_cache import AuthorizationGrantCache
from app.core.exceptions import AuthorizationGrantCacheUnavailableError
from app.schemas.auth_schema import (
    AuthorizationGrantVersions,
    InferenceAccessContext,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

TENANT_ID = UUID("aaaaaaaa-0000-0000-0000-000000000001")
USER_ID = UUID("bbbbbbbb-0000-0000-0000-000000000001")
DEPLOYMENT_ID = UUID("cccccccc-0000-0000-0000-000000000001")
PROVIDER_ID = UUID("dddddddd-0000-0000-0000-000000000001")
MODEL_ID = UUID("eeeeeeee-0000-0000-0000-000000000001")
ENTITLEMENT_ID = UUID("ffffffff-0000-0000-0000-000000000001")
DEPLOYMENT_KEY = "production-chat"
CACHE_TTL_SECONDS = 30


class FakeAuthorizationGrantBackend:
    """Provide deterministic cache storage without external infrastructure."""

    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.get_many_call_count = 0
        self.should_set_succeed = True
        self.should_delete_succeed = True
        self.ttl_by_key: dict[str, int | None] = {}

    async def get_many(self, keys: tuple[str, ...]) -> list[bytes | None]:
        """Return all requested values from one logical cache snapshot."""
        self.get_many_call_count += 1
        return [self.values.get(key) for key in keys]

    async def set(self, key: str, value: bytes, ttl_seconds: int | None = 300) -> bool:
        """Store a value when writes are configured to succeed."""
        if not self.should_set_succeed:
            return False
        self.values[key] = value
        self.ttl_by_key[key] = ttl_seconds
        return True

    async def delete(self, key: str) -> bool:
        """Delete a value when deletes are configured to succeed."""
        if not self.should_delete_succeed:
            return False
        self.values.pop(key, None)
        return True

    async def set_if_values_match(
        self,
        key: str,
        value: bytes,
        expected_values: Mapping[str, bytes | None],
        ttl_seconds: int,
    ) -> bool:
        """Model Redis' atomic comparison and write in one fake operation."""
        if not self.should_set_succeed:
            return False
        if any(self.values.get(name) != expected for name, expected in expected_values.items()):
            return False
        self.values[key] = value
        self.ttl_by_key[key] = ttl_seconds
        return True


def build_access_context() -> InferenceAccessContext:
    """Build one valid inference authorization context for cache tests."""
    return InferenceAccessContext(
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        deployment_key=DEPLOYMENT_KEY,
        deployment_id=DEPLOYMENT_ID,
        provider_id=PROVIDER_ID,
        model_id=MODEL_ID,
        tenant_role="developer",
        entitlement_id=ENTITLEMENT_ID,
    )


@pytest.mark.asyncio
async def test_find_grant_when_cache_is_empty_returns_miss_with_observed_versions() -> None:
    """REQ: an empty cache must preserve a version snapshot for guarded storage."""
    backend = FakeAuthorizationGrantBackend()
    cache = AuthorizationGrantCache(backend, CACHE_TTL_SECONDS)

    lookup = await cache.find_grant(TENANT_ID, USER_ID, DEPLOYMENT_KEY)

    assert lookup.context is None
    assert lookup.observed_versions is not None
    assert backend.get_many_call_count == 1


@pytest.mark.asyncio
async def test_find_grant_when_versions_match_returns_cached_context_in_one_read() -> None:
    """REQ: a cache hit must read the grant and version markers atomically."""
    backend = FakeAuthorizationGrantBackend()
    cache = AuthorizationGrantCache(backend, CACHE_TTL_SECONDS)
    first_lookup = await cache.find_grant(TENANT_ID, USER_ID, DEPLOYMENT_KEY)
    assert first_lookup.observed_versions is not None
    await cache.store_grant_if_unchanged(build_access_context(), first_lookup.observed_versions)
    backend.get_many_call_count = 0

    lookup = await cache.find_grant(TENANT_ID, USER_ID, DEPLOYMENT_KEY)

    assert lookup.context == build_access_context()
    assert backend.get_many_call_count == 1


@pytest.mark.asyncio
async def test_find_grant_version_markers_map_to_their_own_scope() -> None:
    """REQ: each decoded version field must reflect its own scope's stored marker.

    Guards against a future reorder of `AuthorizationGrantVersions`'s fields
    silently swapping two scopes' values if version decoding is ever
    reimplemented using positional pairing again.
    """
    backend = FakeAuthorizationGrantBackend()
    cache = AuthorizationGrantCache(backend, CACHE_TTL_SECONDS)
    backend.values[f"inference_authz_version:tenant:{TENANT_ID}"] = b"tenant:11"
    backend.values[f"inference_authz_version:membership:{TENANT_ID}:{USER_ID}"] = (
        b"membership:22"
    )
    backend.values[f"inference_authz_version:deployment:{TENANT_ID}:{DEPLOYMENT_KEY}"] = (
        b"deployment:33"
    )
    backend.values[f"inference_authz_version:route:{TENANT_ID}:{USER_ID}:{DEPLOYMENT_KEY}"] = (
        b"route:44"
    )

    lookup = await cache.find_grant(TENANT_ID, USER_ID, DEPLOYMENT_KEY)

    assert lookup.observed_versions is not None
    assert lookup.observed_versions.tenant_version == "tenant:11"
    assert lookup.observed_versions.membership_version == "membership:22"
    assert lookup.observed_versions.deployment_version == "deployment:33"
    assert lookup.observed_versions.route_version == "route:44"


@pytest.mark.asyncio
async def test_find_grant_when_payload_is_corrupt_deletes_value_and_returns_miss() -> None:
    """REQ: corrupt grants must be ignored and removed so PostgreSQL can self-heal them."""
    backend = FakeAuthorizationGrantBackend()
    cache = AuthorizationGrantCache(backend, CACHE_TTL_SECONDS)
    grant_key = f"inference_authz:{TENANT_ID}:{USER_ID}:{DEPLOYMENT_KEY}"
    backend.values[grant_key] = b"not-json"

    lookup = await cache.find_grant(TENANT_ID, USER_ID, DEPLOYMENT_KEY)

    assert lookup.context is None
    assert grant_key not in backend.values


@pytest.mark.asyncio
async def test_find_grant_when_version_is_corrupt_replaces_marker_and_returns_miss() -> None:
    """REQ: corrupt version markers must be replaced without trusting cached access."""
    backend = FakeAuthorizationGrantBackend()
    cache = AuthorizationGrantCache(backend, CACHE_TTL_SECONDS)
    version_key = f"inference_authz_version:tenant:{TENANT_ID}"
    backend.values[version_key] = b"\xff"

    lookup = await cache.find_grant(TENANT_ID, USER_ID, DEPLOYMENT_KEY)

    assert lookup.context is None
    assert lookup.observed_versions is None
    assert backend.values[version_key].startswith(b"v:")
    assert backend.ttl_by_key[version_key] == CACHE_TTL_SECONDS


@pytest.mark.asyncio
async def test_store_grant_when_versions_change_does_not_cache_stale_context() -> None:
    """REQ: a database result must not be cached after its observed scope changes."""
    backend = FakeAuthorizationGrantBackend()
    cache = AuthorizationGrantCache(backend, CACHE_TTL_SECONDS)
    observed_versions = AuthorizationGrantVersions()
    await cache.invalidate_tenant(TENANT_ID)

    was_stored = await cache.store_grant_if_unchanged(build_access_context(), observed_versions)

    assert was_stored is False


@pytest.mark.asyncio
async def test_invalidate_tenant_when_backend_write_fails_raises_typed_error() -> None:
    """REQ: authorization invalidation failure must be visible to management callers."""
    backend = FakeAuthorizationGrantBackend()
    backend.should_set_succeed = False
    cache = AuthorizationGrantCache(backend, CACHE_TTL_SECONDS)

    with pytest.raises(AuthorizationGrantCacheUnavailableError):
        await cache.invalidate_tenant(TENANT_ID)


@pytest.mark.asyncio
async def test_invalidate_route_when_grant_delete_fails_raises_typed_error() -> None:
    """REQ: exact-route invalidation must report a failed grant deletion."""
    backend = FakeAuthorizationGrantBackend()
    backend.should_delete_succeed = False
    cache = AuthorizationGrantCache(backend, CACHE_TTL_SECONDS)

    with pytest.raises(AuthorizationGrantCacheUnavailableError):
        await cache.invalidate_route(TENANT_ID, USER_ID, DEPLOYMENT_KEY)
