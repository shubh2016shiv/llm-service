"""
Unit Tests — TenantResolver
============================

Covers:
    - resolve_tenant: returns config for active tenant
    - resolve_tenant: raises TenantNotFoundError when reader returns None
    - resolve_tenant: raises TenantSuspendedError for suspended tenant
    - resolve_tenant: TRIAL status is treated as active (is_active == True)
    - ensure_provider_allowed: passes when allow-list is None (all allowed)
    - ensure_provider_allowed: passes when provider is in the allow-list
    - ensure_provider_allowed: raises ProviderNotAllowedError when blocked

Architecture:
-------------
    FakeTenantConfigReader ──▶ TenantResolver (unit under test)

Author: Shubham Singh
"""

from __future__ import annotations

import pytest

from app.core.exceptions import TenantNotFoundError, TenantSuspendedError
from app.core.settings.models.tenant_config import TenantStatus
from app.inference_routing.exceptions import ProviderNotAllowedError
from app.inference_routing.tenant_resolver import TenantResolver
from tests.unit.inference_routing.conftest import (
    FakeTenantConfigReader,
    build_tenant_config,
)

# ═══════════════════════════════════════════════════════════════════════════════
# resolve_tenant
# ═══════════════════════════════════════════════════════════════════════════════


class TestResolveTenant:
    @pytest.mark.asyncio
    async def test_returns_tenant_config_when_active(self, active_tenant):
        """Happy path — active tenant is returned without modification."""
        resolver = TenantResolver(FakeTenantConfigReader(active_tenant))

        result = await resolver.resolve_tenant(active_tenant.tenant_id)

        assert result is active_tenant

    @pytest.mark.asyncio
    async def test_raises_not_found_when_reader_returns_none(self):
        """Reader returns None → TenantNotFoundError with the queried ID."""
        resolver = TenantResolver(FakeTenantConfigReader(tenant=None))

        with pytest.raises(TenantNotFoundError) as exc_info:
            await resolver.resolve_tenant("missing-tenant-id")

        assert "missing-tenant-id" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_raises_suspended_error_for_suspended_tenant(self, suspended_tenant):
        """Suspended tenant → TenantSuspendedError referencing the tenant ID."""
        resolver = TenantResolver(FakeTenantConfigReader(suspended_tenant))

        with pytest.raises(TenantSuspendedError) as exc_info:
            await resolver.resolve_tenant(suspended_tenant.tenant_id)

        assert str(suspended_tenant.tenant_id) in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_trial_tenant_is_treated_as_active(self):
        """TRIAL status is included in is_active — resolver must not reject it."""
        trial_tenant = build_tenant_config(status=TenantStatus.TRIAL)
        resolver = TenantResolver(FakeTenantConfigReader(trial_tenant))

        result = await resolver.resolve_tenant(trial_tenant.tenant_id)

        assert result is trial_tenant

    @pytest.mark.asyncio
    async def test_deleted_tenant_is_rejected(self):
        """DELETED status is not active — resolver raises TenantSuspendedError."""
        deleted_tenant = build_tenant_config(status=TenantStatus.DELETED)
        resolver = TenantResolver(FakeTenantConfigReader(deleted_tenant))

        with pytest.raises(TenantSuspendedError):
            await resolver.resolve_tenant(deleted_tenant.tenant_id)


# ═══════════════════════════════════════════════════════════════════════════════
# ensure_provider_allowed
# ═══════════════════════════════════════════════════════════════════════════════


class TestEnsureProviderAllowed:
    def test_passes_when_allow_list_is_none(self, active_tenant):
        """None allow-list means all providers permitted — must not raise."""
        assert active_tenant.allowed_provider_names is None
        TenantResolver.ensure_provider_allowed(active_tenant, "openai")
        TenantResolver.ensure_provider_allowed(active_tenant, "anthropic")
        TenantResolver.ensure_provider_allowed(active_tenant, "bedrock")

    def test_passes_when_provider_is_in_allow_list(self, restricted_tenant):
        """Provider in the explicit allow-list → no exception raised."""
        TenantResolver.ensure_provider_allowed(restricted_tenant, "openai")

    def test_raises_when_provider_not_in_allow_list(self, restricted_tenant):
        """Provider absent from allow-list → ProviderNotAllowedError."""
        with pytest.raises(ProviderNotAllowedError) as exc_info:
            TenantResolver.ensure_provider_allowed(restricted_tenant, "anthropic")

        err = exc_info.value
        assert err.provider_name == "anthropic"
        assert err.tenant_id == str(restricted_tenant.tenant_id)

    def test_error_contains_provider_and_tenant_identifiers(self, restricted_tenant):
        """Error message must surface both identifiers for debuggability."""
        with pytest.raises(ProviderNotAllowedError) as exc_info:
            TenantResolver.ensure_provider_allowed(restricted_tenant, "bedrock")

        message = str(exc_info.value)
        assert "bedrock" in message
        assert str(restricted_tenant.tenant_id) in message
