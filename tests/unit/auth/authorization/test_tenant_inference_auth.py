"""Unit tests for tenant-scoped inference authorization decisions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from app.auth.authorization.tenant_inference_auth import InferenceAuthorizationService
from app.core.exceptions import (
    DeploymentInactiveError,
    DeploymentNotFoundError,
    TenantAccessDeniedError,
    TenantNotFoundError,
    TenantSuspendedError,
)
from app.schemas.auth_schema import (
    AuthorizationGrantLookup,
    AuthorizationGrantVersions,
    AuthTokenPayload,
    InferenceAccessContext,
)

TENANT_ID = UUID("aaaaaaaa-0000-0000-0000-000000000001")
USER_ID = UUID("bbbbbbbb-0000-0000-0000-000000000001")
PROVIDER_ID = UUID("cccccccc-0000-0000-0000-000000000001")
MODEL_ID = UUID("dddddddd-0000-0000-0000-000000000001")
DEPLOYMENT_ID = UUID("eeeeeeee-0000-0000-0000-000000000001")
ENTITLEMENT_ID = UUID("ffffffff-0000-0000-0000-000000000001")
DEPLOYMENT_KEY = "production-chat"

_ACTIVE_TENANT = {"status": "active"}
_ACTIVE_MEMBERSHIP = {"status": "active", "tenant_role": "developer"}
_ACTIVE_DEPLOYMENT = {
    "deployment_id": str(DEPLOYMENT_ID),
    "provider_id": str(PROVIDER_ID),
    "model_id": str(MODEL_ID),
    "status": "active",
}
_ACTIVE_ENTITLEMENT = {"entitlement_id": str(ENTITLEMENT_ID)}


def build_caller() -> AuthTokenPayload:
    """Build one authenticated developer caller payload."""
    now = datetime.now(UTC)
    return AuthTokenPayload(
        user_id=USER_ID,
        role="developer",
        token_id=UUID("99999999-0000-0000-0000-000000000001"),
        expires_at=now + timedelta(minutes=5),
        issued_at=now,
    )


class FakeTenantPersistence:
    def __init__(self, tenant: dict[str, str] | None) -> None:
        self.tenant = tenant

    async def get_tenant_by_id(self, tenant_id: UUID) -> dict[str, str] | None:
        return self.tenant


class FakeMembershipPersistence:
    def __init__(self, membership: dict[str, str] | None) -> None:
        self.membership = membership

    async def get_membership(self, tenant_id: UUID, user_id: UUID) -> dict[str, str] | None:
        return self.membership


class FakeDeploymentPersistence:
    def __init__(self, deployment: dict[str, str] | None) -> None:
        self.deployment = deployment

    async def get_deployment_by_key(
        self, tenant_id: UUID, deployment_key: str
    ) -> dict[str, str] | None:
        return self.deployment


class FakeEntitlementPersistence:
    def __init__(self, entitlement: dict[str, str] | None) -> None:
        self.entitlement = entitlement

    async def get_active_entitlement_for_route(self, **kwargs: Any) -> dict[str, str] | None:
        return self.entitlement


class FakeAuthorizationGrantCache:
    """Stand in for AuthorizationGrantCache with a scripted lookup result."""

    def __init__(self, lookup: AuthorizationGrantLookup | None = None) -> None:
        self.lookup = lookup or AuthorizationGrantLookup(
            context=None, observed_versions=AuthorizationGrantVersions()
        )
        self.store_calls: list[tuple[InferenceAccessContext, AuthorizationGrantVersions]] = []

    async def find_grant(
        self, tenant_id: UUID, user_id: UUID, deployment_key: str
    ) -> AuthorizationGrantLookup:
        return self.lookup

    async def store_grant_if_unchanged(
        self, context: InferenceAccessContext, observed_versions: AuthorizationGrantVersions
    ) -> bool:
        self.store_calls.append((context, observed_versions))
        return True


def build_service(
    *,
    tenant: dict[str, str] | None = _ACTIVE_TENANT,
    membership: dict[str, str] | None = _ACTIVE_MEMBERSHIP,
    deployment: dict[str, str] | None = _ACTIVE_DEPLOYMENT,
    entitlement: dict[str, str] | None = _ACTIVE_ENTITLEMENT,
    cache: FakeAuthorizationGrantCache | None = None,
) -> tuple[InferenceAuthorizationService, FakeAuthorizationGrantCache]:
    grant_cache = cache or FakeAuthorizationGrantCache()
    service = InferenceAuthorizationService(
        tenant_persistence=FakeTenantPersistence(tenant),
        membership_persistence=FakeMembershipPersistence(membership),
        deployment_persistence=FakeDeploymentPersistence(deployment),
        entitlement_persistence=FakeEntitlementPersistence(entitlement),
        authorization_cache=grant_cache,
    )
    return service, grant_cache


@pytest.mark.asyncio
async def test_authorize_inference_on_cache_hit_returns_cached_context_without_db_checks() -> None:
    """REQ: a fresh cache hit must short-circuit before any persistence lookup runs."""
    cached_context = InferenceAccessContext(
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        deployment_key=DEPLOYMENT_KEY,
        deployment_id=DEPLOYMENT_ID,
        provider_id=PROVIDER_ID,
        model_id=MODEL_ID,
        tenant_role="developer",
        entitlement_id=ENTITLEMENT_ID,
    )
    cache = FakeAuthorizationGrantCache(
        lookup=AuthorizationGrantLookup(
            context=cached_context, observed_versions=AuthorizationGrantVersions()
        )
    )
    service, _ = build_service(tenant=None, membership=None, deployment=None, cache=cache)

    result = await service.authorize_inference(TENANT_ID, DEPLOYMENT_KEY, build_caller())

    assert result == cached_context


@pytest.mark.asyncio
async def test_authorize_inference_when_tenant_missing_raises_not_found() -> None:
    """REQ: an unknown tenant must fail before membership or deployment are checked."""
    service, _ = build_service(tenant=None)

    with pytest.raises(TenantNotFoundError):
        await service.authorize_inference(TENANT_ID, DEPLOYMENT_KEY, build_caller())


@pytest.mark.asyncio
async def test_authorize_inference_when_tenant_suspended_raises_suspended() -> None:
    """REQ: a tenant outside active/trial status must be rejected."""
    service, _ = build_service(tenant={"status": "suspended"})

    with pytest.raises(TenantSuspendedError):
        await service.authorize_inference(TENANT_ID, DEPLOYMENT_KEY, build_caller())


@pytest.mark.asyncio
async def test_authorize_inference_when_membership_missing_raises_denied() -> None:
    """REQ: a caller with no tenant membership must be denied."""
    service, _ = build_service(membership=None)

    with pytest.raises(TenantAccessDeniedError) as excinfo:
        await service.authorize_inference(TENANT_ID, DEPLOYMENT_KEY, build_caller())
    assert excinfo.value.required_role == "active_member"


@pytest.mark.asyncio
async def test_authorize_inference_when_membership_inactive_raises_denied() -> None:
    """REQ: an inactive membership must be denied even if a row exists."""
    service, _ = build_service(membership={"status": "suspended", "tenant_role": "developer"})

    with pytest.raises(TenantAccessDeniedError) as excinfo:
        await service.authorize_inference(TENANT_ID, DEPLOYMENT_KEY, build_caller())
    assert excinfo.value.required_role == "active_member"


@pytest.mark.asyncio
async def test_authorize_inference_when_tenant_role_not_inference_eligible_raises_denied() -> None:
    """REQ: a viewer-only tenant role must not be allowed to run inference."""
    service, _ = build_service(membership={"status": "active", "tenant_role": "viewer"})

    with pytest.raises(TenantAccessDeniedError) as excinfo:
        await service.authorize_inference(TENANT_ID, DEPLOYMENT_KEY, build_caller())
    assert excinfo.value.required_role == "inference_eligible_role"


@pytest.mark.asyncio
async def test_authorize_inference_when_deployment_missing_raises_not_found() -> None:
    """REQ: an unknown deployment key must fail after membership passes."""
    service, _ = build_service(deployment=None)

    with pytest.raises(DeploymentNotFoundError):
        await service.authorize_inference(TENANT_ID, DEPLOYMENT_KEY, build_caller())


@pytest.mark.asyncio
async def test_authorize_inference_when_deployment_inactive_raises_inactive() -> None:
    """REQ: a paused/maintenance deployment must not be usable."""
    service, _ = build_service(
        deployment={**_ACTIVE_DEPLOYMENT, "status": "maintenance"},
    )

    with pytest.raises(DeploymentInactiveError):
        await service.authorize_inference(TENANT_ID, DEPLOYMENT_KEY, build_caller())


@pytest.mark.asyncio
async def test_authorize_inference_when_entitlement_missing_raises_denied() -> None:
    """REQ: a valid tenant member without an entitlement must still be denied."""
    service, _ = build_service(entitlement=None)

    with pytest.raises(TenantAccessDeniedError) as excinfo:
        await service.authorize_inference(TENANT_ID, DEPLOYMENT_KEY, build_caller())
    assert excinfo.value.required_role == "active_entitlement"


@pytest.mark.asyncio
async def test_authorize_inference_on_full_success_returns_context_and_caches_it() -> None:
    """REQ: a fully valid request must return a correct context and store it."""
    service, cache = build_service()

    result = await service.authorize_inference(TENANT_ID, DEPLOYMENT_KEY, build_caller())

    assert result.tenant_id == TENANT_ID
    assert result.user_id == USER_ID
    assert result.deployment_key == DEPLOYMENT_KEY
    assert result.deployment_id == DEPLOYMENT_ID
    assert result.provider_id == PROVIDER_ID
    assert result.model_id == MODEL_ID
    assert result.tenant_role == "developer"
    assert result.entitlement_id == ENTITLEMENT_ID
    assert len(cache.store_calls) == 1
    stored_context, stored_versions = cache.store_calls[0]
    assert stored_context == result
    assert stored_versions == cache.lookup.observed_versions


@pytest.mark.asyncio
async def test_authorize_inference_when_cache_disabled_skips_store_call() -> None:
    """REQ: a cache with no observed versions (disabled/unavailable) must not be written to."""
    cache = FakeAuthorizationGrantCache(
        lookup=AuthorizationGrantLookup(context=None, observed_versions=None)
    )
    service, _ = build_service(cache=cache)

    await service.authorize_inference(TENANT_ID, DEPLOYMENT_KEY, build_caller())

    assert cache.store_calls == []
