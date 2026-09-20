"""Validation tests for database-controlled outbound provider endpoints."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.unit.inference_routing.conftest import build_user_entitlement_config


@pytest.mark.parametrize(
    "endpoint",
    [
        "file:///etc/passwd",
        "provider.example/v1",
        "https://user:password@provider.example/v1",
        "https://provider.example/v1#fragment",
    ],
)
def test_entitlement_with_unsafe_endpoint_is_rejected(endpoint: str) -> None:
    """Only clean absolute HTTP(S) base URLs may reach outbound adapters."""
    with pytest.raises(ValidationError):
        build_user_entitlement_config(api_endpoint_url=endpoint)


def test_entitlement_endpoint_trailing_slash_is_normalized() -> None:
    """URL composition does not accidentally create double path separators."""
    entitlement = build_user_entitlement_config(
        api_endpoint_url="https://provider.example/v1/"
    )

    assert entitlement.api_endpoint_url == "https://provider.example/v1"
