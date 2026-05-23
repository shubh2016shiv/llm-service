"""
Unit Tests — ResolvedExecutionContextFactory
=============================================

Covers build_for_deployment and build_for_user_entitlement:

    build_for_deployment:
        - resolution_source is TENANT_DEPLOYMENT
        - provider/model/endpoint fields come from deployment + credential selection
        - timeout/retries use deployment override when set
        - timeout/retries fall back to provider defaults when not set
        - effective_max_tokens uses deployment override when set
        - effective_max_tokens falls back to model spec when not set
        - quota_key equals deployment_key
        - route_fingerprint is a non-empty hex string

    build_for_user_entitlement:
        - resolution_source is USER_ENTITLEMENT
        - timeout/retries always use provider defaults (no deployment override)
        - effective_max_tokens always equals model spec max_output_tokens
        - quota_key equals str(entitlement_id)
        - route_fingerprint differs from deployment path for same route

    Both paths:
        - route_fingerprint is deterministic (same inputs → same hash)
        - route_fingerprint changes when any routing dimension changes

Architecture:
-------------
    ResolvedExecutionContextFactory (pure function — no injected dependencies)
        called with: TenantConfig, DeploymentConfig/UserEntitlementConfig,
                     ProviderStaticConfig, LLMModelSpec, CredentialSelection

Author: Shubham Singh
"""

from __future__ import annotations

import pytest

from app.inference_routing.context_factory import ResolvedExecutionContextFactory
from app.inference_routing.credential_resolver import CredentialResolver
from app.inference_routing.models import CredentialScope, ResolutionSource
from tests.unit.inference_routing.conftest import (
    DEPLOYMENT_KEY,
    ENTITLEMENT_ID,
    build_deployment_config,
    build_model_spec,
    build_provider_static_config,
)


@pytest.fixture()
def factory() -> ResolvedExecutionContextFactory:
    return ResolvedExecutionContextFactory()


@pytest.fixture()
def model_spec():
    return build_model_spec()


@pytest.fixture()
def provider_cfg():
    return build_provider_static_config()


# ═══════════════════════════════════════════════════════════════════════════════
# build_for_deployment — resolution source and field mapping
# ═══════════════════════════════════════════════════════════════════════════════


