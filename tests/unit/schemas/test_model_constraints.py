"""Tests for the shared model lifecycle and sampling-parameter contract."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.enums import (
    ModelLifecycleStatus,
    ProviderCatalogAuthMode,
    ProviderCatalogType,
    TenantDeploymentStatus,
    TenantLifecycleStatus,
    TenantMembershipStatus,
    TenantSubscriptionTier,
    UserAccountStatus,
    UserEntitlementStatus,
)
from app.schemas.management_schema import (
    EntitlementCreateRequest,
    ModelCreateRequest,
    ProviderCreateRequest,
    TenantCreateRequest,
    UserCreateRequest,
)
from app.schemas.model_constraints import (
    MAX_TEMPERATURE,
    MAX_TOP_P,
    MIN_TEMPERATURE,
    MIN_TOP_P,
    validate_temperature,
    validate_top_p,
)


def test_model_lifecycle_status_matches_database_vocabulary() -> None:
    """REQ: Python lifecycle vocabulary matches the model-catalog CHECK constraint."""
    assert {status.value for status in ModelLifecycleStatus} == {"active", "deprecated", "retired"}


def test_provider_catalog_enums_match_database_vocabulary() -> None:
    """REQ: catalog enum values match the provider-catalog CHECK constraints."""
    assert {value.value for value in ProviderCatalogType} == {
        "direct_api",
        "cloud_api",
        "self_hosted",
        "gateway",
    }
    assert {value.value for value in ProviderCatalogAuthMode} == {
        "bearer_token",
        "api_key_header",
        "aws_sigv4",
        "oauth",
        "custom",
    }


def test_tenant_deployment_status_matches_database_vocabulary() -> None:
    """REQ: deployment lifecycle values match the tenant-deployment CHECK constraint."""
    assert {value.value for value in TenantDeploymentStatus} == {
        "active",
        "inactive",
        "maintenance",
    }


def test_tenant_membership_status_matches_database_vocabulary() -> None:
    """REQ: membership lifecycle values match the membership-table CHECK constraint."""
    assert {value.value for value in TenantMembershipStatus} == {
        "active",
        "suspended",
        "inactive",
    }


def test_tenant_enums_match_database_vocabulary() -> None:
    """REQ: tenant lifecycle and subscription values match table constraints."""
    assert {value.value for value in TenantLifecycleStatus} == {
        "active",
        "trial",
        "suspended",
        "deleted",
    }
    assert {value.value for value in TenantSubscriptionTier} == {
        "free",
        "starter",
        "professional",
        "enterprise",
    }


def test_user_account_status_matches_database_vocabulary() -> None:
    """REQ: user account lifecycle values match the users-table constraint."""
    assert {value.value for value in UserAccountStatus} == {
        "active",
        "suspended",
        "inactive",
        "deleted",
    }


def test_user_entitlement_status_matches_database_vocabulary() -> None:
    """REQ: entitlement lifecycle values match the entitlement-table constraint."""
    assert {value.value for value in UserEntitlementStatus} == {
        "active",
        "inactive",
        "revoked",
    }


def test_tenant_create_request_uses_shared_tenant_enums() -> None:
    """REQ: management API validates tenant values from the shared contract."""
    request = TenantCreateRequest.model_validate(
        {"tenant_name": "Acme", "tenant_slug": "acme", "tier": "enterprise"}
    )

    assert request.status is TenantLifecycleStatus.ACTIVE
    assert request.tier is TenantSubscriptionTier.ENTERPRISE


def test_user_create_request_uses_shared_account_status() -> None:
    """REQ: management API validates the shared user-account lifecycle enum."""
    request = UserCreateRequest.model_validate(
        {
            "username": "alice",
            "email": "alice@example.com",
            "first_name": "Alice",
            "last_name": "Example",
            "password": "long-enough-password",
        }
    )

    assert request.status is UserAccountStatus.ACTIVE


def test_entitlement_create_request_uses_shared_entitlement_status() -> None:
    """REQ: management API validates the shared entitlement lifecycle enum."""
    request = EntitlementCreateRequest.model_validate(
        {
            "tenant_id": "00000000-0000-0000-0000-000000000001",
            "deployment_key": "primary",
            "provider_id": "00000000-0000-0000-0000-000000000002",
            "model_id": "00000000-0000-0000-0000-000000000003",
            "entitlement_name": "personal-key",
            "api_endpoint_url": "https://example.com",
            "credential": {"auth_mode": "bearer_token", "api_key": "sk-example"},
        }
    )

    assert request.status is UserEntitlementStatus.ACTIVE


@pytest.mark.parametrize("invalid_value", [True, float("nan"), float("inf"), -0.01, 2.01])
def test_validate_temperature_with_invalid_value_raises_value_error(invalid_value: float) -> None:
    """REQ: persistence rejects non-finite and out-of-range temperatures."""
    with pytest.raises(ValueError, match="temperature"):
        validate_temperature(invalid_value, "temperature")


@pytest.mark.parametrize("invalid_value", [True, float("nan"), float("inf"), -0.001, 1.001])
def test_validate_top_p_with_invalid_value_raises_value_error(invalid_value: float) -> None:
    """REQ: persistence rejects non-finite and out-of-range top-p values."""
    with pytest.raises(ValueError, match="top_p"):
        validate_top_p(invalid_value, "top_p")


def test_provider_create_request_uses_catalog_enums() -> None:
    """REQ: management API validates catalog type and auth-mode vocabulary."""
    request = ProviderCreateRequest.model_validate(
        {
            "provider_name": "example_provider",
            "display_name": "Example Provider",
            "supported_operations": ["chat"],
            "provider_type": "cloud_api",
            "auth_mode": "custom",
        }
    )

    assert request.provider_type is ProviderCatalogType.CLOUD_API
    assert request.auth_mode is ProviderCatalogAuthMode.CUSTOM


@pytest.mark.parametrize("temperature", [float(MIN_TEMPERATURE), float(MAX_TEMPERATURE)])
def test_model_schema_accepts_temperature_at_shared_bounds(temperature: float) -> None:
    """REQ: inclusive temperature bounds are accepted at the API boundary."""
    request = ModelCreateRequest(
        model_name="example-model",
        supported_operations=["chat"],
        default_temperature=temperature,
    )

    assert request.default_temperature == temperature


@pytest.mark.parametrize("top_p", [float(MIN_TOP_P), float(MAX_TOP_P)])
def test_model_schema_accepts_top_p_at_shared_bounds(top_p: float) -> None:
    """REQ: inclusive top-p bounds are accepted at the API boundary."""
    request = ModelCreateRequest(
        model_name="example-model",
        supported_operations=["chat"],
        default_top_p=top_p,
    )

    assert request.default_top_p == top_p


def test_model_schema_rejects_temperature_outside_shared_bounds() -> None:
    """REQ: API validation rejects a value beyond the fixed safety limit."""
    with pytest.raises(ValidationError):
        ModelCreateRequest(
            model_name="example-model",
            supported_operations=["chat"],
            default_temperature=float(MAX_TEMPERATURE) + 0.01,
        )
