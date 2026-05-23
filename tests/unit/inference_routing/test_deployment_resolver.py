"""
Unit Tests — DeploymentResolver (Cache-Aside Pattern)
=======================================================

Covers every branch of the cache-aside read-through logic:

    Cache HIT path:
        - valid JSON  + active  → returns deployment (no DB call)
        - valid JSON  + inactive → raises DeploymentInactiveError
        - corrupt JSON           → raises ConfigurationError (no silent swallow)

    Cache MISS path:
        - DB returns deployment  + active   → returns deployment + repopulates cache
        - DB returns deployment  + inactive → raises DeploymentInactiveError
        - DB returns None                   → raises DeploymentNotFoundError
        - cache repopulation failure        → deployment still returned (non-fatal)

Architecture:
-------------
    FakeRedisCache ──▶ DeploymentResolver (unit under test) ◀── FakeDeploymentConfigReader

Author: Shubham Singh
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from app.core.exceptions import ConfigurationError, DeploymentInactiveError, DeploymentNotFoundError
from app.core.settings.models.tenant_config import DeploymentStatus
from app.inference_routing.deployment_resolver import DeploymentResolver
from tests.unit.inference_routing.conftest import (
    DEPLOYMENT_KEY,
    TENANT_ID,
    FakeDeploymentConfigReader,
    FakeRedisCache,
    build_deployment_config,
)

if TYPE_CHECKING:
    from app.infrastructure.redis_cache import RedisCache

# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

CACHE_KEY = f"tenant:{TENANT_ID}:deployments:{DEPLOYMENT_KEY}"


def _make_resolver(
    *,
    cache_data: bytes | None = None,
    db_deployment=None,
) -> tuple[DeploymentResolver, FakeRedisCache]:
    stored = {CACHE_KEY: cache_data} if cache_data is not None else {}
    cache = FakeRedisCache(stored)
    db_reader = FakeDeploymentConfigReader(db_deployment)
    # cast: FakeRedisCache is structurally compatible with RedisCache for testing
    resolver = DeploymentResolver(cache=cast("RedisCache", cache), db_reader=db_reader)
    return resolver, cache


# ═══════════════════════════════════════════════════════════════════════════════
# Cache HIT path
# ═══════════════════════════════════════════════════════════════════════════════


class TestCacheHitPath:
    @pytest.mark.asyncio
    async def test_returns_deployment_from_cache_when_valid_and_active(
        self, active_deployment
    ):
        """Valid cached payload of an active deployment → returned without hitting DB."""
        cached_bytes = active_deployment.model_dump_json().encode("utf-8")
        resolver, _cache = _make_resolver(cache_data=cached_bytes)

        result = await resolver.resolve(TENANT_ID, DEPLOYMENT_KEY)

        assert result.deployment_key == DEPLOYMENT_KEY
        assert result.is_active is True

    @pytest.mark.asyncio
    async def test_raises_inactive_error_for_cached_inactive_deployment(
        self, inactive_deployment
    ):
        """Cached deployment with INACTIVE status → DeploymentInactiveError."""
        cached_bytes = inactive_deployment.model_dump_json().encode("utf-8")
        resolver, _ = _make_resolver(cache_data=cached_bytes)

        with pytest.raises(DeploymentInactiveError) as exc_info:
            await resolver.resolve(TENANT_ID, DEPLOYMENT_KEY)

        assert exc_info.value.deployment_key == DEPLOYMENT_KEY

    @pytest.mark.asyncio
    async def test_raises_configuration_error_for_corrupt_cached_json(self):
        """Corrupted cache bytes → ConfigurationError (never silently ignored)."""
        resolver, _ = _make_resolver(cache_data=b"this is not valid json {{{")

        with pytest.raises(ConfigurationError) as exc_info:
            await resolver.resolve(TENANT_ID, DEPLOYMENT_KEY)

        assert DEPLOYMENT_KEY in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_does_not_call_db_reader_on_cache_hit(self, active_deployment):
        """Cache hit must short-circuit — DB reader never called."""
        cached_bytes = active_deployment.model_dump_json().encode("utf-8")
        # DB reader has None → if it were called, it would raise DeploymentNotFoundError
        stored = {CACHE_KEY: cached_bytes}
        cache = FakeRedisCache(stored)
        db_reader = FakeDeploymentConfigReader(deployment=None)
        resolver = DeploymentResolver(cache=cast("RedisCache", cache), db_reader=db_reader)

        result = await resolver.resolve(TENANT_ID, DEPLOYMENT_KEY)

        assert result.deployment_key == DEPLOYMENT_KEY


# ═══════════════════════════════════════════════════════════════════════════════
# Cache MISS path
# ═══════════════════════════════════════════════════════════════════════════════


class TestCacheMissPath:
    @pytest.mark.asyncio
    async def test_returns_deployment_from_db_on_cache_miss(self, active_deployment):
        """Cache miss → falls back to DB reader → returns active deployment."""
        resolver, _ = _make_resolver(cache_data=None, db_deployment=active_deployment)

        result = await resolver.resolve(TENANT_ID, DEPLOYMENT_KEY)

        assert result.deployment_key == DEPLOYMENT_KEY

    @pytest.mark.asyncio
    async def test_repopulates_cache_after_db_fallback(self, active_deployment):
        """After DB fallback, resolver must write to cache for subsequent requests."""
        stored: dict = {}
        cache = FakeRedisCache(stored)
        db_reader = FakeDeploymentConfigReader(active_deployment)
        resolver = DeploymentResolver(cache=cast("RedisCache", cache), db_reader=db_reader)

        await resolver.resolve(TENANT_ID, DEPLOYMENT_KEY)

        assert len(cache.set_calls) == 1
        set_key, _ = cache.set_calls[0]
        assert set_key == CACHE_KEY

    @pytest.mark.asyncio
    async def test_raises_not_found_when_db_returns_none(self):
        """Neither cache nor DB has the deployment → DeploymentNotFoundError."""
        resolver, _ = _make_resolver(cache_data=None, db_deployment=None)

        with pytest.raises(DeploymentNotFoundError) as exc_info:
            await resolver.resolve(TENANT_ID, DEPLOYMENT_KEY)

        err = exc_info.value
        assert err.deployment_key == DEPLOYMENT_KEY
        assert err.tenant_id == str(TENANT_ID)

    @pytest.mark.asyncio
    async def test_raises_inactive_error_for_db_inactive_deployment(
        self, inactive_deployment
    ):
        """DB returns inactive deployment → DeploymentInactiveError."""
        resolver, _ = _make_resolver(cache_data=None, db_deployment=inactive_deployment)

        with pytest.raises(DeploymentInactiveError):
            await resolver.resolve(TENANT_ID, DEPLOYMENT_KEY)

    @pytest.mark.asyncio
    async def test_deployment_returned_even_when_cache_repopulation_fails(
        self, active_deployment
    ):
        """Cache write failure is non-fatal — deployment is still returned."""

        class BrokenCache(FakeRedisCache):
            async def set(self, key, value, **kwargs):
                raise ConnectionError("Redis is down")

        cache = BrokenCache({})
        db_reader = FakeDeploymentConfigReader(active_deployment)
        resolver = DeploymentResolver(cache=cast("RedisCache", cache), db_reader=db_reader)

        result = await resolver.resolve(TENANT_ID, DEPLOYMENT_KEY)

        assert result.deployment_key == DEPLOYMENT_KEY


# ═══════════════════════════════════════════════════════════════════════════════
# Parametrize inactive statuses
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("status", [DeploymentStatus.INACTIVE, DeploymentStatus.MAINTENANCE])
@pytest.mark.asyncio
async def test_all_non_active_statuses_raise_inactive_error(status):
    """Both INACTIVE and MAINTENANCE statuses must be rejected."""
    deployment = build_deployment_config(status=status)
    cached_bytes = deployment.model_dump_json().encode("utf-8")
    resolver, _ = _make_resolver(cache_data=cached_bytes)

    with pytest.raises(DeploymentInactiveError) as exc_info:
        await resolver.resolve(TENANT_ID, DEPLOYMENT_KEY)

    assert exc_info.value.status == status.value