class TestBuildForDeployment:
    def test_resolution_source_is_tenant_deployment(
        self, factory, active_tenant, active_deployment, provider_cfg, model_spec
    ):
        selection = CredentialResolver.from_deployment(active_deployment)
        ctx = factory.build_for_deployment(
            tenant_config=active_tenant,
            deployment_config=active_deployment,
            provider_static_config=provider_cfg,
            model_spec=model_spec,
            credential_selection=selection,
        )
        assert ctx.resolution_source == ResolutionSource.TENANT_DEPLOYMENT

    def test_provider_name_comes_from_deployment(
        self, factory, active_tenant, active_deployment, provider_cfg, model_spec
    ):
        selection = CredentialResolver.from_deployment(active_deployment)
        ctx = factory.build_for_deployment(
            tenant_config=active_tenant,
            deployment_config=active_deployment,
            provider_static_config=provider_cfg,
            model_spec=model_spec,
            credential_selection=selection,
        )
        assert ctx.provider_name == active_deployment.provider_name

    def test_model_name_comes_from_deployment(
        self, factory, active_tenant, active_deployment, provider_cfg, model_spec
    ):
        selection = CredentialResolver.from_deployment(active_deployment)
        ctx = factory.build_for_deployment(
            tenant_config=active_tenant,
            deployment_config=active_deployment,
            provider_static_config=provider_cfg,
            model_spec=model_spec,
            credential_selection=selection,
        )
        assert ctx.model_name == active_deployment.model_name

    def test_secret_reference_comes_from_credential_selection(
        self, factory, active_tenant, active_deployment, provider_cfg, model_spec
    ):
        selection = CredentialResolver.from_deployment(active_deployment)
        ctx = factory.build_for_deployment(
            tenant_config=active_tenant,
            deployment_config=active_deployment,
            provider_static_config=provider_cfg,
            model_spec=model_spec,
            credential_selection=selection,
        )
        assert ctx.secret_reference == selection.secret_reference
        assert ctx.credential_scope == CredentialScope.TENANT

    def test_timeout_uses_deployment_override_when_set(
        self, factory, active_tenant, provider_cfg, model_spec
    ):
        deployment = build_deployment_config(timeout_seconds=30.0)
        selection = CredentialResolver.from_deployment(deployment)
        ctx = factory.build_for_deployment(
            tenant_config=active_tenant,
            deployment_config=deployment,
            provider_static_config=provider_cfg,
            model_spec=model_spec,
            credential_selection=selection,
        )
        assert ctx.effective_timeout_seconds == 30.0

    def test_timeout_falls_back_to_provider_default_when_not_set(
        self, factory, active_tenant, active_deployment, model_spec
    ):
        """No deployment override → uses provider_cfg.default_timeout_seconds."""
        provider_cfg = build_provider_static_config(default_timeout_seconds=45.0)
        selection = CredentialResolver.from_deployment(active_deployment)
        ctx = factory.build_for_deployment(
            tenant_config=active_tenant,
            deployment_config=active_deployment,  # timeout_seconds=None
            provider_static_config=provider_cfg,
            model_spec=model_spec,
            credential_selection=selection,
        )
        assert ctx.effective_timeout_seconds == 45.0

    def test_max_retries_uses_deployment_override_when_set(
        self, factory, active_tenant, provider_cfg, model_spec
    ):
        deployment = build_deployment_config(max_retries=5)
        selection = CredentialResolver.from_deployment(deployment)
        ctx = factory.build_for_deployment(
            tenant_config=active_tenant,
            deployment_config=deployment,
            provider_static_config=provider_cfg,
            model_spec=model_spec,
            credential_selection=selection,
        )
        assert ctx.effective_max_retries == 5

    def test_max_retries_falls_back_to_provider_default_when_none(
        self, factory, active_tenant, active_deployment, model_spec
    ):
        provider_cfg = build_provider_static_config(default_max_retries=7)
        selection = CredentialResolver.from_deployment(active_deployment)
        ctx = factory.build_for_deployment(
            tenant_config=active_tenant,
            deployment_config=active_deployment,  # max_retries=None
            provider_static_config=provider_cfg,
            model_spec=model_spec,
            credential_selection=selection,
        )
        assert ctx.effective_max_retries == 7

    def test_max_tokens_uses_deployment_override_when_set(
        self, factory, active_tenant, provider_cfg, model_spec
    ):
        deployment = build_deployment_config(default_max_tokens=1024)
        selection = CredentialResolver.from_deployment(deployment)
        ctx = factory.build_for_deployment(
            tenant_config=active_tenant,
            deployment_config=deployment,
            provider_static_config=provider_cfg,
            model_spec=model_spec,
            credential_selection=selection,
        )
        assert ctx.effective_max_tokens == 1024

    def test_max_tokens_falls_back_to_model_spec_when_not_set(
        self, factory, active_tenant, active_deployment, provider_cfg
    ):
        model_spec = build_model_spec()  # max_output_tokens=4096
        selection = CredentialResolver.from_deployment(active_deployment)
        ctx = factory.build_for_deployment(
            tenant_config=active_tenant,
            deployment_config=active_deployment,  # default_max_tokens=None
            provider_static_config=provider_cfg,
            model_spec=model_spec,
            credential_selection=selection,
        )
        assert ctx.effective_max_tokens == model_spec.max_output_tokens

    def test_quota_key_equals_deployment_key(
        self, factory, active_tenant, active_deployment, provider_cfg, model_spec
    ):
        selection = CredentialResolver.from_deployment(active_deployment)
        ctx = factory.build_for_deployment(
            tenant_config=active_tenant,
            deployment_config=active_deployment,
            provider_static_config=provider_cfg,
            model_spec=model_spec,
            credential_selection=selection,
        )
        assert ctx.quota_key == DEPLOYMENT_KEY

    def test_route_fingerprint_is_nonempty_hex(
        self, factory, active_tenant, active_deployment, provider_cfg, model_spec
    ):
        selection = CredentialResolver.from_deployment(active_deployment)
        ctx = factory.build_for_deployment(
            tenant_config=active_tenant,
            deployment_config=active_deployment,
            provider_static_config=provider_cfg,
            model_spec=model_spec,
            credential_selection=selection,
        )
        assert len(ctx.route_fingerprint) == 64  # SHA-256 hex digest
        int(ctx.route_fingerprint, 16)  # valid hex


# ═══════════════════════════════════════════════════════════════════════════════
# build_for_user_entitlement — resolution source and field mapping
# ═══════════════════════════════════════════════════════════════════════════════


