"""
Unit Tests — UserEntitlementResolver
======================================

Covers:
    - No candidates → returns None (fall-through to deployment path)
    - All candidates inactive → returns None
    - Exactly one active candidate → returns it
    - Multiple active candidates → raises AmbiguousUserEntitlementError
    - Single active candidate but provider blocked → raises ProviderNotAllowedError
    - Provider allow-list enforcement uses tenant_resolver.ensure_provider_allowed

Architecture:
-------------
    FakeUserEntitlementReader ──▶ UserEntitlementResolver (unit under test)
                                         │
                                         ▼
                                  TenantResolver (real, injected with FakeTenantConfigReader)

Author: Shubham Singh
"""

from __future__ import annotations

import pytest

from app.core.settings.models.tenant_config import TenantStatus
from app.inference_routing.entitlement_resolver import UserEntitlementResolver
from app.inference_routing.exceptions import (
    AmbiguousUserEntitlementError,
    ProviderNotAllowedError,
)
from app.inference_routing.models import ResolutionRequest
from app.inference_routing.tenant_resolver import TenantResolver
from app.schemas.enums import OperationType
from tests.unit.inference_routing.conftest import (
    DEPLOYMENT_KEY,
    TENANT_ID,
    USER_ID,
    FakeTenantConfigReader,
    FakeUserEntitlementReader,
    build_tenant_config,
    build_user_entitlement_config,
)


def _make_request() -> ResolutionRequest:
    return ResolutionRequest(
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        deployment_key=DEPLOYMENT_KEY,
        operation=OperationType.CHAT,
    )


