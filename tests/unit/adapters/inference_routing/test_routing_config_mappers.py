"""Direct boundary tests for inference-routing PostgreSQL row mappers.

Architecture:
    Raw projection -> routing_config_mappers -> validated routing model

These tests pin the fail-closed conversion behavior independently of the
PostgreSQL reader that calls the mappers in production.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.adapters.inference_routing.routing_config_mappers import (
    convert_entitlement_row,
    convert_tenant_row,
)
from app.schemas.enums import TenantLifecycleStatus, TenantSubscriptionTier
from tests.unit.adapters.inference_routing.routing_row_fakes import (
    entitlement_row,
    tenant_row,
)
from tests.unit.inference_routing.conftest import (
    ENTITLEMENT_ID,
    PROVIDER_NAME,
    TENANT_ID,
)


def test_convert_tenant_row_with_valid_projection_returns_typed_config() -> None:
    """A complete SQL projection crosses into the frozen tenant model."""
    # Arrange
    row = tenant_row()

    # Act
    tenant = convert_tenant_row(row)

    # Assert
    assert tenant.tenant_id == TENANT_ID
    assert tenant.status == TenantLifecycleStatus.ACTIVE
    assert tenant.tier == TenantSubscriptionTier.ENTERPRISE
    assert tenant.rate_limits.rpm == 100
    assert tenant.rate_limits.tpm == 10_000
    assert tenant.rate_limits.concurrent_requests == 5
    assert tenant.allowed_provider_names == frozenset({PROVIDER_NAME})


def test_convert_tenant_row_with_null_provider_allow_list_preserves_none() -> None:
    """A database NULL remains the model's unrestricted-provider sentinel."""
    # Arrange
    row = tenant_row()
    row["allowed_provider_names"] = None

    # Act
    tenant = convert_tenant_row(row)

    # Assert
    assert tenant.allowed_provider_names is None


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("status", "unknown-status"),
        ("tier", "unknown-tier"),
    ],
)
def test_convert_tenant_row_with_unknown_enum_value_raises_value_error(
    field_name: str,
    invalid_value: str,
) -> None:
    """Unknown database enum strings fail before influencing route policy."""
    # Arrange
    row = tenant_row()
    row[field_name] = invalid_value

    # Act / Assert
    with pytest.raises(ValueError, match=invalid_value):
        convert_tenant_row(row)


def test_convert_tenant_row_with_invalid_rate_limit_raises_validation_error() -> None:
    """Typed-model constraints reject structurally invalid database values."""
    # Arrange
    row = tenant_row()
    row["rate_limit_requests_per_minute"] = 0

    # Act / Assert
    with pytest.raises(ValidationError):
        convert_tenant_row(row)


def test_convert_entitlement_row_with_valid_projection_returns_typed_config() -> None:
    """A complete entitlement projection preserves its authorized identity."""
    # Arrange
    row = entitlement_row()

    # Act
    entitlement = convert_entitlement_row(row)

    # Assert
    assert entitlement.entitlement_id == ENTITLEMENT_ID
    assert entitlement.tenant_id == TENANT_ID
    assert entitlement.provider_name == PROVIDER_NAME
    assert entitlement.secret_reference == "secret/user/openai-key"
    assert entitlement.extra_config == {"owner": "user"}
    assert entitlement.is_active is True


@pytest.mark.parametrize(
    ("status", "expected_is_active"),
    [
        ("active", True),
        ("inactive", False),
        ("revoked", False),
    ],
)
def test_convert_entitlement_row_maps_supported_status(
    status: str,
    expected_is_active: bool,
) -> None:
    """Every supported status maps independently of the SQL active filter."""
    # Arrange
    row = entitlement_row()
    row["status"] = status

    # Act
    entitlement = convert_entitlement_row(row)

    # Assert
    assert entitlement.is_active is expected_is_active


def test_convert_entitlement_row_with_unknown_status_raises_value_error() -> None:
    """An unknown entitlement status fails closed at the mapper boundary."""
    # Arrange
    row = entitlement_row()
    row["status"] = "unknown-status"

    # Act / Assert
    with pytest.raises(ValueError, match="unknown-status"):
        convert_entitlement_row(row)


def test_convert_entitlement_row_with_invalid_endpoint_raises_validation_error() -> None:
    """Pydantic validation rejects unsafe provider endpoint projections."""
    # Arrange
    row = entitlement_row()
    row["api_endpoint_url"] = "not-a-url"

    # Act / Assert
    with pytest.raises(ValidationError):
        convert_entitlement_row(row)
