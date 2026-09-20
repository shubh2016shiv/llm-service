"""Unit tests for inference/management exception -> HTTP status mapping."""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError

from app.api.exception_handlers import (
    _FALLBACK_EXCEPTION_STATUS,
    _on_request_validation_error,
    _resolve_status,
    translate_inference_error,
    translate_management_error,
)
from app.core.exceptions import (
    DeploymentInactiveError,
    DeploymentNotFoundError,
    ProviderError,
    TenantAccessDeniedError,
    TenantNotFoundError,
)
from app.inference_routing.exceptions import AuthorizedEntitlementUnavailableError


def test_translate_inference_error_with_tenant_access_denied_returns_403() -> None:
    """REQ: an access-denied inference call (no membership/role/entitlement) is a 403,
    not the 500 default _resolve_status falls back to for an unmapped exception type.
    """
    exc = TenantAccessDeniedError(
        user_id="7cbb6261-c5fb-4b3d-bda4-13139bfd04cf",
        tenant_id="e4adea8d-6c26-468e-bb8b-0f2e3829bf87",
        required_role="active_entitlement",
    )

    with pytest.raises(HTTPException) as exc_info:
        translate_inference_error(exc)

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert "active_entitlement" in exc_info.value.detail


@pytest.mark.parametrize(
    ("exc", "expected_status"),
    [
        (TenantNotFoundError("tenant-1"), status.HTTP_404_NOT_FOUND),
        (DeploymentNotFoundError("tenant-1", "route-1"), status.HTTP_404_NOT_FOUND),
        (DeploymentInactiveError("route-1", "maintenance"), status.HTTP_422_UNPROCESSABLE_CONTENT),
    ],
)
def test_translate_inference_error_known_mappings(exc: Exception, expected_status: int) -> None:
    """REQ: every exception type raised by tenant_inference_auth.py's gates maps
    to its documented status, not the unmapped-type 500 fallback.
    """
    with pytest.raises(HTTPException) as exc_info:
        translate_inference_error(exc)  # type: ignore[arg-type]

    assert exc_info.value.status_code == expected_status


def test_translate_management_error_with_tenant_access_denied_returns_403() -> None:
    """REQ: the same exception type maps to 403 on the management path too."""
    exc = TenantAccessDeniedError(
        user_id="user-1", tenant_id="tenant-1", required_role="tenant_membership"
    )

    with pytest.raises(HTTPException) as exc_info:
        translate_management_error(exc)

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


def test_ambiguous_user_entitlement_returns_409_not_500() -> None:
    """REQ: two active entitlements matching one route is a 409 conflict in the
    tenant's own data, not a 500. Regression test: this type was raised by
    route_resolution.py but absent from every status map, so _resolve_status
    walked its MRO (ResolutionError -> LLMServiceError), found no entry, and
    fell through to the 500 default — reporting a deterministic, admin-fixable
    config conflict as a server fault that invites a pointless retry.
    """
    exc = AuthorizedEntitlementUnavailableError("gpt4-prod")

    with pytest.raises(HTTPException) as exc_info:
        translate_inference_error(exc)

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    # The message must name the deployment so an admin knows which duplicate to remove.
    assert "gpt4-prod" in exc_info.value.detail


def test_ambiguous_user_entitlement_resolves_to_409_in_fallback_map() -> None:
    """REQ: an instance escaping route-level translation to the global handler
    resolves to 409 there too, rather than reverting to the 500 default.
    """
    exc = AuthorizedEntitlementUnavailableError("route-1")

    assert _resolve_status(exc, _FALLBACK_EXCEPTION_STATUS) == status.HTTP_403_FORBIDDEN


def test_generic_provider_failure_maps_to_bad_gateway() -> None:
    """An upstream protocol failure is a 502 rather than an internal 500."""
    exc = ProviderError("Malformed upstream response.", provider_name="example")

    with pytest.raises(HTTPException) as exc_info:
        translate_inference_error(exc)

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY


@pytest.mark.asyncio
async def test_validation_error_never_echoes_rejected_secret_input() -> None:
    """Credential values are removed from FastAPI/Pydantic validation details."""
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/tenants",
            "headers": [],
            "query_string": b"",
            "state": {"request_id": "request-123"},
        }
    )
    error = RequestValidationError(
        [
            {
                "type": "string_too_short",
                "loc": ("body", "credential", "api_key"),
                "msg": "String should have at least 1 character",
                "input": "super-secret-value",
            }
        ]
    )

    response = await _on_request_validation_error(request, error)
    payload = json.loads(response.body)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert payload["error_code"] == "REQUEST_VALIDATION_ERROR"
    assert payload["request_id"] == "request-123"
    assert "super-secret-value" not in response.body.decode()
    assert payload["errors"][0]["location"] == ["body", "credential", "api_key"]