class TestBuildForUserEntitlement:
    def test_resolution_source_is_user_entitlement(
        self, factory, active_tenant, active_entitlement, provider_cfg, model_spec
    ):
        selection = CredentialResolver.from_user_entitlement(active_entitlement)
        ctx = factory.build_for_user_entitlement(
            tenant_config=active_tenant,
            user_entitlement_config=active_entitlement,
            provider_static_config=provider_cfg,
            model_spec=model_spec,
            credential_selection=selection,
        )
        assert ctx.resolution_source == ResolutionSource.USER_ENTITLEMENT

    def test_credential_scope_is_user(
        self, factory, active_tenant, active_entitlement, provider_cfg, model_spec
    ):
        selection = CredentialResolver.from_user_entitlement(active_entitlement)
        ctx = factory.build_for_user_entitlement(
            tenant_config=active_tenant,
            user_entitlement_config=active_entitlement,
            provider_static_config=provider_cfg,
            model_spec=model_spec,
            credential_selection=selection,
        )
        assert ctx.credential_scope == CredentialScope.USER

    def test_timeout_always_uses_provider_default(
        self, factory, active_tenant, active_entitlement, model_spec
    ):
        """Entitlement path never has deployment overrides — must use provider default."""
        provider_cfg = build_provider_static_config(default_timeout_seconds=90.0)
        selection = CredentialResolver.from_user_entitlement(active_entitlement)
        ctx = factory.build_for_user_entitlement(
            tenant_config=active_tenant,
            user_entitlement_config=active_entitlement,
            provider_static_config=provider_cfg,
            model_spec=model_spec,
            credential_selection=selection,
        )
        assert ctx.effective_timeout_seconds == 90.0

    def test_max_tokens_always_equals_model_spec_max_output_tokens(
        self, factory, active_tenant, active_entitlement, provider_cfg
    ):
        model_spec = build_model_spec()
        selection = CredentialResolver.from_user_entitlement(active_entitlement)
        ctx = factory.build_for_user_entitlement(
            tenant_config=active_tenant,
            user_entitlement_config=active_entitlement,
            provider_static_config=provider_cfg,
            model_spec=model_spec,
            credential_selection=selection,
        )
        assert ctx.effective_max_tokens == model_spec.max_output_tokens

    def test_quota_key_equals_entitlement_id_string(
        self, factory, active_tenant, active_entitlement, provider_cfg, model_spec
    ):
        selection = CredentialResolver.from_user_entitlement(active_entitlement)
        ctx = factory.build_for_user_entitlement(
            tenant_config=active_tenant,
            user_entitlement_config=active_entitlement,
            provider_static_config=provider_cfg,
            model_spec=model_spec,
            credential_selection=selection,
        )
        assert ctx.quota_key == str(ENTITLEMENT_ID)

    def test_deployment_config_is_none_on_entitlement_path(
        self, factory, active_tenant, active_entitlement, provider_cfg, model_spec
    ):
        selection = CredentialResolver.from_user_entitlement(active_entitlement)
        ctx = factory.build_for_user_entitlement(
            tenant_config=active_tenant,
            user_entitlement_config=active_entitlement,
            provider_static_config=provider_cfg,
            model_spec=model_spec,
            credential_selection=selection,
        )
        assert ctx.deployment_config is None


# ═══════════════════════════════════════════════════════════════════════════════
# Fingerprint determinism
# ═══════════════════════════════════════════════════════════════════════════════


class TestRouteFingerprintDeterminism:
    def test_same_inputs_produce_identical_fingerprint(
        self, factory, active_tenant, active_deployment, provider_cfg, model_spec
    ):
        selection = CredentialResolver.from_deployment(active_deployment)
        ctx_a = factory.build_for_deployment(
            tenant_config=active_tenant,
            deployment_config=active_deployment,
            provider_static_config=provider_cfg,
            model_spec=model_spec,
            credential_selection=selection,
        )
        ctx_b = factory.build_for_deployment(
            tenant_config=active_tenant,
            deployment_config=active_deployment,
            provider_static_config=provider_cfg,
            model_spec=model_spec,
            credential_selection=selection,
        )
        assert ctx_a.route_fingerprint == ctx_b.route_fingerprint

    def test_entitlement_and_deployment_paths_produce_different_fingerprints(
        self, factory, active_tenant, active_deployment, active_entitlement, provider_cfg, model_spec
    ):
        """Same provider/model but different resolution source → different fingerprint."""
        dep_selection = CredentialResolver.from_deployment(active_deployment)
        ent_selection = CredentialResolver.from_user_entitlement(active_entitlement)

        ctx_dep = factory.build_for_deployment(
            tenant_config=active_tenant,
            deployment_config=active_deployment,
            provider_static_config=provider_cfg,
            model_spec=model_spec,
            credential_selection=dep_selection,
        )
        ctx_ent = factory.build_for_user_entitlement(
            tenant_config=active_tenant,
            user_entitlement_config=active_entitlement,
            provider_static_config=provider_cfg,
            model_spec=model_spec,
            credential_selection=ent_selection,
        )

        assert ctx_dep.route_fingerprint != ctx_ent.route_fingerprint
