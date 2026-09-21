"""Global test fixtures shared across unit and integration test suites.

This module is the single place where the test FastAPI application is assembled.
All other test modules (unit, integration) import their fixtures
from here or from their own conftest.py, which in turn depend on these.

Design goals:
    1. Zero changes to app/ — all test infrastructure lives in tests/.
    2. Deterministic — hardcoded UUIDs, tokens, and settings for reproducibility.
    3. Isolated — no Redis, no PostgreSQL, no Vault, no network calls.

What this file provides:
    - test_settings           → ApplicationSettings with test-safe values
    - test_auth_token         → A valid JWT for the test tenant/user
    - test_auth_headers       → Full header set (Authorization, X-Tenant-ID, X-Deployment-Key)
    - test_app                → FastAPI app with all routers, zero external dependencies
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from jose import jwt

from app.api.llm_inference_router import router as llm_inference_router
from app.api.management_routers.provider_catalog_router import (
    router as provider_catalog_router,
)
from app.api.management_routers.user_router import (
    router as user_management_router,
)
from app.core.settings.settings import ApplicationSettings, get_application_settings

# ═══════════════════════════════════════════════════════════════════════════════
# Deterministic test constants
# ═══════════════════════════════════════════════════════════════════════════════

TEST_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
TEST_USER_ID = UUID("10000000-0000-0000-0000-000000000001")
TEST_DEPLOYMENT_KEY = "test-deployment"

# ═══════════════════════════════════════════════════════════════════════════════
# Test settings — isolated from the real environment
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="session")
def test_settings() -> Any:
    """Return ApplicationSettings with test-safe, deterministic values.

    Session-scoped: created once per test run. All values are hardcoded
    so CI and local runs produce identical results.

    Environment isolation:
        We temporarily clear env vars that might leak from the developer's
        shell, then set only the ones needed for a valid test configuration.
    """
    saved = dict(os.environ)
    try:
        # Purge everything that might leak real secrets or URLs.
        for key in list(os.environ):
            if any(
                prefix in key.upper()
                for prefix in (
                    "DATABASE_",
                    "REDIS_",
                    "JWT_",
                    "ENCRYPTION_",
                    "VAULT_",
                    "SECRET_",
                    "APP_ENVIRONMENT",
                )
            ):
                del os.environ[key]

        # Set only the values needed for testing.
        os.environ.update(
            {
                "APP_ENVIRONMENT": "test",
                "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test_db",
                "REDIS_URL": "redis://localhost:6379/0",
                "ENCRYPTION_MASTER_KEY": "dGVzdC1rZXktMzItYnl0ZXMtbG9uZyEhISEhISEhISEh",
                "JWT_SECRET_KEY": "test-secret-key-for-jwt-signing-only",
                "JWT_ALGORITHM": "HS256",
                "JWT_ISSUER": "test-identity-service",
                "JWT_AUDIENCE": "llm-provider-service-tests",
                "SECRET_BACKEND": "environment",
                "VAULT_USERNAME": "test-vault-user",
                "VAULT_PASSWORD": "test-vault-password",
                "CONFIG_DIR": "config",
            }
        )

        # Invalidate the lru_cache so get_application_settings() reads our values.
        get_application_settings.cache_clear()
        yield get_application_settings()
    finally:
        os.environ.clear()
        os.environ.update(saved)
        get_application_settings.cache_clear()


# ═══════════════════════════════════════════════════════════════════════════════
# JWT token — valid for the test tenant
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="session")
def test_auth_token(test_settings: ApplicationSettings) -> str:
    """Sign a valid upstream identity token for the test resource server.

    This token passes the cryptographic validation in get_current_user()
    and validate_access_token() — no mocking required for the auth layer.
    """
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(TEST_USER_ID),
            "role": "owner",
            "type": "access",
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(hours=1),
            "iss": test_settings.jwt_issuer,
            "aud": test_settings.jwt_audience,
            "jti": str(uuid4()),
        },
        test_settings.jwt_secret_key.get_secret_value(),
        algorithm=test_settings.jwt_algorithm,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Auth headers — injected into every test request
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="session")
def test_auth_headers(test_auth_token: str) -> dict[str, str]:
    """Complete set of headers needed to pass the auth dependency chain.

    These satisfy:
        - HTTPBearer (Authorization header)
        - require_inference_access (X-Tenant-ID, X-Deployment-Key)
    """
    return {
        "Authorization": f"Bearer {test_auth_token}",
        "X-Tenant-ID": str(TEST_TENANT_ID),
        "X-Deployment-Key": TEST_DEPLOYMENT_KEY,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Mock InferenceService — returns predictable responses
# ═══════════════════════════════════════════════════════════════════════════════


def _build_mock_inference_service() -> MagicMock:
    """Create a mock InferenceService that returns valid responses.

    Every execute_* method returns a well-formed response dict so the
    Pydantic response_model validation passes. The raw values don't
    matter — what matters is that the response schema is conformant
    and no 500 errors leak through.
    """
    from app.schemas.responses_schema import (
        ChatResponse,
        ChatStreamChunk,
        EmbedResponse,
        RerankResponse,
        RerankResult,
        Usage,
    )

    mock = MagicMock()

    # --- Chat (non-streaming) ---
    mock.execute_chat = AsyncMock(
        return_value=ChatResponse(
            content="Hello! I am an AI assistant.",
            role="assistant",
            finish_reason="stop",
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            model="gpt-4o",
        )
    )

    # --- Chat (streaming) — async generator yielding chunks ---
    async def _mock_stream_chunks() -> Any:
        yield ChatStreamChunk(content="Hello", finish_reason=None, index=0)
        yield ChatStreamChunk(content="!", finish_reason="stop", index=0)

    async def _mock_prepare_stream(*args: Any, **kwargs: Any) -> Any:
        return _mock_stream_chunks()

    mock.prepare_stream_chat = AsyncMock(side_effect=_mock_prepare_stream)

    # --- Embed ---
    mock.execute_embed = AsyncMock(
        return_value=EmbedResponse(
            embeddings=[[0.1, 0.2, 0.3]],
            model="text-embedding-3-small",
            usage=Usage(prompt_tokens=5, completion_tokens=0, total_tokens=5),
        )
    )

    # --- Rerank ---
    mock.execute_rerank = AsyncMock(
        return_value=RerankResponse(
            results=[
                RerankResult(
                    index=0,
                    document="Paris is the capital of France.",
                    relevance_score=0.98,
                ),
                RerankResult(
                    index=3,
                    document="France is a country in Western Europe.",
                    relevance_score=0.76,
                ),
            ],
            model="rerank-english-v3.0",
        )
    )

    return mock


# ═══════════════════════════════════════════════════════════════════════════════
# Test FastAPI app — all routers, no external dependencies
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="session")
def test_app(
    test_settings: ApplicationSettings,
    test_auth_token: str,
) -> Any:
    """Build a fully-routed FastAPI app with all external dependencies mocked.

    This app has:
        - All inference routes (POST /api/v1/llm/chat, /embed, /rerank)
        - All management routes (tenants, users, providers, deployments, etc.)
        - A working auth layer (real JWT validation, no mocked crypto)
        - Mocked business services (return predictable results, never 500)

    Nothing in app/ is modified. All mocking is done via FastAPI's
    dependency_overrides mechanism, which is designed exactly for this.

    Session-scoped: built once and reused across all test cases.
    """
    from app.api.inference_dependencies import get_inference_authorization_service
    from app.api.llm_inference_router import _get_inference_service
    from app.api.management_dependencies import (
        get_model_catalog_service,
        get_provider_catalog_service,
        get_tenant_deployment_service,
        get_tenant_membership_service,
        get_tenant_service,
        get_user_entitlement_service,
        get_user_service,
    )
    from app.api.shared_dependencies import (
        get_inference_authorization_cache,
        get_tenant_access_service,
    )
    from app.auth.authorization.authorization_grant_cache import AuthorizationGrantCache
    from app.schemas.auth_schema import AuthorizationGrantLookup

    app = FastAPI(
        title="LLM Provider Service (Test)",
        version="0.1.0",
        docs_url=None,  # Hide Swagger UI in tests
        redoc_url=None,  # Hide ReDoc in tests
        openapi_url="/openapi.json",
    )

    # ── Register production routers that are currently import-safe ──────────
    app.include_router(llm_inference_router)
    app.include_router(provider_catalog_router)
    app.include_router(user_management_router)

    # ── Mock InferenceService (stored on app.state, as production does) ────
    mock_inference = _build_mock_inference_service()
    app.state.inference_service = mock_inference

    # ── Override _get_inference_service to avoid the real lookup ────────────
    # The real dependency reads from request.app.state; ours returns the mock
    # directly. The override key is the exact function object imported by the
    # production router.
    def _mock_get_inference_service() -> MagicMock:
        return mock_inference

    app.dependency_overrides[_get_inference_service] = _mock_get_inference_service

    # ── Mock the inference authorization cache (no Redis needed) ────────────
    mock_cache = MagicMock(spec=AuthorizationGrantCache)

    async def _mock_find_grant(*args: Any, **kwargs: Any) -> AuthorizationGrantLookup:
        return AuthorizationGrantLookup(context=None, observed_versions=None)

    mock_cache.find_grant = _mock_find_grant
    mock_cache.store_grant_if_unchanged = AsyncMock(return_value=False)

    def _mock_get_cache() -> AuthorizationGrantCache:
        return mock_cache

    app.dependency_overrides[get_inference_authorization_cache] = _mock_get_cache

    # ── Mock the entire management service chain ────────────────────────────
    # Every fake row matches one concrete response model. Keeping these rows
    # distinct ensures router tests fail if a public contract drifts.

    _mock_services: dict[str, MagicMock] = {}

    def _mock_service_factory(name: str) -> MagicMock:
        """Create a mock service that returns schema-conformant responses."""
        svc = MagicMock()

        # --- Concrete rows used by both point reads and list endpoints ---
        timestamp = "2025-01-01T00:00:00Z"
        tenant = {
            "tenant_id": str(uuid4()),
            "tenant_name": "Test tenant",
            "tenant_slug": "test-tenant",
            "status": "active",
            "tier": "professional",
            "rate_limit_requests_per_minute": 1000,
            "rate_limit_tokens_per_minute": 100000,
            "rate_limit_concurrent_requests": 10,
            "allowed_provider_names": ["openai"],
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        provider = {
            "provider_id": str(uuid4()),
            "provider_name": "openai",
            "display_name": "OpenAI",
            "provider_type": "direct_api",
            "auth_mode": "bearer_token",
            "default_api_endpoint_url": "https://api.openai.com/v1",
            "supported_operations": ["chat", "embed"],
            "is_active": True,
            "provider_metadata": {},
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        model = {
            "model_id": str(uuid4()),
            "provider_id": provider["provider_id"],
            "model_name": "gpt-4o-mini",
            "model_version": None,
            "display_name": "GPT-4o mini",
            "supported_operations": ["chat"],
            "context_window_tokens": 128000,
            "max_output_tokens": 4096,
            "default_temperature": 0.7,
            "default_top_p": 1.0,
            "pricing_metadata": {},
            "model_metadata": {},
            "status": "active",
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        user = {
            "user_id": str(uuid4()),
            "username": "test-user",
            "email": "test@example.com",
            "first_name": "Test",
            "last_name": "User",
            "platform_role": "developer",
            "status": "active",
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        membership = {
            "membership_id": str(uuid4()),
            "tenant_id": tenant["tenant_id"],
            "user_id": user["user_id"],
            "tenant_role": "developer",
            "status": "active",
            "created_by_user_id": None,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        deployment = {
            "deployment_id": str(uuid4()),
            "tenant_id": tenant["tenant_id"],
            "provider_id": provider["provider_id"],
            "model_id": model["model_id"],
            "deployment_key": "production-chat",
            "deployment_name": "Production chat",
            "status": "active",
            "api_endpoint_url": "https://api.openai.com/v1",
            "cloud_provider": None,
            "cloud_region": None,
            "provider_deployment_name": None,
            "token_capacity_limit": 100000,
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
        }
        entitlement = {
            "entitlement_id": str(uuid4()),
            "tenant_id": tenant["tenant_id"],
            "user_id": user["user_id"],
            "deployment_key": deployment["deployment_key"],
            "provider_id": provider["provider_id"],
            "model_id": model["model_id"],
            "entitlement_name": "Test entitlement",
            "status": "active",
            "api_endpoint_url": "https://api.openai.com/v1",
            "cloud_provider": None,
            "cloud_region": None,
            "provider_deployment_name": None,
            "extra_config": {},
            "created_by_user_id": None,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        svc.create_tenant = AsyncMock(return_value=tenant)
        svc.update_tenant = AsyncMock(return_value=tenant)
        svc.get_tenant = AsyncMock(return_value=tenant)
        svc.suspend_tenant = AsyncMock(return_value=tenant)
        svc.activate_tenant = AsyncMock(return_value=tenant)
        svc.delete_tenant = AsyncMock(return_value=None)

        svc.create_membership = AsyncMock(return_value=membership)
        svc.update_membership = AsyncMock(return_value=membership)
        svc.get_tenant_membership = AsyncMock(return_value=membership)
        svc.delete_membership = AsyncMock(return_value=None)

        svc.create_deployment = AsyncMock(return_value=deployment)
        svc.update_deployment = AsyncMock(return_value=deployment)
        svc.get_deployment = AsyncMock(return_value=deployment)
        svc.activate_deployment = AsyncMock(return_value=deployment)
        svc.set_maintenance = AsyncMock(return_value=deployment)
        svc.delete_deployment = AsyncMock(return_value=None)

        svc.create_provider = AsyncMock(return_value=provider)
        svc.update_provider = AsyncMock(return_value=provider)
        svc.get_provider = AsyncMock(return_value=provider)
        svc.delete_provider = AsyncMock(return_value=None)

        svc.create_model = AsyncMock(return_value=model)
        svc.update_model = AsyncMock(return_value=model)
        svc.get_model = AsyncMock(return_value=model)
        svc.activate_model = AsyncMock(return_value=model)
        svc.deactivate_model = AsyncMock(return_value=model)

        svc.create_user = AsyncMock(return_value=user)
        svc.update_user = AsyncMock(return_value=user)
        svc.get_user = AsyncMock(return_value=user)
        svc.get_user_by_email = AsyncMock(return_value=user)
        svc.suspend_user = AsyncMock(return_value=user)
        svc.activate_user = AsyncMock(return_value=user)
        svc.delete_user = AsyncMock(return_value=None)

        svc.create_entitlement = AsyncMock(return_value=entitlement)
        svc.update_entitlement = AsyncMock(return_value=entitlement)
        svc.get_entitlement = AsyncMock(return_value=entitlement)
        svc.delete_entitlement = AsyncMock(return_value=None)

        # --- List methods → return a list of dicts ---
        svc.list_tenants = AsyncMock(return_value=[tenant])
        svc.list_tenant_memberships = AsyncMock(return_value=[membership])
        svc.list_user_memberships = AsyncMock(return_value=[membership])
        svc.list_deployments = AsyncMock(return_value=[deployment])
        svc.list_providers = AsyncMock(return_value=[provider])
        svc.list_models = AsyncMock(return_value=[model])
        svc.list_users = AsyncMock(return_value=[user])
        svc.list_user_entitlements = AsyncMock(return_value=[entitlement])

        # --- Count methods → return an integer ---
        svc.count_tenants = AsyncMock(return_value=1)
        svc.count_tenant_members = AsyncMock(return_value=1)
        svc.count_user_tenants = AsyncMock(return_value=1)
        svc.count_deployments = AsyncMock(return_value=1)
        svc.count_providers = AsyncMock(return_value=1)
        svc.count_models = AsyncMock(return_value=1)
        svc.count_users = AsyncMock(return_value=1)
        svc.count_user_entitlements = AsyncMock(return_value=1)

        # --- Authorization ---
        async def _authorize(*args: Any, **kwargs: Any) -> Any:
            from app.schemas.auth_schema import InferenceAccessContext

            return InferenceAccessContext(
                tenant_id=TEST_TENANT_ID,
                user_id=TEST_USER_ID,
                deployment_key=TEST_DEPLOYMENT_KEY,
                deployment_id=UUID("40000000-0000-0000-0000-000000000001"),
                provider_id=UUID("20000000-0000-0000-0000-000000000001"),
                model_id=UUID("30000000-0000-0000-0000-000000000001"),
                tenant_role="owner",
                entitlement_id=UUID("60000000-0000-0000-0000-000000000001"),
            )

        svc.authorize_inference = _authorize

        _mock_services[name] = svc
        return svc

    # Build all mocks
    tenant_svc = _mock_service_factory("tenant")
    membership_svc = _mock_service_factory("membership")
    deployment_svc = _mock_service_factory("deployment")
    provider_svc = _mock_service_factory("provider")
    model_svc = _mock_service_factory("model")
    user_svc = _mock_service_factory("user")
    entitlement_svc = _mock_service_factory("entitlement")
    tenant_access_svc = _mock_service_factory("tenant_access")
    authz_svc = _mock_service_factory("authz")

    # Override every service factory dependency
    app.dependency_overrides[get_tenant_service] = lambda: tenant_svc
    app.dependency_overrides[get_tenant_membership_service] = lambda: membership_svc
    app.dependency_overrides[get_tenant_deployment_service] = lambda: deployment_svc
    app.dependency_overrides[get_provider_catalog_service] = lambda: provider_svc
    app.dependency_overrides[get_model_catalog_service] = lambda: model_svc
    app.dependency_overrides[get_user_service] = lambda: user_svc
    app.dependency_overrides[get_user_entitlement_service] = lambda: entitlement_svc
    app.dependency_overrides[get_tenant_access_service] = lambda: tenant_access_svc
    app.dependency_overrides[get_inference_authorization_service] = lambda: authz_svc

    yield app

    # Cleanup: remove all overrides so the next test run starts fresh.
    app.dependency_overrides.clear()
