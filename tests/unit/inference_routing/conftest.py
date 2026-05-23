"""
Inference Routing Unit Test Fixtures
======================================

Shared fakes, builders, and fixtures for the inference_routing/ test suite.

Architecture:
-------------
    ┌───────────────────────┐
    │  conftest.py (this)   │  ← named fake classes + pytest fixtures
    └───────────────────────┘
              │
              ▼
    test_tenant_resolver.py
    test_entitlement_resolver.py
    test_deployment_resolver.py
    test_credential_resolver.py
    test_provider_validator.py
    test_context_factory.py
    test_pipeline.py

Design decisions:
    - Named fake classes (not inline lambdas) — behaviour is explicit and testable.
    - All IDs are deterministic constants — reproducible across CI and local runs.
    - No network, Redis, or database calls — all I/O seams are replaced by fakes.
    - RedisCache fake uses an in-memory dict so cache-aside path tests work without
      a live Redis instance.

Author: Shubham Singh
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from app.core.settings.models.model_config import LLMModelSpec, ModelCapability
from app.core.settings.models.provider_config import (
    AuthMode,
    ProviderAuthConfig,
    ProviderEndpointConfig,
    ProviderStaticConfig,
    ProviderType,
)
from app.core.settings.models.tenant_config import (
    DeploymentConfig,
    DeploymentStatus,
    TenantConfig,
    TenantStatus,
    TenantTier,
    UserEntitlementConfig,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Deterministic test constants
# ═══════════════════════════════════════════════════════════════════════════════

TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
USER_ID = UUID("10000000-0000-0000-0000-000000000001")
DEPLOYMENT_ID = UUID("20000000-0000-0000-0000-000000000001")
ENTITLEMENT_ID = UUID("30000000-0000-0000-0000-000000000001")
DEPLOYMENT_KEY = "gpt4-production"
PROVIDER_NAME = "openai"
MODEL_NAME = "gpt-4o"
API_ENDPOINT = "https://api.openai.com/v1"
SECRET_REF = "secret/acme/openai-key"


# ═══════════════════════════════════════════════════════════════════════════════
# Domain model builders — produce valid frozen Pydantic objects
# ═══════════════════════════════════════════════════════════════════════════════


def build_tenant_config(
    *,
    status: TenantStatus = TenantStatus.ACTIVE,
    allowed_provider_names: frozenset[str] | None = None,
) -> TenantConfig:
    """Return a TenantConfig with sensible test defaults."""
    return TenantConfig(
        tenant_id=TENANT_ID,
        tenant_name="Acme Corp",
        tenant_slug="acme-corp",
        status=status,
        tier=TenantTier.ENTERPRISE,
        allowed_provider_names=allowed_provider_names,
    )


def build_deployment_config(
    *,
    status: DeploymentStatus = DeploymentStatus.ACTIVE,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
    default_max_tokens: int | None = None,
) -> DeploymentConfig:
    """Return a DeploymentConfig with sensible test defaults."""
    return DeploymentConfig(
        deployment_id=DEPLOYMENT_ID,
        tenant_id=TENANT_ID,
        deployment_key=DEPLOYMENT_KEY,
        deployment_name="GPT-4o Production",
        status=status,
        provider_name=PROVIDER_NAME,
        model_name=MODEL_NAME,
        api_endpoint_url=API_ENDPOINT,
        secret_reference=SECRET_REF,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        default_max_tokens=default_max_tokens,
    )


def build_user_entitlement_config(
    *,
    is_active: bool = True,
    provider_name: str = PROVIDER_NAME,
    model_name: str = MODEL_NAME,
) -> UserEntitlementConfig:
    """Return a UserEntitlementConfig with sensible test defaults."""
    return UserEntitlementConfig(
        entitlement_id=ENTITLEMENT_ID,
        user_id=USER_ID,
        tenant_id=TENANT_ID,
        entitlement_name="Personal OpenAI Key",
        provider_name=provider_name,
        model_name=model_name,
        api_endpoint_url=API_ENDPOINT,
        secret_reference="secret/user/bob-openai-key",
        is_active=is_active,
    )


def build_model_spec(
    *,
    name: str = MODEL_NAME,
    capabilities: frozenset[ModelCapability] | None = None,
) -> LLMModelSpec:
    """Return an LLMModelSpec for the given model name."""
    if capabilities is None:
        capabilities = frozenset({ModelCapability.CHAT, ModelCapability.EMBED})
    return LLMModelSpec(
        name=name,
        max_output_tokens=4096,
        context_window=128_000,
        capabilities=capabilities,
    )


def build_provider_static_config(
    *,
    provider_name: str = PROVIDER_NAME,
    model_spec: LLMModelSpec | None = None,
    default_timeout_seconds: float = 60.0,
    default_max_retries: int = 3,
    default_temperature: float = 0.7,
) -> ProviderStaticConfig:
    """Return a ProviderStaticConfig with one model entry."""
    spec = model_spec or build_model_spec()
    return ProviderStaticConfig(
        provider_name=provider_name,
        provider_type=ProviderType.REST_API,
        implementation_class="app.providers.direct.openai_provider.OpenAIProvider",
        auth=ProviderAuthConfig(
            mode=AuthMode.BEARER_TOKEN,
            header_name="Authorization",
            header_prefix="Bearer",
        ),
        endpoints=ProviderEndpointConfig(
            base_url=API_ENDPOINT,
            chat="/chat/completions",
            embed="/embeddings",
        ),
        capabilities=spec.capabilities,
        models=(spec,),
        default_timeout_seconds=default_timeout_seconds,
        default_max_retries=default_max_retries,
        default_temperature=default_temperature,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Named fake classes — implement contracts without touching real infrastructure
# ═══════════════════════════════════════════════════════════════════════════════


class FakeTenantConfigReader:
    """Fake TenantConfigReader. Returns pre-loaded tenant or None."""

    def __init__(self, tenant: TenantConfig | None = None) -> None:
        self._tenant = tenant

    async def get_tenant_config(self, tenant_id: object) -> TenantConfig | None:
        return self._tenant


class FakeDeploymentConfigReader:
    """Fake DeploymentConfigReader. Returns pre-loaded deployment or None."""

    def __init__(self, deployment: DeploymentConfig | None = None) -> None:
        self._deployment = deployment

    async def get_deployment_config(
        self, tenant_id: object, deployment_key: str
    ) -> DeploymentConfig | None:
        return self._deployment


class FakeUserEntitlementReader:
    """Fake UserEntitlementReader. Returns a controlled list of candidates."""

    def __init__(self, candidates: list[UserEntitlementConfig] | None = None) -> None:
        self._candidates: list[UserEntitlementConfig] = candidates or []

    async def find_matching_entitlements(
        self,
        tenant_id: object,
        user_id: object,
        deployment_key: str,
        requested_model_name: str | None = None,
        entitlement_id: object | None = None,
    ) -> list[UserEntitlementConfig]:
        return self._candidates


class FakeRedisCache:
    """In-memory Redis stand-in for cache-aside path testing.

    Tracks set() calls so tests can assert repopulation behaviour.
    """

    def __init__(self, stored: dict[str, bytes] | None = None) -> None:
        self._store: dict[str, bytes] = stored or {}
        self.set_calls: list[tuple[str, bytes]] = []

    async def get(self, key: str) -> bytes | None:
        return self._store.get(key)

    async def set(self, key: str, value: bytes, **kwargs: Any) -> None:
        self._store[key] = value
        self.set_calls.append((key, value))


class FakeConfigLoader:
    """Fake ConfigLoader. Returns a pre-configured ProviderStaticConfig or raises FileNotFoundError."""

    def __init__(
        self,
        configs: dict[str, ProviderStaticConfig] | None = None,
    ) -> None:
        self._configs: dict[str, ProviderStaticConfig] = configs or {}

    def load_provider_config(self, provider_name: str) -> ProviderStaticConfig:
        if provider_name not in self._configs:
            raise FileNotFoundError(f"No config for provider {provider_name!r}")
        return self._configs[provider_name]


# ═══════════════════════════════════════════════════════════════════════════════
# Pytest fixtures — composed from builders and fakes above
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def active_tenant() -> TenantConfig:
    """Active tenant with all providers allowed."""
    return build_tenant_config(status=TenantStatus.ACTIVE)


@pytest.fixture()
def suspended_tenant() -> TenantConfig:
    """Suspended tenant — should fail policy checks immediately."""
    return build_tenant_config(status=TenantStatus.SUSPENDED)


@pytest.fixture()
def restricted_tenant() -> TenantConfig:
    """Active tenant allowed only to use 'openai'."""
    return build_tenant_config(
        status=TenantStatus.ACTIVE,
        allowed_provider_names=frozenset({"openai"}),
    )


@pytest.fixture()
def active_deployment() -> DeploymentConfig:
    """Active deployment with provider-level defaults (no overrides)."""
    return build_deployment_config()


@pytest.fixture()
def inactive_deployment() -> DeploymentConfig:
    """Inactive deployment — should be rejected by DeploymentResolver."""
    return build_deployment_config(status=DeploymentStatus.INACTIVE)


@pytest.fixture()
def active_entitlement() -> UserEntitlementConfig:
    """Single active user entitlement for openai/gpt-4o."""
    return build_user_entitlement_config(is_active=True)


@pytest.fixture()
def inactive_entitlement() -> UserEntitlementConfig:
    """Inactive entitlement — must be filtered out by the resolver."""
    return build_user_entitlement_config(is_active=False)


@pytest.fixture()
def provider_static_config() -> ProviderStaticConfig:
    """Provider static config for openai with one chat+embed model."""
    return build_provider_static_config()
