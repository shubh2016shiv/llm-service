"""Small builders and fakes shared by inference-routing tests."""

from __future__ import annotations

from uuid import UUID

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
    TenantConfig,
    UserEntitlementConfig,
)
from app.schemas.enums import (
    TenantDeploymentStatus,
    TenantLifecycleStatus,
    TenantSubscriptionTier,
)

TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
USER_ID = UUID("10000000-0000-0000-0000-000000000001")
DEPLOYMENT_ID = UUID("20000000-0000-0000-0000-000000000001")
ENTITLEMENT_ID = UUID("30000000-0000-0000-0000-000000000001")
DEPLOYMENT_KEY = "gpt4-production"
PROVIDER_NAME = "openai"
MODEL_NAME = "gpt-4o"
API_ENDPOINT = "https://api.openai.com/v1"
SECRET_REF = "secret/acme/openai-key"


def build_tenant_config(
    *,
    status: TenantLifecycleStatus = TenantLifecycleStatus.ACTIVE,
    allowed_provider_names: frozenset[str] | None = None,
) -> TenantConfig:
    """Build a valid tenant routing configuration."""
    return TenantConfig(
        tenant_id=TENANT_ID,
        tenant_name="Acme Corp",
        tenant_slug="acme-corp",
        status=status,
        tier=TenantSubscriptionTier.ENTERPRISE,
        allowed_provider_names=allowed_provider_names,
    )


def build_deployment_config(
    *,
    status: TenantDeploymentStatus = TenantDeploymentStatus.ACTIVE,
    provider_name: str = PROVIDER_NAME,
    model_name: str = MODEL_NAME,
    timeout_seconds: float | None = None,
    default_temperature: float = 0.7,
    default_max_tokens: int | None = None,
) -> DeploymentConfig:
    """Build a valid tenant deployment configuration."""
    return DeploymentConfig(
        deployment_id=DEPLOYMENT_ID,
        tenant_id=TENANT_ID,
        deployment_key=DEPLOYMENT_KEY,
        deployment_name="GPT-4o Production",
        status=status,
        provider_name=provider_name,
        model_name=model_name,
        api_endpoint_url=API_ENDPOINT,
        secret_reference=SECRET_REF,
        timeout_seconds=timeout_seconds,
        default_temperature=default_temperature,
        default_max_tokens=default_max_tokens,
        extra_headers={"X-Route": "production"},
        extra_config={"api_version": "test"},
    )


def build_user_entitlement_config(
    *,
    is_active: bool = True,
    provider_name: str = PROVIDER_NAME,
    model_name: str = MODEL_NAME,
    api_endpoint_url: str = API_ENDPOINT,
) -> UserEntitlementConfig:
    """Build a valid user entitlement configuration."""
    return UserEntitlementConfig(
        entitlement_id=ENTITLEMENT_ID,
        user_id=USER_ID,
        tenant_id=TENANT_ID,
        entitlement_name="Personal OpenAI Key",
        provider_name=provider_name,
        model_name=model_name,
        api_endpoint_url=api_endpoint_url,
        secret_reference="secret/user/openai-key",
        extra_config={"owner": "user"},
        is_active=is_active,
    )


def build_model_spec(
    *,
    name: str = MODEL_NAME,
    capabilities: frozenset[ModelCapability] | None = None,
) -> LLMModelSpec:
    """Build a model specification with selectable capabilities."""
    return LLMModelSpec(
        name=name,
        max_output_tokens=4096,
        context_window=128_000,
        capabilities=(
            capabilities
            if capabilities is not None
            else frozenset({ModelCapability.CHAT, ModelCapability.EMBED})
        ),
    )


def build_provider_static_config(
    *,
    model_spec: LLMModelSpec | None = None,
    default_timeout_seconds: float = 60.0,
    default_temperature: float = 0.7,
) -> ProviderStaticConfig:
    """Build a provider catalog entry containing one model."""
    spec = model_spec or build_model_spec()
    return ProviderStaticConfig(
        provider_name=PROVIDER_NAME,
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
        default_temperature=default_temperature,
    )


class FakeConfigLoader:
    """Return provider configurations from an in-memory catalog."""

    def __init__(self, configs: dict[str, ProviderStaticConfig] | None = None) -> None:
        self.configs = configs or {}

    def load_provider_config(self, provider_name: str) -> ProviderStaticConfig:
        """Return the named provider or match the production catalog contract."""
        if provider_name not in self.configs:
            raise KeyError(provider_name)
        return self.configs[provider_name]
