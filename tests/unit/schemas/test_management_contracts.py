"""Specification tests for management request and response boundaries."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.management_responses import DeploymentResponse, UserResponse
from app.schemas.management_schema import (
    BearerCredential,
    ProviderCreateRequest,
    UserCreateRequest,
)


def test_user_create_request_redacts_plaintext_password_from_repr() -> None:
    """REQ: accidental model logging must not reveal a submitted password."""
    password = "correct-horse-battery-staple"
    request = UserCreateRequest.model_validate(
        {
            "username": "new-user",
            "email": "new-user@example.com",
            "first_name": "New",
            "last_name": "User",
            "password": password,
        }
    )

    rendered = repr(request)

    assert password not in rendered
    assert "**********" in rendered


def test_bearer_credential_redacts_api_key_from_repr() -> None:
    """REQ: accidental credential-model logging must not reveal provider keys."""
    api_key = "provider-secret-key"
    credential = BearerCredential.model_validate(
        {"auth_mode": "bearer_token", "api_key": api_key}
    )

    rendered = repr(credential)

    assert api_key not in rendered
    assert "**********" in rendered


@pytest.mark.parametrize("unsupported_mode", ["oauth", "custom", "aws_sigv4"])
def test_bearer_credential_with_unimplemented_mode_is_rejected(unsupported_mode: str) -> None:
    """REQ: the API cannot persist credential shapes the runtime cannot consume."""
    with pytest.raises(ValidationError):
        BearerCredential.model_validate(
            {"auth_mode": unsupported_mode, "api_key": "provider-secret-key"}
        )


def test_provider_create_request_with_unknown_operation_is_rejected() -> None:
    """REQ: provider capabilities use the gateway's canonical operation vocabulary."""
    with pytest.raises(ValidationError, match="Input should be"):
        ProviderCreateRequest.model_validate(
            {
                "provider_name": "openai",
                "display_name": "OpenAI",
                "supported_operations": ["chat", "arbitrary-provider-operation"],
            }
        )


def test_user_response_with_password_hash_is_rejected() -> None:
    """REQ: sensitive persistence fields cannot silently enter an HTTP response."""
    timestamp = datetime.now(UTC)
    row = {
        "user_id": uuid4(),
        "username": "safe-user",
        "email": "safe-user@example.com",
        "first_name": "Safe",
        "last_name": "User",
        "platform_role": "developer",
        "status": "active",
        "created_at": timestamp,
        "updated_at": timestamp,
        "password_hash": "must-never-leave-persistence",
    }

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        UserResponse.model_validate(row)


def test_deployment_response_with_secret_reference_is_rejected() -> None:
    """REQ: secret-backend locations cannot cross the management API boundary."""
    timestamp = datetime.now(UTC)
    row = {
        "deployment_id": uuid4(),
        "tenant_id": uuid4(),
        "provider_id": uuid4(),
        "model_id": uuid4(),
        "deployment_key": "production-chat",
        "deployment_name": "Production chat",
        "status": "active",
        "api_endpoint_url": "https://api.example.com/v1",
        "cloud_provider": None,
        "cloud_region": None,
        "provider_deployment_name": None,
        "token_capacity_limit": 100_000,
        "token_lock_duration_seconds": 70,
        "timeout_seconds": 30.0,
        "max_retries": 2,
        "default_temperature": 0.7,
        "default_top_p": 1.0,
        "default_max_output_tokens": 4096,
        "is_default": True,
        "routing_priority": 0,
        "extra_headers": {},
        "extra_config": {},
        "created_by_user_id": None,
        "created_at": timestamp,
        "updated_at": timestamp,
        "secret_reference": "vault://must-not-be-public",
    }

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DeploymentResponse.model_validate(row)