def _make_resolver(
    candidates: list,
    *,
    allowed_provider_names: frozenset[str] | None = None,
) -> UserEntitlementResolver:
    tenant = build_tenant_config(
        status=TenantStatus.ACTIVE,
        allowed_provider_names=allowed_provider_names,
    )
    tenant_resolver = TenantResolver(FakeTenantConfigReader(tenant))
    entitlement_reader = FakeUserEntitlementReader(candidates)
    return UserEntitlementResolver(
        entitlement_reader=entitlement_reader,
        tenant_resolver=tenant_resolver,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# No candidates → fall-through
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoEntitlementCandidates:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_candidates(self, active_tenant):
        """Empty candidate list → resolver returns None (deployment path)."""
        tenant_resolver = TenantResolver(FakeTenantConfigReader(active_tenant))
        resolver = UserEntitlementResolver(
            entitlement_reader=FakeUserEntitlementReader([]),
            tenant_resolver=tenant_resolver,
        )

        result = await resolver.resolve_override(active_tenant, _make_request())

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_all_candidates_inactive(self, active_tenant, inactive_entitlement):
        """All candidates inactive → filtered out → returns None."""
        tenant_resolver = TenantResolver(FakeTenantConfigReader(active_tenant))
        resolver = UserEntitlementResolver(
            entitlement_reader=FakeUserEntitlementReader([inactive_entitlement]),
            tenant_resolver=tenant_resolver,
        )

        result = await resolver.resolve_override(active_tenant, _make_request())

        assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# Single active candidate — happy path
# ═══════════════════════════════════════════════════════════════════════════════


class TestSingleActiveEntitlement:
    @pytest.mark.asyncio
    async def test_returns_entitlement_for_single_active_candidate(
        self, active_tenant, active_entitlement
    ):
        """Exactly one active candidate → entitlement returned."""
        tenant_resolver = TenantResolver(FakeTenantConfigReader(active_tenant))
        resolver = UserEntitlementResolver(
            entitlement_reader=FakeUserEntitlementReader([active_entitlement]),
            tenant_resolver=tenant_resolver,
        )

        result = await resolver.resolve_override(active_tenant, _make_request())

        assert result is active_entitlement

    @pytest.mark.asyncio
    async def test_inactive_mixed_with_active_returns_the_active_one(
        self, active_tenant, active_entitlement, inactive_entitlement
    ):
        """One active + one inactive → only active survives filter."""
        tenant_resolver = TenantResolver(FakeTenantConfigReader(active_tenant))
        resolver = UserEntitlementResolver(
            entitlement_reader=FakeUserEntitlementReader(
                [inactive_entitlement, active_entitlement]
            ),
            tenant_resolver=tenant_resolver,
        )

        result = await resolver.resolve_override(active_tenant, _make_request())

        assert result is active_entitlement


# ═══════════════════════════════════════════════════════════════════════════════
# Ambiguous multi-match
# ═══════════════════════════════════════════════════════════════════════════════


class TestAmbiguousEntitlement:
    @pytest.mark.asyncio
    async def test_raises_ambiguous_error_for_two_active_candidates(self, active_tenant):
        """Two active candidates → AmbiguousUserEntitlementError with context."""
        ent_a = build_user_entitlement_config(is_active=True)
        ent_b = build_user_entitlement_config(is_active=True)
        tenant_resolver = TenantResolver(FakeTenantConfigReader(active_tenant))
        resolver = UserEntitlementResolver(
            entitlement_reader=FakeUserEntitlementReader([ent_a, ent_b]),
            tenant_resolver=tenant_resolver,
        )

        with pytest.raises(AmbiguousUserEntitlementError) as exc_info:
            await resolver.resolve_override(active_tenant, _make_request())

        err = exc_info.value
        assert err.deployment_key == DEPLOYMENT_KEY
        assert err.user_id == str(USER_ID)
        assert err.tenant_id == str(TENANT_ID)

    @pytest.mark.asyncio
    async def test_error_message_contains_all_identifiers(self, active_tenant):
        """Error message must include deployment_key, user_id, and tenant_id."""
        ent_a = build_user_entitlement_config(is_active=True)
        ent_b = build_user_entitlement_config(is_active=True)
        tenant_resolver = TenantResolver(FakeTenantConfigReader(active_tenant))
        resolver = UserEntitlementResolver(
            entitlement_reader=FakeUserEntitlementReader([ent_a, ent_b]),
            tenant_resolver=tenant_resolver,
        )

        with pytest.raises(AmbiguousUserEntitlementError) as exc_info:
            await resolver.resolve_override(active_tenant, _make_request())

        message = str(exc_info.value)
        assert DEPLOYMENT_KEY in message
        assert str(USER_ID) in message
        assert str(TENANT_ID) in message


# ═══════════════════════════════════════════════════════════════════════════════
# Tenant provider allow-list enforcement
# ═══════════════════════════════════════════════════════════════════════════════


class TestProviderAllowListEnforcement:
    @pytest.mark.asyncio
    async def test_raises_provider_not_allowed_when_entitlement_provider_is_blocked(
        self, restricted_tenant
    ):
        """Active entitlement for a blocked provider → ProviderNotAllowedError."""
        entitlement = build_user_entitlement_config(
            is_active=True,
            provider_name="anthropic",  # not in restricted_tenant allow-list
        )
        tenant_resolver = TenantResolver(FakeTenantConfigReader(restricted_tenant))
        resolver = UserEntitlementResolver(
            entitlement_reader=FakeUserEntitlementReader([entitlement]),
            tenant_resolver=tenant_resolver,
        )

        with pytest.raises(ProviderNotAllowedError) as exc_info:
            await resolver.resolve_override(restricted_tenant, _make_request())

        assert exc_info.value.provider_name == "anthropic"

    @pytest.mark.asyncio
    async def test_passes_when_entitlement_provider_is_allowed(
        self, restricted_tenant, active_entitlement
    ):
        """Active entitlement for allowed provider 'openai' → returns entitlement."""
        tenant_resolver = TenantResolver(FakeTenantConfigReader(restricted_tenant))
        resolver = UserEntitlementResolver(
            entitlement_reader=FakeUserEntitlementReader([active_entitlement]),
            tenant_resolver=tenant_resolver,
        )

        result = await resolver.resolve_override(restricted_tenant, _make_request())

        assert result is active_entitlement
