"""Fetch and validate the configuration needed to resolve inference routes.

Architecture:
    InferenceRouteResolver
             |
             v
    CachedInferenceRoutingConfigReader
       |             |                  |
       v             v                  v
    PostgreSQL   DeploymentConfigCache   row mappers
                       |
                       v
                     Redis

The three reads have deliberately different freshness rules:
    * Tenant configuration always comes from PostgreSQL so suspension and
      rate-limit changes take effect on the next request.
    * User entitlements always come from PostgreSQL so revoked personal keys
      disappear immediately.
    * Deployment configuration uses cache-aside because it is read frequently
      and changes comparatively rarely.

When several requests miss the same deployment cache key together, they share
one ``asyncio.Task``. Think of that task as one numbered ticket for the actual
PostgreSQL trip: every waiter observes the same value or failure. ``shield``
prevents one impatient/cancelled HTTP request from cancelling work that other
requests still need.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import TYPE_CHECKING

from app.adapters.inference_routing.deployment_cache import DeploymentConfigCache
from app.adapters.inference_routing.routing_config_mappers import (
    convert_deployment_row,
    convert_entitlement_row,
    convert_tenant_row,
)
from app.inference_routing.cache_keys import build_deployment_config_cache_key

if TYPE_CHECKING:
    from uuid import UUID

    from app.adapters.inference_routing.contracts import (
        DeploymentCacheBackend,
        DeploymentRoutingPersistence,
        EntitlementRoutingPersistence,
        TenantRoutingPersistence,
    )
    from app.core.settings.models.tenant_config import (
        DeploymentConfig,
        TenantConfig,
        UserEntitlementConfig,
    )
    from app.inference_routing.models import ResolutionRequest


class CachedInferenceRoutingConfigReader:
    """Combine authoritative PostgreSQL reads with deployment cache-aside."""

    def __init__(
        self,
        tenant_persistence: TenantRoutingPersistence,
        entitlement_persistence: EntitlementRoutingPersistence,
        deployment_persistence: DeploymentRoutingPersistence,
        redis_cache: DeploymentCacheBackend,
    ) -> None:
        """Store narrow dependencies and initialize per-process request sharing."""
        self._tenant_persistence = tenant_persistence
        self._entitlement_persistence = entitlement_persistence
        self._deployment_persistence = deployment_persistence
        self._deployment_cache = DeploymentConfigCache(redis_cache)

        # One entry means one PostgreSQL read is currently running for that
        # deployment. This dictionary is process-local: it protects one worker
        # without introducing the failure modes of a distributed lock.
        self._inflight_reads: dict[str, asyncio.Task[DeploymentConfig | None]] = {}
        self._stats: defaultdict[str, int] = defaultdict(int)

    async def read_tenant_config(self, tenant_id: UUID) -> TenantConfig | None:
        """Read current tenant rules directly from PostgreSQL."""
        row = await self._tenant_persistence.get_tenant_config_for_routing(tenant_id)
        return convert_tenant_row(row) if row is not None else None

    async def find_user_entitlements(
        self,
        request: ResolutionRequest,
    ) -> list[UserEntitlementConfig]:
        """Read and validate personal-key candidates for one routing request."""
        rows = await self._entitlement_persistence.list_routing_entitlements_for_route(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            deployment_key=request.deployment_key,
            requested_model_name=request.requested_model_name,
            entitlement_id=request.pre_authorized_entitlement_id,
        )
        return [convert_entitlement_row(row) for row in rows]

    async def read_deployment_config(
        self,
        tenant_id: UUID,
        deployment_key: str,
    ) -> DeploymentConfig | None:
        """Read deployment configuration from Redis, then PostgreSQL on a miss."""
        cache_key = build_deployment_config_cache_key(tenant_id, deployment_key)
        cached = await self._deployment_cache.read(cache_key)
        if cached is not None:
            return cached

        # There is no await between checking and inserting the task. In one
        # asyncio event loop, another coroutine therefore cannot slip into
        # that tiny critical section and create a duplicate database trip.
        task = self._inflight_reads.get(cache_key)
        if task is None:
            self._stats["database_load"] += 1
            task = asyncio.create_task(
                self._load_deployment(tenant_id, deployment_key, cache_key),
                name=f"routing-config:{cache_key}",
            )
            self._inflight_reads[cache_key] = task

            def finish_inflight(completed: asyncio.Task[DeploymentConfig | None]) -> None:
                self._finish_inflight(cache_key, completed)

            task.add_done_callback(finish_inflight)
        else:
            self._stats["coalesced_waiter"] += 1

        # shield protects the shared trip from cancellation by this caller.
        # The caller still receives CancelledError promptly; the task continues
        # for other waiters and to warm Redis for the next request.
        return await asyncio.shield(task)

    async def _load_deployment(
        self,
        tenant_id: UUID,
        deployment_key: str,
        cache_key: str,
    ) -> DeploymentConfig | None:
        """Run the one authoritative database trip shared by all waiters."""
        row = await self._deployment_persistence.get_deployment_config_for_routing(
            tenant_id, deployment_key
        )
        if row is None:
            return None
        deployment = convert_deployment_row(row)
        await self._deployment_cache.write(cache_key, deployment)
        return deployment

    def _finish_inflight(
        self,
        cache_key: str,
        task: asyncio.Task[DeploymentConfig | None],
    ) -> None:
        """Remove a completed ticket and mark its exception as observed."""
        if self._inflight_reads.get(cache_key) is task:
            self._inflight_reads.pop(cache_key, None)
        if not task.cancelled():
            # Reading exception() prevents asyncio's "Task exception was never
            # retrieved" warning when the original caller was cancelled and no
            # other waiter remained. Awaiters still receive the same exception.
            task.exception()

    def stats(self) -> dict[str, int]:
        """Return cache and coalescing counters for operational diagnostics."""
        snapshot = {f"reader.{name}": value for name, value in self._stats.items()}
        snapshot.update(
            {
                f"deployment_cache.{name}": value
                for name, value in self._deployment_cache.stats().items()
            }
        )
        snapshot["reader.inflight"] = len(self._inflight_reads)
        return dict(sorted(snapshot.items()))
