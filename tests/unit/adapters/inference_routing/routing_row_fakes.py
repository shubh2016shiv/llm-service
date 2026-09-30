"""Build realistic PostgreSQL projections for inference-routing adapter tests.

Architecture:
    Adapter tests -> row builders -> routing reader and mapper boundaries

Each call returns a fresh dictionary so tests can safely mutate individual
fields without sharing state.
"""

from __future__ import annotations

from typing import Any

from tests.unit.inference_routing.conftest import (
    API_ENDPOINT,
    ENTITLEMENT_ID,
    MODEL_NAME,
    PROVIDER_NAME,
    TENANT_ID,
    USER_ID,
)


def tenant_row() -> dict[str, Any]:
    """Build a valid tenant routing projection."""
    return {
        "tenant_id": TENANT_ID,
        "tenant_name": "Acme",
        "tenant_slug": "acme",
        "status": "active",
        "tier": "enterprise",
        "rate_limit_requests_per_minute": 100,
        "rate_limit_tokens_per_minute": 10_000,
        "rate_limit_concurrent_requests": 5,
        "allowed_provider_names": [PROVIDER_NAME],
    }


def entitlement_row() -> dict[str, Any]:
    """Build a valid entitlement routing projection."""
    return {
        "entitlement_id": ENTITLEMENT_ID,
        "user_id": USER_ID,
        "tenant_id": TENANT_ID,
        "entitlement_name": "Personal route",
        "provider_name": PROVIDER_NAME,
        "model_name": MODEL_NAME,
        "api_endpoint_url": API_ENDPOINT,
        "secret_reference": "secret/user/openai-key",
        "cloud_provider": None,
        "cloud_region": None,
        "extra_config": {"owner": "user"},
        "status": "active",
    }
