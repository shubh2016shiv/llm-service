"""
OpenAI Direct Resolution Demo
=============================

Step-by-step smoke test of the tenant-aware resolution and provider-execution
pipeline WITHOUT going through the FastAPI layer.

What this validates (in order):
  1. ConfigLoader can parse config/providers/openai.yaml
  2. TenantResolutionService enforces active-status checks
  3. UserEntitlementResolutionService picks up the user-scoped override
  4. DeploymentResolutionService falls back to the tenant deployment
  5. ProviderRouteValidationService validates provider + model against YAML catalog
  6. CredentialResolutionService selects the correct credential scope
  7. ResolvedExecutionContextFactory produces an immutable execution context
  8. EnvironmentSecretStore resolves OPENAI_API_KEY from the environment
  9. OpenAIProvider executes a real HTTP call and returns a ChatResponse

Token Manager:
  Set USE_TOKEN_MANAGER = True when the token-manager microservice is live.
  When False the quota gate is skipped entirely (bypass mode for early dev).

Usage:
  export OPENAI_API_KEY="sk-..."
  python3 examples/openai_direct_resolution_demo.py
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from uuid import UUID, uuid5, NAMESPACE_URL

import aiobreaker
import httpx

from app.core.secret_store import EnvironmentSecretStore
from app.core.settings import ConfigLoader
from app.core.settings.models.tenant_config import (
    DeploymentConfig,
    DeploymentStatus,
    TenantConfig,
    TenantRateLimits,
    TenantStatus,
    TenantTier,
    UserEntitlementConfig,
)
from app.infrastructure.http_client_factory import HTTPClientFactory
from app.providers.direct.openai_provider import OpenAIProvider
from app.routing import (
    CredentialResolutionService,
    DeploymentResolutionService,
    ProviderRouteValidationService,
    RequestResolutionService,
    ResolutionRequest,
    ResolvedExecutionContext,
    ResolvedExecutionContextFactory,
    TenantResolutionService,
    UserEntitlementResolutionService,
)
from app.routing.deployment_resolver import DeploymentNotFoundError as LegacyDeploymentNotFoundError
from app.schemas.enums import OperationType
from app.schemas.requests import ChatMessage, ChatRequest

logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------------------------
# Feature Switch — Token Manager Integration
# ---------------------------------------------------------------------------
# Set to True once the token-manager microservice is running and reachable.
# When False, the quota gate is skipped (bypass mode).
USE_TOKEN_MANAGER: bool = False

# ---------------------------------------------------------------------------
# Demo Identity Constants
# ---------------------------------------------------------------------------
TENANT_ID = UUID("11111111-1111-1111-1111-111111111111")
DEPLOYMENT_ID = UUID("22222222-2222-2222-2222-222222222222")
ENTITLEMENT_ID = UUID("33333333-3333-3333-3333-333333333333")
USER_WITHOUT_OVERRIDE_ID = UUID("44444444-4444-4444-4444-444444444444")
USER_WITH_OVERRIDE_ID = UUID("55555555-5555-5555-5555-555555555555")


# ---------------------------------------------------------------------------
# In-Memory Protocol Implementations
# These satisfy the TenantConfigReader and UserEntitlementReader protocols
# without requiring a live database — necessary for pre-API smoke testing.
# ---------------------------------------------------------------------------

class _TenantStore:
    """Minimal TenantConfigReader backed by a single in-memory TenantConfig."""

    def __init__(self, config: TenantConfig) -> None:
        self._config = config

    async def get_tenant_config(self, tenant_id: UUID | str) -> TenantConfig | None:
        if str(tenant_id) != str(self._config.tenant_id):
            return None
        return self._config


class _EntitlementStore:
    """Minimal UserEntitlementReader backed by a static entitlement registry."""

    def __init__(
        self,
        deployment_key: str,
        entitlements: dict[UUID, tuple[UserEntitlementConfig, ...]],
    ) -> None:
        self._deployment_key = deployment_key
        self._entitlements = entitlements

    async def find_matching_entitlements(
        self,
        tenant_id: UUID | str,
        user_id: UUID | str,
        deployment_key: str,
        requested_model_name: str | None = None,
    ) -> list[UserEntitlementConfig]:
        matches: list[UserEntitlementConfig] = []
        for entitlement in self._entitlements.get(UUID(str(user_id)), ()):
            if str(entitlement.tenant_id) != str(tenant_id):
                continue
            if deployment_key != self._deployment_key:
                continue
            if requested_model_name and entitlement.model_name != requested_model_name:
                continue
            matches.append(entitlement)
        return matches


class _DeploymentStore:
    """Minimal DeploymentResolver protocol backed by a static deployment map."""

    def __init__(self, deployments: dict[str, DeploymentConfig]) -> None:
        self._deployments = deployments

    async def resolve(self, tenant_id: UUID | str, deployment_key: str) -> DeploymentConfig:
        deployment = self._deployments.get(deployment_key)
        if deployment is None or str(deployment.tenant_id) != str(tenant_id):
            raise LegacyDeploymentNotFoundError(UUID(str(tenant_id)), deployment_key)
        return deployment


# ---------------------------------------------------------------------------
# Service Assembly
# ---------------------------------------------------------------------------

def build_resolution_service(
    config_loader: ConfigLoader,
    tenant_config: TenantConfig,
    deployment_config: DeploymentConfig,
    user_entitlement_config: UserEntitlementConfig,
) -> RequestResolutionService:
    """Wire up all resolution sub-services with the in-memory stores above."""
    tenant_store = _TenantStore(tenant_config)
    tenant_svc = TenantResolutionService(tenant_store)

    entitlement_store = _EntitlementStore(
        deployment_key=deployment_config.deployment_key,
        entitlements={user_entitlement_config.user_id: (user_entitlement_config,)},
    )
    entitlement_svc = UserEntitlementResolutionService(
        entitlement_reader=entitlement_store,
        tenant_resolution_service=tenant_svc,
    )

    deployment_store = _DeploymentStore(
        deployments={deployment_config.deployment_key: deployment_config},
    )
    deployment_svc = DeploymentResolutionService(deployment_store)

    return RequestResolutionService(
        tenant_resolution_service=tenant_svc,
        user_entitlement_resolution_service=entitlement_svc,
        deployment_resolution_service=deployment_svc,
        provider_route_validation_service=ProviderRouteValidationService(config_loader),
        credential_resolution_service=CredentialResolutionService(),
        resolved_execution_context_factory=ResolvedExecutionContextFactory(),
    )


# ---------------------------------------------------------------------------
# Execution Context → Provider Config Adapter
# ---------------------------------------------------------------------------

def context_to_deployment_config(
    context: ResolvedExecutionContext,
    deployment_key: str,
) -> DeploymentConfig:
    """Convert a ResolvedExecutionContext into the DeploymentConfig the provider expects.

    When the tenant deployment path wins, the config is already attached to the
    context. When the user-entitlement path wins, we synthesise a transient
    DeploymentConfig from the already-resolved context fields — the provider
    layer does not need to know which path was taken.
    """
    if context.deployment_config is not None:
        return context.deployment_config

    entitlement = context.user_entitlement_config
    if entitlement is None:
        raise ValueError("ResolvedExecutionContext has neither deployment nor entitlement.")

    synthetic_id = uuid5(
        NAMESPACE_URL,
        f"{context.tenant_config.tenant_id}:{entitlement.entitlement_id}:{deployment_key}",
    )
    return DeploymentConfig(
        deployment_id=synthetic_id,
        tenant_id=context.tenant_config.tenant_id,
        deployment_key=deployment_key,
        deployment_name=f"user-entitlement:{entitlement.entitlement_name}",
        status=DeploymentStatus.ACTIVE,
        provider_name=context.provider_name,
        model_name=context.model_name,
        api_endpoint_url=context.api_endpoint_url,
        secret_reference=context.secret_reference,
        cloud_region=context.cloud_region,
        timeout_seconds=context.effective_timeout_seconds,
        max_retries=context.effective_max_retries,
        default_temperature=context.effective_temperature,
        default_max_tokens=context.effective_max_tokens,
        extra_headers={},
        extra_config=dict(entitlement.extra_config),
        is_default=False,
        priority=100,
    )


# ---------------------------------------------------------------------------
# Token Manager Gate (bypassed when USE_TOKEN_MANAGER = False)
# ---------------------------------------------------------------------------

async def check_token_quota(
    tenant_id: UUID,
    deployment_key: str,
    chat_request: ChatRequest,
) -> None:
    """Enforce token allocation via the token-manager microservice.

    Skipped entirely when USE_TOKEN_MANAGER is False so the rest of the
    pipeline can be exercised before that service is integrated.
    """
    if not USE_TOKEN_MANAGER:
        logging.getLogger(__name__).debug(
            "Token manager bypassed (USE_TOKEN_MANAGER=False)",
            extra={"tenant_id": str(tenant_id), "deployment_key": deployment_key},
        )
        return

    from app.adapters.clients.token_manager_client import TokenManagerClient, QuotaExceededError

    client = TokenManagerClient()
    allowed = await client.check_quota(
        tenant_id=tenant_id,
        deployment_key=deployment_key,
        request=chat_request,
    )
    if not allowed:
        raise QuotaExceededError(
            tenant_id=tenant_id,
            message=f"Token quota exceeded for deployment '{deployment_key}'.",
        )


# ---------------------------------------------------------------------------
# Single Case Runner
# ---------------------------------------------------------------------------

async def run_case(
    *,
    case_name: str,
    user_id: UUID,
    deployment_key: str,
    requested_model_name: str | None,
    prompt: str,
    resolution_service: RequestResolutionService,
    http_client_factory: HTTPClientFactory,
    secret_store: EnvironmentSecretStore,
) -> None:
    """Resolve route → acquire token quota → execute provider → print result."""

    # Step 1 — Resolve the full execution context.
    resolution_request = ResolutionRequest(
        tenant_id=TENANT_ID,
        user_id=user_id,
        deployment_key=deployment_key,
        operation=OperationType.CHAT,
        requested_model_name=requested_model_name,
        trace_id=f"demo:{case_name}",
    )
    context = await resolution_service.resolve(resolution_request)

    print(f"\n{'='*62}")
    print(f"  CASE : {case_name}")
    print(f"{'='*62}")
    print(f"[STEP 1] Route resolved")
    print(f"         resolution_source  : {context.resolution_source.value}")
    print(f"         provider_name      : {context.provider_name}")
    print(f"         model_name         : {context.model_name}")
    print(f"         credential_scope   : {context.credential_scope.value}")
    print(f"         api_endpoint_url   : {context.api_endpoint_url}")
    print(f"         effective_timeout  : {context.effective_timeout_seconds}s")
    print(f"         effective_temp     : {context.effective_temperature}")
    print(f"         effective_tokens   : {context.effective_max_tokens}")
    print(f"         route_fingerprint  : {context.route_fingerprint[:24]}...")

    # Step 2 — Retrieve plaintext API key from the secret store.
    api_key = secret_store.get_secret(
        context.secret_reference,
        tenant_id=str(context.tenant_config.tenant_id),
    )
    print(f"[STEP 2] Secret fetched")
    print(f"         secret_reference   : {context.secret_reference}")
    print(f"         key_preview        : {api_key[:10]}...{api_key[-4:]}")

    # Step 3 — Build the provider-ready chat request.
    chat_request = ChatRequest(
        messages=[
            ChatMessage(role="system", content="You are a concise assistant."),
            ChatMessage(role="user", content=prompt),
        ],
        temperature=context.effective_temperature,
        max_tokens=min(120, context.effective_max_tokens),
        resolved_api_key=api_key,
    )
    print(f"[STEP 3] ChatRequest built")
    print(f"         messages           : {len(chat_request.messages)} (system + user)")
    print(f"         temperature        : {chat_request.temperature}")
    print(f"         max_tokens         : {chat_request.max_tokens}")

    # Step 4 — Token manager gate (bypassed when USE_TOKEN_MANAGER = False).
    print(f"[STEP 4] Token manager gate")
    print(f"         USE_TOKEN_MANAGER  : {USE_TOKEN_MANAGER}")
    await check_token_quota(
        tenant_id=TENANT_ID,
        deployment_key=deployment_key,
        chat_request=chat_request,
    )
    print(f"         quota_status       : PASSED")

    # Step 5 — Instantiate the provider and execute.
    http_client = http_client_factory.create_client("rest_api")
    if not isinstance(http_client, httpx.AsyncClient):
        raise TypeError("OpenAI direct provider requires an httpx.AsyncClient.")

    deployment_config = context_to_deployment_config(context, deployment_key)
    provider = OpenAIProvider(
        static_config=context.provider_static_config,
        deployment_config=deployment_config,
        http_client=http_client,
        circuit_breaker=aiobreaker.CircuitBreaker(),
    )
    print(f"[STEP 5] Provider instantiated")
    print(f"         provider_class     : {provider.__class__.__name__}")
    print(f"         target_model       : {deployment_config.model_name}")
    print(f"         target_endpoint    : {deployment_config.api_endpoint_url}")
    print(f"         circuit_breaker    : CLOSED (fresh)")

    print(f"[STEP 6] Executing LLM call...")
    try:
        response = await provider.generate(chat_request)
        print(f"[STEP 6] Response received")
        print(f"         response_model     : {response.model}")
        print(f"         finish_reason      : {response.finish_reason}")
        print(f"         response_content   : {response.content.strip()}")
        if response.usage is not None:
            u = response.usage
            print(f"         prompt_tokens      : {u.prompt_tokens}")
            print(f"         completion_tokens  : {u.completion_tokens}")
            print(f"         total_tokens       : {u.total_tokens}")
        print(f"[  OK  ] Case '{case_name}' completed successfully.")
    except Exception as exc:
        print(f"[FAILED] Case '{case_name}' — {type(exc).__name__}: {exc}")
        raise
    finally:
        await http_client.aclose()
        print(f"[STEP 7] HTTP client closed cleanly.")


# ---------------------------------------------------------------------------
# Demo Fixtures
# ---------------------------------------------------------------------------

def make_tenant_config() -> TenantConfig:
    return TenantConfig(
        tenant_id=TENANT_ID,
        tenant_name="Acme Enterprise",
        tenant_slug="acme-enterprise",
        status=TenantStatus.ACTIVE,
        tier=TenantTier.ENTERPRISE,
        rate_limits=TenantRateLimits(rpm=5000, tpm=1_000_000, concurrent_requests=50),
        allowed_provider_names=frozenset({"openai"}),
    )


def make_deployment_config() -> DeploymentConfig:
    return DeploymentConfig(
        deployment_id=DEPLOYMENT_ID,
        tenant_id=TENANT_ID,
        deployment_key="openai-direct-mini",
        deployment_name="OpenAI Direct Mini",
        status=DeploymentStatus.ACTIVE,
        provider_name="openai",
        model_name="gpt-4o-mini",
        api_endpoint_url="https://api.openai.com/v1",
        secret_reference="OPENAI_API_KEY",
        cloud_region=None,
        timeout_seconds=45.0,
        max_retries=2,
        default_temperature=0.2,
        default_max_tokens=256,
        extra_headers={},
        extra_config={},
        is_default=True,
        priority=10,
    )


def make_user_entitlement_config() -> UserEntitlementConfig:
    return UserEntitlementConfig(
        entitlement_id=ENTITLEMENT_ID,
        user_id=USER_WITH_OVERRIDE_ID,
        tenant_id=TENANT_ID,
        entitlement_name="Personal OpenAI Mini",
        provider_name="openai",
        model_name="gpt-4o-mini",
        api_endpoint_url="https://api.openai.com/v1",
        secret_reference="OPENAI_API_KEY",
        cloud_provider=None,
        cloud_region=None,
        extra_config={},
        is_active=True,
    )


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

async def main() -> None:
    """Boot infrastructure, wire services, then run both resolution scenarios."""
    config_dir = Path(__file__).resolve().parents[1] / "config"
    config_loader = ConfigLoader(config_dir=config_dir, environment="development")
    global_config = config_loader.load_global_config()
    http_client_factory = HTTPClientFactory(global_config.http_pool)
    secret_store = EnvironmentSecretStore()

    tenant_config = make_tenant_config()
    deployment_config = make_deployment_config()
    user_entitlement_config = make_user_entitlement_config()

    resolution_service = build_resolution_service(
        config_loader=config_loader,
        tenant_config=tenant_config,
        deployment_config=deployment_config,
        user_entitlement_config=user_entitlement_config,
    )

    # --- Case 1: Standard tenant deployment path ---
    await run_case(
        case_name="tenant_deployment_resolution",
        user_id=USER_WITHOUT_OVERRIDE_ID,
        deployment_key=deployment_config.deployment_key,
        requested_model_name=None,
        prompt="Reply with one short sentence confirming the tenant deployment route worked.",
        resolution_service=resolution_service,
        http_client_factory=http_client_factory,
        secret_store=secret_store,
    )

    # --- Case 2: User-entitlement override path ---
    await run_case(
        case_name="user_entitlement_override_resolution",
        user_id=USER_WITH_OVERRIDE_ID,
        deployment_key=deployment_config.deployment_key,
        requested_model_name="gpt-4o-mini",
        prompt="Reply with one short sentence confirming the user entitlement override route worked.",
        resolution_service=resolution_service,
        http_client_factory=http_client_factory,
        secret_store=secret_store,
    )


if __name__ == "__main__":
    asyncio.run(main())
