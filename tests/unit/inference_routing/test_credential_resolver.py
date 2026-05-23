"""
Unit Tests — CredentialResolver
================================

Covers:
    - from_user_entitlement: produces USER scope with correct reference fields
    - from_deployment: produces TENANT scope with correct reference fields
    - cloud_region propagation for both paths
    - No real secrets are ever accessed — only reference strings

Architecture:
-------------
    CredentialResolver (pure static methods — no injected dependencies)

Author: Shubham Singh
"""

from __future__ import annotations

from app.inference_routing.credential_resolver import CredentialResolver
from app.inference_routing.models import CredentialScope
from tests.unit.inference_routing.conftest import (
    build_deployment_config,
    build_user_entitlement_config,
)


class TestFromUserEntitlement:
    def test_returns_user_scope(self, active_entitlement):
        """Entitlement path → credential scope must be USER."""
        selection = CredentialResolver.from_user_entitlement(active_entitlement)

        assert selection.credential_scope == CredentialScope.USER

    def test_secret_reference_matches_entitlement(self, active_entitlement):
        """Secret reference must be taken verbatim from the entitlement config."""
        selection = CredentialResolver.from_user_entitlement(active_entitlement)

        assert selection.secret_reference == active_entitlement.secret_reference

    def test_api_endpoint_matches_entitlement(self, active_entitlement):
        """API endpoint URL comes from the entitlement, not a default."""
        selection = CredentialResolver.from_user_entitlement(active_entitlement)

        assert selection.api_endpoint_url == active_entitlement.api_endpoint_url

    def test_cloud_region_propagated_when_set(self):
        """cloud_region on the entitlement must be forwarded to the selection."""
        entitlement = build_user_entitlement_config(is_active=True)
        # Manually produce a config with a region by patching model fields
        entitlement_with_region = entitlement.model_copy(update={"cloud_region": "us-east-1"})

        selection = CredentialResolver.from_user_entitlement(entitlement_with_region)

        assert selection.cloud_region == "us-east-1"

    def test_cloud_region_is_none_when_not_set(self, active_entitlement):
        """cloud_region must be None when the entitlement has no region."""
        selection = CredentialResolver.from_user_entitlement(active_entitlement)

        assert selection.cloud_region is None


class TestFromDeployment:
    def test_returns_tenant_scope(self, active_deployment):
        """Deployment path → credential scope must be TENANT."""
        selection = CredentialResolver.from_deployment(active_deployment)

        assert selection.credential_scope == CredentialScope.TENANT

    def test_secret_reference_matches_deployment(self, active_deployment):
        """Secret reference must come from the deployment config."""
        selection = CredentialResolver.from_deployment(active_deployment)

        assert selection.secret_reference == active_deployment.secret_reference

    def test_api_endpoint_matches_deployment(self, active_deployment):
        """API endpoint URL comes from the deployment config."""
        selection = CredentialResolver.from_deployment(active_deployment)

        assert selection.api_endpoint_url == active_deployment.api_endpoint_url

    def test_cloud_region_propagated_when_set(self):
        """cloud_region in the deployment must be forwarded to the selection."""
        deployment = build_deployment_config()
        deployment_with_region = deployment.model_copy(update={"cloud_region": "eu-west-1"})

        selection = CredentialResolver.from_deployment(deployment_with_region)

        assert selection.cloud_region == "eu-west-1"

    def test_cloud_region_is_none_when_not_set(self, active_deployment):
        """cloud_region defaults to None when deployment has no region."""
        selection = CredentialResolver.from_deployment(active_deployment)

        assert selection.cloud_region is None


class TestScopeDistinction:
    def test_entitlement_and_deployment_produce_different_scopes(
        self, active_entitlement, active_deployment
    ):
        """The two factory methods must produce distinct credential scopes."""
        user_sel = CredentialResolver.from_user_entitlement(active_entitlement)
        tenant_sel = CredentialResolver.from_deployment(active_deployment)

        assert user_sel.credential_scope != tenant_sel.credential_scope
        assert user_sel.credential_scope == CredentialScope.USER
        assert tenant_sel.credential_scope == CredentialScope.TENANT
