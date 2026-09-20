"""Tests for the isolated local-infrastructure dashboard token helper."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from jose import jwt

from infrastructure.local_stack.environment import LocalInfrastructureEnvironment
from infrastructure.local_stack.jwt_token import generate_dashboard_jwt

USER_ID = UUID("10000000-0000-0000-0000-000000000099")


def _environment(role: str = "owner") -> LocalInfrastructureEnvironment:
    return LocalInfrastructureEnvironment(
        postgres_user="postgres",
        postgres_password="postgres-password",
        postgres_database="llm_services",
        redis_password="redis-password",
        vault_root_token="vault-root-token",
        vault_service_username="llm-service",
        vault_service_password="vault-password",
        vault_kv_prefix="llm-provider-service",
        jwt_secret_key="test-local-infrastructure-signing-key",
        jwt_algorithm="HS256",
        jwt_issuer="llm-local-infrastructure",
        jwt_audience="llm-provider-service",
        jwt_access_token_expire_hours=1,
        dashboard_jwt_user_id=USER_ID,
        dashboard_jwt_role=role,
    )


def test_generate_dashboard_jwt_matches_backend_claim_contract() -> None:
    environment = _environment()

    token = generate_dashboard_jwt(environment)
    claims = jwt.decode(
        token,
        environment.jwt_secret_key,
        algorithms=[environment.jwt_algorithm],
        issuer=environment.jwt_issuer,
        audience=environment.jwt_audience,
    )

    assert claims["sub"] == str(USER_ID)
    assert claims["role"] == "owner"
    assert claims["type"] == "access"
    assert claims["iss"] == environment.jwt_issuer
    assert claims["aud"] == environment.jwt_audience
    assert UUID(claims["jti"])
    assert claims["exp"] - claims["iat"] == 60 * 60
    assert datetime.fromtimestamp(claims["exp"], tz=UTC) > datetime.now(UTC)


def test_generate_dashboard_jwt_rejects_unknown_role() -> None:
    with pytest.raises(ValueError, match="LOCAL_DASHBOARD_JWT_ROLE"):
        generate_dashboard_jwt(_environment(role="superuser"))
