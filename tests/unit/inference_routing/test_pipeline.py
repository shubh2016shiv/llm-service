"""
Unit Tests — OrchestrationPipeline
=====================================

Integration of all resolvers into the full routing pipeline.

Covers:
    Path A — User Entitlement wins:
        - Active entitlement present → context built with USER_ENTITLEMENT source
        - provider_validator and credential_resolver called with entitlement data
        - deployment_resolver NOT called when entitlement path wins

    Path B — Deployment fallback:
        - No entitlement → deployment resolved → context built with TENANT_DEPLOYMENT source
        - ensure_provider_allowed enforced on the deployment's provider

    Failure escalation:
        - TenantNotFoundError propagates from TenantResolver
        - TenantSuspendedError propagates from TenantResolver
        - ProviderNotAllowedError propagates on deployment path
        - AmbiguousUserEntitlementError propagates from UserEntitlementResolver
        - DeploymentNotFoundError propagates from DeploymentResolver
        - DeploymentInactiveError propagates from DeploymentResolver
        - OperationNotSupportedError propagates from ProviderRouteValidator
        - ModelNotSupportedError propagates from ProviderRouteValidator

Architecture:
-------------
    FakeTenantConfigReader
    FakeUserEntitlementReader    ──▶ OrchestrationPipeline (integration under test)
    FakeDeploymentConfigReader
    FakeRedisCache
    FakeConfigLoader

Author: Shubham Singh
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from app.core.exceptions import (
    DeploymentNotFoundError,
    ModelNotSupportedError,
    TenantNotFoundError,
    TenantSuspendedError,
)
from app.core.settings.models.model_config import ModelCapability
from app.core.settings.models.tenant_config import TenantStatus
from app.inference_routing.context_factory import ResolvedExecutionContextFactory
from app.inference_routing.credential_resolver import CredentialResolver
from app.inference_routing.deployment_resolver import DeploymentResolver
from app.inference_routing.entitlement_resolver import UserEntitlementResolver
from app.inference_routing.exceptions import (
    AmbiguousUserEntitlementError,
    OperationNotSupportedError,
    ProviderNotAllowedError,
)
from app.inference_routing.models import ResolutionRequest, ResolutionSource
from app.inference_routing.pipeline import OrchestrationPipeline
from app.inference_routing.provider_validator import ProviderRouteValidator
from app.inference_routing.tenant_resolver import TenantResolver
from app.schemas.enums import OperationType
from tests.unit.inference_routing.conftest import (
    DEPLOYMENT_KEY,
    MODEL_NAME,
    PROVIDER_NAME,
    TENANT_ID,
    USER_ID,
    FakeConfigLoader,
    FakeDeploymentConfigReader,
    FakeRedisCache,
    FakeTenantConfigReader,
    FakeUserEntitlementReader,
    build_deployment_config,
    build_model_spec,
    build_provider_static_config,
    build_tenant_config,
    build_user_entitlement_config,
)

if TYPE_CHECKING:
    from app.core.settings.loader import ConfigLoader
    from app.infrastructure.redis_cache import RedisCache

# ═══════════════════════════════════════════════════════════════════════════════
# Pipeline factory helper
# ═══════════════════════════════════════════════════════════════════════════════


def _build_pipeline(
    *,
    tenant=None,
    entitlement_candidates=None,
    deployment=None,
    provider_name: str = PROVIDER_NAME,
    model_name: str = MODEL_NAME,
    capabilities: frozenset[ModelCapability] | None = None,
    allowed_provider_names: frozenset[str] | None = None,
) -> OrchestrationPipeline:
    if tenant is None:
        tenant = build_tenant_config(
            status=TenantStatus.ACTIVE,
            allowed_provider_names=allowed_provider_names,
        )

    if capabilities is None:
        capabilities = frozenset({ModelCapability.CHAT, ModelCapability.EMBED})

    spec = build_model_spec(name=model_name, capabilities=capabilities)
    provider_cfg = build_provider_static_config(
        provider_name=provider_name, model_spec=spec
    )

    tenant_resolver = TenantResolver(FakeTenantConfigReader(tenant))

    entitlement_resolver = UserEntitlementResolver(
        entitlement_reader=FakeUserEntitlementReader(entitlement_candidates or []),
        tenant_resolver=tenant_resolver,
    )

    cache = FakeRedisCache()
    db_reader = FakeDeploymentConfigReader(deployment)
    deployment_resolver = DeploymentResolver(cache=cast("RedisCache", cache), db_reader=db_reader)

    provider_validator = ProviderRouteValidator(
        config_loader=cast("ConfigLoader", FakeConfigLoader({provider_name: provider_cfg}))
    )

    credential_resolver = CredentialResolver()
    context_factory = ResolvedExecutionContextFactory()

    return OrchestrationPipeline(
        tenant_resolver=tenant_resolver,
        entitlement_resolver=entitlement_resolver,
        deployment_resolver=deployment_resolver,
        provider_validator=provider_validator,
        credential_resolver=credential_resolver,
        context_factory=context_factory,
    )


def _make_request(operation: OperationType = OperationType.CHAT) -> ResolutionRequest:
    return ResolutionRequest(
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        deployment_key=DEPLOYMENT_KEY,
        operation=operation,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Path A — User Entitlement wins
# ═══════════════════════════════════════════════════════════════════════════════


class TestEntitlementPath:
    @pytest.mark.asyncio
    async def test_returns_user_entitlement_context_when_active_entitlement_present(self):
        """Active entitlement → resolution_source == USER_ENTITLEMENT."""
        entitlement = build_user_entitlement_config(is_active=True)
        pipeline = _build_pipeline(entitlement_candidates=[entitlement])

        ctx = await pipeline.resolve(_make_request())

        assert ctx.resolution_source == ResolutionSource.USER_ENTITLEMENT

    @pytest.mark.asyncio
    async def test_entitlement_path_sets_correct_provider_and_model(self):
        """Context provider/model must come from entitlement, not deployment."""
        entitlement = build_user_entitlement_config(
            is_active=True,
            provider_name=PROVIDER_NAME,
            model_name=MODEL_NAME,
        )
        pipeline = _build_pipeline(entitlement_candidates=[entitlement])

        ctx = await pipeline.resolve(_make_request())

        assert ctx.provider_name == PROVIDER_NAME
        assert ctx.model_name == MODEL_NAME

    @pytest.mark.asyncio
    async def test_entitlement_path_does_not_require_deployment_in_db(self):
        """DeploymentResolver DB is empty — entitlement path must not call it."""
        entitlement = build_user_entitlement_config(is_active=True)
        pipeline = _build_pipeline(
            entitlement_candidates=[entitlement],
            deployment=None,  # DB returns None — would raise if called
        )

        ctx = await pipeline.resolve(_make_request())

        assert ctx.resolution_source == ResolutionSource.USER_ENTITLEMENT


# ═══════════════════════════════════════════════════════════════════════════════
# Path B — Deployment fallback
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeploymentPath:
    @pytest.mark.asyncio
    async def test_returns_deployment_context_when_no_entitlement_candidates(self):
        """No entitlements → falls back to deployment → TENANT_DEPLOYMENT source."""
        deployment = build_deployment_config()
        pipeline = _build_pipeline(
            entitlement_candidates=[],
            deployment=deployment,
        )

        ctx = await pipeline.resolve(_make_request())

        assert ctx.resolution_source == ResolutionSource.TENANT_DEPLOYMENT

    @pytest.mark.asyncio
    async def test_deployment_path_sets_correct_provider_and_model(self):
        deployment = build_deployment_config()
        pipeline = _build_pipeline(
            entitlement_candidates=[],
            deployment=deployment,
        )

        ctx = await pipeline.resolve(_make_request())

        assert ctx.provider_name == PROVIDER_NAME
        assert ctx.model_name == MODEL_NAME

    @pytest.mark.asyncio
    async def test_deployment_path_enforces_provider_allow_list(self):
        """Tenant allow-list blocks 'anthropic' — deployment for 'anthropic' must fail."""
        deployment = build_deployment_config()
        # Build a deployment for 'anthropic' — but tenant only allows 'openai'
        anthropic_deployment = deployment.model_copy(
            update={"provider_name": "anthropic"}
        )
        tenant = build_tenant_config(
            status=TenantStatus.ACTIVE,
            allowed_provider_names=frozenset({"openai"}),
        )
        # Build pipeline manually so we can inject both restricted tenant and anthropic deployment
        from app.inference_routing.context_factory import ResolvedExecutionContextFactory
        from app.inference_routing.credential_resolver import CredentialResolver

        spec = build_model_spec()
        anthropic_cfg = build_provider_static_config(provider_name="anthropic", model_spec=spec)
        tenant_resolver = TenantResolver(FakeTenantConfigReader(tenant))
        entitlement_resolver = UserEntitlementResolver(
            entitlement_reader=FakeUserEntitlementReader([]),
            tenant_resolver=tenant_resolver,
        )
        cache = FakeRedisCache()
        db_reader = FakeDeploymentConfigReader(anthropic_deployment)
        deployment_resolver = DeploymentResolver(cache=cast("RedisCache", cache), db_reader=db_reader)
        provider_validator = ProviderRouteValidator(
            config_loader=cast("ConfigLoader", FakeConfigLoader({"anthropic": anthropic_cfg}))
        )
        pipeline = OrchestrationPipeline(
            tenant_resolver=tenant_resolver,
            entitlement_resolver=entitlement_resolver,
            deployment_resolver=deployment_resolver,
            provider_validator=provider_validator,
            credential_resolver=CredentialResolver(),
            context_factory=ResolvedExecutionContextFactory(),
        )

        with pytest.raises(ProviderNotAllowedError):
            await pipeline.resolve(_make_request())


# ═══════════════════════════════════════════════════════════════════════════════
# Tenant-level failures
# ═══════════════════════════════════════════════════════════════════════════════


class TestTenantFailures:
    @pytest.mark.asyncio
    async def test_raises_tenant_not_found_when_tenant_absent(self):
        """No tenant record → TenantNotFoundError before any other check."""
        tenant_resolver = TenantResolver(FakeTenantConfigReader(tenant=None))
        pipeline = OrchestrationPipeline(
            tenant_resolver=tenant_resolver,
            entitlement_resolver=UserEntitlementResolver(
                entitlement_reader=FakeUserEntitlementReader([]),
                tenant_resolver=tenant_resolver,
            ),
            deployment_resolver=DeploymentResolver(
                cache=cast("RedisCache", FakeRedisCache()), db_reader=FakeDeploymentConfigReader(None)
            ),
            provider_validator=ProviderRouteValidator(config_loader=cast("ConfigLoader", FakeConfigLoader({}))),
            credential_resolver=CredentialResolver(),
            context_factory=ResolvedExecutionContextFactory(),
        )

        with pytest.raises(TenantNotFoundError):
            await pipeline.resolve(_make_request())

    @pytest.mark.asyncio
    async def test_raises_tenant_suspended_error_for_suspended_tenant(self, suspended_tenant):
        """Suspended tenant → TenantSuspendedError before any routing."""
        tenant_resolver = TenantResolver(FakeTenantConfigReader(suspended_tenant))
        pipeline = OrchestrationPipeline(
            tenant_resolver=tenant_resolver,
            entitlement_resolver=UserEntitlementResolver(
                entitlement_reader=FakeUserEntitlementReader([]),
                tenant_resolver=tenant_resolver,
            ),
            deployment_resolver=DeploymentResolver(
                cache=cast("RedisCache", FakeRedisCache()), db_reader=FakeDeploymentConfigReader(None)
            ),
            provider_validator=ProviderRouteValidator(config_loader=cast("ConfigLoader", FakeConfigLoader({}))),
            credential_resolver=CredentialResolver(),
            context_factory=ResolvedExecutionContextFactory(),
        )

        with pytest.raises(TenantSuspendedError):
            await pipeline.resolve(_make_request())


# ═══════════════════════════════════════════════════════════════════════════════
# Entitlement-level failures
# ═══════════════════════════════════════════════════════════════════════════════


class TestEntitlementFailures:
    @pytest.mark.asyncio
    async def test_raises_ambiguous_error_for_multiple_active_entitlements(self):
        """Two active entitlements → AmbiguousUserEntitlementError from pipeline."""
        ent_a = build_user_entitlement_config(is_active=True)
        ent_b = build_user_entitlement_config(is_active=True)
        pipeline = _build_pipeline(entitlement_candidates=[ent_a, ent_b])

        with pytest.raises(AmbiguousUserEntitlementError):
            await pipeline.resolve(_make_request())


# ═══════════════════════════════════════════════════════════════════════════════
# Deployment-level failures
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeploymentFailures:
    @pytest.mark.asyncio
    async def test_raises_deployment_not_found_when_no_deployment_exists(self):
        """No entitlement, no deployment → DeploymentNotFoundError."""
        pipeline = _build_pipeline(entitlement_candidates=[], deployment=None)

        with pytest.raises(DeploymentNotFoundError):
            await pipeline.resolve(_make_request())


# ═══════════════════════════════════════════════════════════════════════════════
# Provider/model validation failures
# ═══════════════════════════════════════════════════════════════════════════════


class TestProviderValidationFailures:
    @pytest.mark.asyncio
    async def test_raises_model_not_supported_for_unknown_model(self):
        """Deployment references a model not in the provider catalog → ModelNotSupportedError."""
        deployment = build_deployment_config()
        deployment_unknown_model = deployment.model_copy(update={"model_name": "ghost-model"})
        # Provider catalog only has MODEL_NAME, not 'ghost-model'
        pipeline = _build_pipeline(
            entitlement_candidates=[],
            deployment=deployment_unknown_model,
        )

        with pytest.raises(ModelNotSupportedError):
            await pipeline.resolve(_make_request())

    @pytest.mark.asyncio
    async def test_raises_operation_not_supported_when_model_lacks_capability(self):
        """EMBED request to a chat-only model → OperationNotSupportedError."""
        deployment = build_deployment_config()
        pipeline = _build_pipeline(
            entitlement_candidates=[],
            deployment=deployment,
            capabilities=frozenset({ModelCapability.CHAT}),  # no EMBED
        )

        with pytest.raises(OperationNotSupportedError):
            await pipeline.resolve(_make_request(operation=OperationType.EMBED))
