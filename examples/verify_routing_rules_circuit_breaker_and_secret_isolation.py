"""
Resolution Pipeline Thorough Execution Tests
============================================

Direct execution of the full resolution and circuit breaker pipeline without
FastAPI or live API endpoints.  Every test imports and instantiates the REAL
service classes (TenantResolutionService, RequestResolutionService, etc.) and
drives them through error branches exactly as production requests would.

This is NOT a unit-test file with mocks.  The only "fake" layer is:
  - In-memory stores (_TenantStore, _EntitlementStore, _DeploymentStore) —
    the same pattern already used by the demo.
  - A configurable httpx transport (_FakeAsyncTransport) for circuit breaker
    tests that need HTTP-level failure injection without a live provider.

Stage Coverage
--------------
  Stage 1 — Resolution Pipeline Edge Cases
    1.1   Tenant not found (wrong tenant_id)
    1.2   Tenant suspended (SUSPENDED status)
    1.3   Provider not allowed by tenant policy
    1.4   Deployment not found (unknown deployment_key)
    1.5   Deployment inactive (INACTIVE status)
    1.6   User entitlement ambiguity (two active entitlements for same user)
    1.7   Inactive entitlement skipped → falls back to deployment path
    1.8   Model not supported by provider (unknown model name)
    1.9   Operation not supported by model (EMBED on chat-only gpt-4o-mini)
    1.10  Empty entitlement list → deployment path succeeds

  Stage 2 — Circuit Breaker Scenarios
    2.1   Fresh CLOSED breaker allows call through
    2.2   OPEN breaker rejects call immediately (CircuitBreakerError)
    2.3   HALF_OPEN breaker allows single trial call → transitions CLOSED
    2.4   Failure counting drives CLOSED → OPEN transition
    2.5   Stream failure is counted by the breaker

  Stage 3 — Secret Store Scenarios
    3.1   EnvironmentSecretStore: unset env variable raises KeyError
    3.2   EnvironmentSecretStore: correctly set env variable is returned
    3.3   AESGCMSecretStore: unregistered reference raises KeyError
    3.4   AESGCMSecretStore: wrong tenant_id (different HKDF-derived key) raises ValueError
          — validates per-tenant encryption isolation

  Stage 4 — Context Adapter Edge Cases
    4.1   Deployment path: adapter returns the attached DeploymentConfig unchanged
    4.2   Entitlement path: adapter synthesises a transient DeploymentConfig
    4.3   Neither path (both None): adapter raises ValueError

  Stage 5 — Protocol Store Edge Cases
    5.1   _TenantStore: matching tenant_id returns TenantConfig
    5.2   _TenantStore: unknown tenant_id returns None
    5.3   _EntitlementStore: user with no entitlements returns empty list
    5.4   _EntitlementStore: model_name filter narrows multi-entitlement list
    5.5   _EntitlementStore: wrong deployment_key returns empty list
    5.6   _EntitlementStore: wrong tenant_id returns empty list
    5.7   _DeploymentStore: matching key and tenant returns DeploymentConfig
    5.8   _DeploymentStore: unknown deployment_key raises LegacyDeploymentNotFoundError
    5.9   _DeploymentStore: key exists but wrong tenant_id raises LegacyDeploymentNotFoundError

Usage
-----
  python3 examples/verify_routing_rules_circuit_breaker_and_secret_isolation.py
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import aiobreaker
import httpx

# ---------------------------------------------------------------------------
# Path bootstrap — same import environment as the demo
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[1]
_EXAMPLES_DIR = Path(__file__).resolve().parent
# Add repo root first (for `app.*` imports), then examples dir (for the demo module)
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_EXAMPLES_DIR) not in sys.path:
    sys.path.insert(1, str(_EXAMPLES_DIR))

# Import demo infrastructure (real in-memory store implementations + fixtures)
from demo_full_request_lifecycle_with_live_openai import (  # noqa: E402
    TENANT_ID,
    USER_WITH_OVERRIDE_ID,
    USER_WITHOUT_OVERRIDE_ID,
    _DeploymentStore,
    _EntitlementStore,
    _TenantStore,
    context_to_deployment_config,
    make_deployment_config,
    make_tenant_config,
    make_user_entitlement_config,
)

# Real routing / core imports
from app.core.exceptions import (  # noqa: E402
    DeploymentInactiveError,
    DeploymentNotFoundError,
    ModelNotSupportedError,
    TenantNotFoundError,
    TenantSuspendedError,
)
from app.core.secret_store import (  # noqa: E402
    AESGCMSecretStore,
    EnvironmentSecretStore,
    encrypt_api_key,
)
from app.core.settings import ConfigLoader  # noqa: E402
from app.core.settings.models.tenant_config import (  # noqa: E402
    DeploymentConfig,
    DeploymentStatus,
    TenantConfig,
    TenantStatus,
    UserEntitlementConfig,
)
from app.providers.direct.openai_provider import OpenAIProvider  # noqa: E402
from app.routing import (  # noqa: E402
    CredentialResolutionService,
    DeploymentResolutionService,
    ProviderRouteValidationService,
    RequestResolutionService,
    ResolutionRequest,
    ResolutionSource,
    ResolvedExecutionContextFactory,
    TenantResolutionService,
    UserEntitlementResolutionService,
)
from app.routing.deployment_resolver import (  # noqa: E402
    DeploymentNotFoundError as LegacyDeploymentNotFoundError,
)
from app.routing.exceptions import (  # noqa: E402
    AmbiguousUserEntitlementError,
    OperationNotSupportedError,
    ProviderNotAllowedError,
)
from app.schemas.enums import OperationType  # noqa: E402
from app.schemas.requests import ChatMessage, ChatRequest  # noqa: E402

logging.basicConfig(level=logging.WARNING)  # suppress INFO noise

# ===========================================================================
# Test Tracker
# ===========================================================================


@dataclass
class _Result:
    label: str
    passed: bool
    detail: str = ""


@dataclass
class TestTracker:
    results: list[_Result] = field(default_factory=list)

    def pass_(self, label: str, detail: str = "") -> None:
        self.results.append(_Result(label=label, passed=True, detail=detail))
        suffix = f" — {detail}" if detail else ""
        print(f"    [ PASS ] {label}{suffix}")

    def fail(self, label: str, reason: str) -> None:
        self.results.append(_Result(label=label, passed=False, detail=reason))
        print(f"    [FAILED] {label} — {reason}")

    def summary(self) -> None:
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        failed = total - passed
        bar = "=" * 66
        print(f"\n{bar}")
        print(f"  FINAL SUMMARY:  {passed}/{total} passed,  {failed} failed")
        print(bar)
        if failed:
            print("  FAILED TESTS:")
            for r in self.results:
                if not r.passed:
                    print(f"    ✗  {r.label}: {r.detail}")
        else:
            print("  All tests passed.")
        print()


# ===========================================================================
# Shared Infrastructure Helpers
# ===========================================================================


def _config_loader() -> ConfigLoader:
    config_dir = _REPO_ROOT / "config"
    return ConfigLoader(config_dir=config_dir, environment="development")


def _make_resolution_service(
    *,
    tenant_config: TenantConfig | None = None,
    deployment_config: DeploymentConfig | None = None,
    entitlements_for_user: dict[UUID, tuple[UserEntitlementConfig, ...]] | None = None,
    config_loader: ConfigLoader | None = None,
) -> RequestResolutionService:
    """Assemble a real RequestResolutionService from overrideable in-memory fixtures."""
    tc = tenant_config or make_tenant_config()
    dc = deployment_config or make_deployment_config()
    cl = config_loader or _config_loader()

    default_uc = make_user_entitlement_config()
    entitlements: dict[UUID, tuple[UserEntitlementConfig, ...]] = {
        default_uc.user_id: (default_uc,),
    }
    if entitlements_for_user:
        entitlements.update(entitlements_for_user)

    tenant_store = _TenantStore(tc)
    tenant_svc = TenantResolutionService(tenant_store)

    entitlement_store = _EntitlementStore(
        deployment_key=dc.deployment_key,
        entitlements=entitlements,
    )
    entitlement_svc = UserEntitlementResolutionService(
        entitlement_reader=entitlement_store,
        tenant_resolution_service=tenant_svc,
    )

    deployment_store = _DeploymentStore(deployments={dc.deployment_key: dc})
    deployment_svc = DeploymentResolutionService(deployment_store)  # type: ignore[arg-type]

    return RequestResolutionService(
        tenant_resolution_service=tenant_svc,
        user_entitlement_resolution_service=entitlement_svc,
        deployment_resolution_service=deployment_svc,
        provider_route_validation_service=ProviderRouteValidationService(cl),
        credential_resolution_service=CredentialResolutionService(),
        resolved_execution_context_factory=ResolvedExecutionContextFactory(),
    )


def _make_resolution_request(
    *,
    tenant_id: UUID = TENANT_ID,
    user_id: UUID = USER_WITHOUT_OVERRIDE_ID,
    deployment_key: str = "openai-direct-mini",
    operation: OperationType = OperationType.CHAT,
    requested_model_name: str | None = None,
    trace_id: str | None = None,
) -> ResolutionRequest:
    return ResolutionRequest(
        tenant_id=tenant_id,
        user_id=user_id,
        deployment_key=deployment_key,
        operation=operation,
        requested_model_name=requested_model_name,
        trace_id=trace_id,
    )


class _FakeAsyncTransport(httpx.AsyncBaseTransport):
    """Async httpx transport returning pre-configured responses in order.

    Each entry in ``responses`` is either:
      - (int, dict)  → HTTP status code + JSON body
      - Exception    → raised directly (simulates network-level failure)

    Once exhausted, every subsequent call returns a 500 "mock exhausted" body.
    """

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self._call_count = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._call_count >= len(self._responses):
            body = {"error": {"message": "mock transport exhausted", "type": "server_error"}}
            return httpx.Response(500, json=body)
        item = self._responses[self._call_count]
        self._call_count += 1
        if isinstance(item, BaseException):
            raise item
        status_code, body = item
        return httpx.Response(status_code, json=body)


def _fake_200_chat_body(model: str = "gpt-4o-mini") -> dict:
    return {
        "id": "chatcmpl-test-breaker",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "circuit breaker test response"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
    }


def _fake_500_body() -> dict:
    return {"error": {"message": "internal server error", "type": "server_error", "code": None}}


def _make_provider(
    responses: list,
    *,
    fail_max: int = 5,
    timeout_duration: timedelta | None = None,
) -> tuple[OpenAIProvider, aiobreaker.CircuitBreaker, httpx.AsyncClient]:
    """Build a real OpenAIProvider wired to a fake transport and a fresh circuit breaker."""
    cl = _config_loader()
    static_config = cl.load_provider_config("openai")
    dc = make_deployment_config()

    breaker = aiobreaker.CircuitBreaker(fail_max=fail_max, timeout_duration=timeout_duration)
    transport = _FakeAsyncTransport(responses)
    http_client = httpx.AsyncClient(transport=transport)

    provider = OpenAIProvider(
        static_config=static_config,
        deployment_config=dc,
        http_client=http_client,
        circuit_breaker=breaker,
    )
    return provider, breaker, http_client


def _make_chat_request(api_key: str = "sk-fake-for-cb-tests") -> ChatRequest:
    return ChatRequest(
        messages=[ChatMessage(role="user", content="circuit breaker ping")],
        temperature=0.0,
        max_tokens=10,
        resolved_api_key=api_key,
    )


# ===========================================================================
# STAGE 1 — Resolution Pipeline Edge Cases
# ===========================================================================


def _print_stage_header(stage_num: int, title: str) -> None:
    bar = "─" * 66
    print(f"\n{bar}")
    print(f"  Stage {stage_num}: {title}")
    print(bar)


async def stage_1_1_tenant_not_found(tracker: TestTracker) -> None:
    """1.1 — TenantResolutionService raises TenantNotFoundError for an unknown UUID."""
    label = "1.1  Tenant not found (wrong tenant_id)"
    wrong_id = uuid4()
    store = _TenantStore(make_tenant_config())  # only knows TENANT_ID
    svc = TenantResolutionService(store)
    try:
        await svc.resolve_tenant(wrong_id)
        tracker.fail(label, "No exception raised — expected TenantNotFoundError")
    except TenantNotFoundError as exc:
        assert str(wrong_id) in str(exc), f"tenant_id missing from error: {exc}"
        assert exc.error_code == "TENANT_NOT_FOUND"
        tracker.pass_(label, f"TenantNotFoundError raised for {wrong_id}")
    except Exception as exc:
        tracker.fail(label, f"Wrong exception type: {type(exc).__name__}: {exc}")


async def stage_1_2_tenant_suspended(tracker: TestTracker) -> None:
    """1.2 — TenantResolutionService raises TenantSuspendedError for a SUSPENDED tenant."""
    label = "1.2  Tenant suspended"
    suspended = make_tenant_config().model_copy(update={"status": TenantStatus.SUSPENDED})
    store = _TenantStore(suspended)
    svc = TenantResolutionService(store)
    try:
        await svc.resolve_tenant(TENANT_ID)
        tracker.fail(label, "No exception raised — expected TenantSuspendedError")
    except TenantSuspendedError as exc:
        assert exc.error_code == "TENANT_SUSPENDED"
        assert "suspended" in str(exc).lower()
        tracker.pass_(label, f"TenantSuspendedError raised; reason={exc.reason!r}")
    except Exception as exc:
        tracker.fail(label, f"Wrong exception type: {type(exc).__name__}: {exc}")


async def stage_1_3_provider_not_allowed(tracker: TestTracker) -> None:
    """1.3 — ProviderNotAllowedError when tenant policy excludes the deployment provider."""
    label = "1.3  Provider not allowed by tenant policy"
    # Tenant only allows 'anthropic', deployment uses 'openai'
    tc = make_tenant_config().model_copy(
        update={"allowed_provider_names": frozenset({"anthropic"})}
    )
    svc = _make_resolution_service(tenant_config=tc)
    # USER_WITHOUT_OVERRIDE_ID has no entitlement → falls through to deployment path
    request = _make_resolution_request(user_id=USER_WITHOUT_OVERRIDE_ID)
    try:
        await svc.resolve(request)
        tracker.fail(label, "No exception raised — expected ProviderNotAllowedError")
    except ProviderNotAllowedError as exc:
        assert exc.provider_name == "openai"
        assert exc.error_code == "PROVIDER_NOT_ALLOWED"
        tracker.pass_(label, f"ProviderNotAllowedError: provider={exc.provider_name!r}")
    except Exception as exc:
        tracker.fail(label, f"Wrong exception type: {type(exc).__name__}: {exc}")


async def stage_1_4_deployment_not_found(tracker: TestTracker) -> None:
    """1.4 — DeploymentNotFoundError when deployment_key has no matching record."""
    label = "1.4  Deployment not found"
    svc = _make_resolution_service()
    # No entitlement for this user + key combo does not exist in the store
    request = _make_resolution_request(
        user_id=USER_WITHOUT_OVERRIDE_ID,
        deployment_key="does-not-exist-anywhere",
    )
    try:
        await svc.resolve(request)
        tracker.fail(label, "No exception raised — expected DeploymentNotFoundError")
    except DeploymentNotFoundError as exc:
        assert exc.error_code == "DEPLOYMENT_NOT_FOUND"
        assert "does-not-exist-anywhere" in str(exc)
        tracker.pass_(label, f"DeploymentNotFoundError: key={exc.deployment_key!r}")
    except Exception as exc:
        tracker.fail(label, f"Wrong exception type: {type(exc).__name__}: {exc}")


async def stage_1_5_deployment_inactive(tracker: TestTracker) -> None:
    """1.5 — DeploymentInactiveError when the deployment has status INACTIVE."""
    label = "1.5  Deployment inactive"
    inactive_dc = make_deployment_config().model_copy(update={"status": DeploymentStatus.INACTIVE})
    svc = _make_resolution_service(deployment_config=inactive_dc)
    request = _make_resolution_request(user_id=USER_WITHOUT_OVERRIDE_ID)
    try:
        await svc.resolve(request)
        tracker.fail(label, "No exception raised — expected DeploymentInactiveError")
    except DeploymentInactiveError as exc:
        assert exc.error_code == "DEPLOYMENT_INACTIVE"
        assert exc.status == "inactive"
        tracker.pass_(label, f"DeploymentInactiveError: status={exc.status!r}")
    except Exception as exc:
        tracker.fail(label, f"Wrong exception type: {type(exc).__name__}: {exc}")


async def stage_1_6_ambiguous_entitlement(tracker: TestTracker) -> None:
    """1.6 — AmbiguousUserEntitlementError when two active entitlements match the request."""
    label = "1.6  User entitlement ambiguity (multiple active entitlements)"
    base_uc = make_user_entitlement_config()
    # Second entitlement for the same user — both active, both match the deployment key
    second_uc = base_uc.model_copy(
        update={
            "entitlement_id": uuid4(),
            "entitlement_name": "Personal OpenAI Mini v2",
        }
    )
    svc = _make_resolution_service(
        entitlements_for_user={USER_WITH_OVERRIDE_ID: (base_uc, second_uc)}
    )
    # Request with model_name=None so BOTH entitlements match (no model filter)
    request = _make_resolution_request(
        user_id=USER_WITH_OVERRIDE_ID,
        requested_model_name=None,
    )
    try:
        await svc.resolve(request)
        tracker.fail(label, "No exception raised — expected AmbiguousUserEntitlementError")
    except AmbiguousUserEntitlementError as exc:
        assert exc.error_code == "AMBIGUOUS_USER_ENTITLEMENT"
        assert str(USER_WITH_OVERRIDE_ID) in exc.user_id
        tracker.pass_(label, "AmbiguousUserEntitlementError raised for 2 active entitlements")
    except Exception as exc:
        tracker.fail(label, f"Wrong exception type: {type(exc).__name__}: {exc}")


async def stage_1_7_inactive_entitlement_skipped(tracker: TestTracker) -> None:
    """1.7 — Inactive entitlement is filtered out; pipeline falls back to deployment path."""
    label = "1.7  Inactive entitlement skipped → falls back to deployment"
    inactive_uc = make_user_entitlement_config().model_copy(update={"is_active": False})
    svc = _make_resolution_service(entitlements_for_user={USER_WITH_OVERRIDE_ID: (inactive_uc,)})
    request = _make_resolution_request(user_id=USER_WITH_OVERRIDE_ID)
    try:
        ctx = await svc.resolve(request)
        assert ctx.resolution_source == ResolutionSource.TENANT_DEPLOYMENT, (
            f"Expected TENANT_DEPLOYMENT, got {ctx.resolution_source}"
        )
        assert ctx.deployment_config is not None
        assert ctx.user_entitlement_config is None
        tracker.pass_(
            label,
            f"Resolution fell back to TENANT_DEPLOYMENT; "
            f"deployment_key={ctx.deployment_config.deployment_key!r}",
        )
    except Exception as exc:
        tracker.fail(label, f"{type(exc).__name__}: {exc}")


async def stage_1_8_model_not_supported(tracker: TestTracker) -> None:
    """1.8 — ModelNotSupportedError when the deployment references a model unknown to the YAML catalog."""
    label = "1.8  Model not supported by provider"
    bad_model_dc = make_deployment_config().model_copy(
        update={"model_name": "gpt-999-fantasy-model-not-in-catalog"}
    )
    svc = _make_resolution_service(deployment_config=bad_model_dc)
    request = _make_resolution_request(user_id=USER_WITHOUT_OVERRIDE_ID)
    try:
        await svc.resolve(request)
        tracker.fail(label, "No exception raised — expected ModelNotSupportedError")
    except ModelNotSupportedError as exc:
        assert exc.error_code == "MODEL_NOT_SUPPORTED"
        assert "gpt-999-fantasy-model-not-in-catalog" in str(exc)
        tracker.pass_(
            label,
            f"ModelNotSupportedError: provider={exc.provider_name!r} model={exc.model_name!r}",
        )
    except Exception as exc:
        tracker.fail(label, f"Wrong exception type: {type(exc).__name__}: {exc}")


async def stage_1_9_operation_not_supported(tracker: TestTracker) -> None:
    """1.9 — OperationNotSupportedError: gpt-4o-mini is chat-only; EMBED is rejected."""
    label = "1.9  Operation not supported by model (EMBED on chat-only gpt-4o-mini)"
    svc = _make_resolution_service()
    request = _make_resolution_request(
        user_id=USER_WITHOUT_OVERRIDE_ID,
        operation=OperationType.EMBED,
    )
    try:
        await svc.resolve(request)
        tracker.fail(label, "No exception raised — expected OperationNotSupportedError")
    except OperationNotSupportedError as exc:
        assert exc.error_code == "OPERATION_NOT_SUPPORTED"
        assert exc.operation == "embed"
        assert exc.model_name == "gpt-4o-mini"
        tracker.pass_(
            label,
            f"OperationNotSupportedError: model={exc.model_name!r} op={exc.operation!r}",
        )
    except Exception as exc:
        tracker.fail(label, f"Wrong exception type: {type(exc).__name__}: {exc}")


async def stage_1_10_empty_entitlement_falls_back(tracker: TestTracker) -> None:
    """1.10 — Empty entitlement list (no match) falls back to the tenant deployment path successfully."""
    label = "1.10 Empty entitlement list → deployment path resolves"
    # USER_WITHOUT_OVERRIDE_ID has no entitlement entry in the default store
    svc = _make_resolution_service()
    request = _make_resolution_request(user_id=USER_WITHOUT_OVERRIDE_ID)
    try:
        ctx = await svc.resolve(request)
        assert ctx.resolution_source == ResolutionSource.TENANT_DEPLOYMENT
        assert ctx.deployment_config is not None
        assert ctx.user_entitlement_config is None
        assert ctx.provider_name == "openai"
        assert ctx.model_name == "gpt-4o-mini"
        tracker.pass_(
            label,
            f"Resolved via TENANT_DEPLOYMENT; fingerprint={ctx.route_fingerprint[:20]}...",
        )
    except Exception as exc:
        tracker.fail(label, f"{type(exc).__name__}: {exc}")


async def run_stage_1(tracker: TestTracker) -> None:
    _print_stage_header(1, "Resolution Pipeline Edge Cases")
    await stage_1_1_tenant_not_found(tracker)
    await stage_1_2_tenant_suspended(tracker)
    await stage_1_3_provider_not_allowed(tracker)
    await stage_1_4_deployment_not_found(tracker)
    await stage_1_5_deployment_inactive(tracker)
    await stage_1_6_ambiguous_entitlement(tracker)
    await stage_1_7_inactive_entitlement_skipped(tracker)
    await stage_1_8_model_not_supported(tracker)
    await stage_1_9_operation_not_supported(tracker)
    await stage_1_10_empty_entitlement_falls_back(tracker)


# ===========================================================================
# STAGE 2 — Circuit Breaker Scenarios
# ===========================================================================


async def stage_2_1_closed_breaker_allows_call(tracker: TestTracker) -> None:
    """2.1 — Fresh CLOSED breaker lets the call through; fail_counter stays at 0."""
    label = "2.1  Fresh CLOSED breaker allows call through"
    provider, breaker, http_client = _make_provider(
        [(200, _fake_200_chat_body())],
        fail_max=3,
    )
    try:
        assert breaker.current_state.name == "CLOSED", (
            f"Expected CLOSED, got {breaker.current_state.name}"
        )
        assert breaker.fail_counter == 0

        response = await provider.generate(_make_chat_request())

        assert response.content == "circuit breaker test response"
        assert breaker.fail_counter == 0, (
            f"fail_counter should be 0 after success, got {breaker.fail_counter}"
        )
        assert breaker.current_state.name == "CLOSED"
        tracker.pass_(
            label,
            f"call succeeded; fail_counter={breaker.fail_counter}; "
            f"state={breaker.current_state.name}",
        )
    except Exception as exc:
        tracker.fail(label, f"{type(exc).__name__}: {exc}")
    finally:
        await http_client.aclose()


async def stage_2_2_open_breaker_rejects(tracker: TestTracker) -> None:
    """2.2 — OPEN breaker immediately rejects the call (no HTTP request is made)."""
    label = "2.2  OPEN breaker rejects call immediately"
    # We configure a 200 response — it should never be reached if the breaker is OPEN
    provider, breaker, http_client = _make_provider(
        [(200, _fake_200_chat_body())],
        fail_max=5,
        timeout_duration=timedelta(seconds=3600),
    )
    try:
        breaker.open()  # Force OPEN without needing failure counting
        assert breaker.current_state.name == "OPEN"

        await provider.generate(_make_chat_request())
        tracker.fail(label, "Call went through — expected CircuitBreakerError")
    except aiobreaker.CircuitBreakerError:
        tracker.pass_(label, f"CircuitBreakerError raised; state={breaker.current_state.name}")
    except Exception as exc:
        tracker.fail(label, f"Wrong exception type: {type(exc).__name__}: {exc}")
    finally:
        await http_client.aclose()


async def stage_2_3_half_open_allows_trial(tracker: TestTracker) -> None:
    """2.3 — HALF_OPEN breaker allows a single trial call; success transitions back to CLOSED."""
    label = "2.3  HALF_OPEN breaker allows trial call → transitions to CLOSED on success"
    provider, breaker, http_client = _make_provider(
        [(200, _fake_200_chat_body())],
        fail_max=1,
    )
    try:
        breaker.half_open()  # Force HALF_OPEN directly
        assert breaker.current_state.name == "HALF_OPEN"

        response = await provider.generate(_make_chat_request())

        assert response.content == "circuit breaker test response"
        assert breaker.current_state.name == "CLOSED", (
            f"Expected CLOSED after trial success, got {breaker.current_state.name}"
        )
        tracker.pass_(label, "Trial call succeeded; breaker returned to CLOSED")
    except Exception as exc:
        tracker.fail(label, f"{type(exc).__name__}: {exc}")
    finally:
        await http_client.aclose()


async def stage_2_4_failure_counting_opens_breaker(tracker: TestTracker) -> None:
    """2.4 — Three consecutive 500s (fail_max=3) drive CLOSED → OPEN; additional calls rejected.

    aiobreaker state machine:
      - Calls 1..(fail_max-1): original exception propagates, fail_counter increments, stays CLOSED
      - Call fail_max:         CircuitBreakerError("Failures threshold reached...") → transitions OPEN
      - Calls (fail_max+1)+:  CircuitBreakerError("Timeout not elapsed yet...")
    """
    label = "2.4  Failure counting drives CLOSED → OPEN (fail_max=3)"
    fail_max = 3
    provider, breaker, http_client = _make_provider(
        [
            (500, _fake_500_body()),  # fail #1 — ProviderError, CLOSED
            (500, _fake_500_body()),  # fail #2 — ProviderError, CLOSED
            (500, _fake_500_body()),  # fail #3 — CircuitBreakerError("threshold reached") + OPEN
            (200, _fake_200_chat_body()),  # would succeed but breaker blocks it
        ],
        fail_max=fail_max,
        timeout_duration=timedelta(seconds=3600),
    )
    try:
        from app.core.exceptions import ProviderError

        assert breaker.fail_counter == 0
        assert breaker.current_state.name == "CLOSED"

        # Calls 1 to (fail_max - 1): ProviderError propagates; fail_counter grows; stays CLOSED
        for attempt in range(1, fail_max):
            try:
                await provider.generate(_make_chat_request())
                tracker.fail(label, f"Call #{attempt} should have raised ProviderError")
                return
            except ProviderError:
                assert breaker.fail_counter == attempt, (
                    f"fail_counter expected {attempt} after call #{attempt}, "
                    f"got {breaker.fail_counter}"
                )
                assert breaker.current_state.name == "CLOSED"

        # Call fail_max: aiobreaker counts the failure, opens the circuit,
        # and raises CircuitBreakerError("Failures threshold reached...") — NOT ProviderError
        try:
            await provider.generate(_make_chat_request())
            tracker.fail(label, f"Call #{fail_max} should have raised CircuitBreakerError")
            return
        except aiobreaker.CircuitBreakerError as exc:
            assert "threshold" in exc.args[0].lower() or "opened" in exc.args[0].lower(), (
                f"Expected 'threshold reached' message, got: {exc.args[0]!r}"
            )
            assert breaker.current_state.name == "OPEN", (
                f"Expected OPEN after fail_max failures, got {breaker.current_state.name}"
            )

        # Subsequent call — breaker is OPEN, 1-hour timeout not elapsed → rejected immediately
        try:
            await provider.generate(_make_chat_request())
            tracker.fail(label, "Post-open call should have raised CircuitBreakerError")
            return
        except aiobreaker.CircuitBreakerError as exc:
            assert "elapsed" in exc.args[0].lower() or "open" in exc.args[0].lower(), (
                f"Expected 'still open' message, got: {exc.args[0]!r}"
            )
            tracker.pass_(
                label,
                f"Calls 1-{fail_max - 1}: ProviderError counted; "
                f"call {fail_max}: CircuitBreakerError('threshold reached') + OPEN; "
                f"call {fail_max + 1}: CircuitBreakerError('timeout not elapsed')",
            )
    except Exception as exc:
        tracker.fail(label, f"Unexpected: {type(exc).__name__}: {exc}")
    finally:
        await http_client.aclose()


async def stage_2_5_stream_failure_trips_breaker(tracker: TestTracker) -> None:
    """2.5 — A 500 on a streaming call propagates through the queue and is counted by the breaker.

    With fail_max=1, the first internal failure (ProviderError from the 500) causes
    aiobreaker to open the circuit and raise CircuitBreakerError("Failures threshold reached")
    instead of the original ProviderError.  That error is relayed through the stream queue
    to the stream_generate caller.  The breaker is now OPEN; the next regular generate()
    call is rejected with CircuitBreakerError("Timeout not elapsed yet").
    """
    label = "2.5  Stream failure is counted by the breaker"
    provider, breaker, http_client = _make_provider(
        [
            (500, _fake_500_body()),  # streaming POST returns 500
            (200, _fake_200_chat_body()),  # would succeed if breaker allows (it won't)
        ],
        fail_max=1,
        timeout_duration=timedelta(seconds=3600),
    )
    try:
        assert breaker.current_state.name == "CLOSED"
        assert breaker.fail_counter == 0

        # Stream the call — the 500 triggers the internal ProviderError, which aiobreaker
        # counts as failure #1 = fail_max, opens the circuit, and raises CircuitBreakerError.
        # That error is packaged as _StreamError in the queue and re-raised here.
        stream_exception: BaseException | None = None
        try:
            async for _ in provider.stream_generate(_make_chat_request()):
                pass
        except aiobreaker.CircuitBreakerError as exc:
            stream_exception = exc  # "Failures threshold reached, circuit breaker opened."
        except Exception as exc:
            stream_exception = exc  # Capture any other unexpected error

        assert stream_exception is not None, (
            "stream_generate should have raised an exception on the 500 response"
        )
        assert isinstance(stream_exception, aiobreaker.CircuitBreakerError), (
            f"Expected CircuitBreakerError from stream queue, got {type(stream_exception).__name__}"
        )
        assert (
            "threshold" in stream_exception.args[0].lower()
            or "opened" in stream_exception.args[0].lower()
        ), f"Unexpected CircuitBreakerError message: {stream_exception.args[0]!r}"
        assert breaker.current_state.name == "OPEN", (
            f"Breaker should be OPEN after stream failure, got {breaker.current_state.name}"
        )

        # Next regular generate() is blocked — breaker is OPEN, timeout not elapsed
        try:
            await provider.generate(_make_chat_request())
            tracker.fail(label, "Post-stream-failure call should have raised CircuitBreakerError")
            return
        except aiobreaker.CircuitBreakerError as exc:
            assert "elapsed" in exc.args[0].lower() or "open" in exc.args[0].lower()
            tracker.pass_(
                label,
                f"Stream 500 → {type(stream_exception).__name__}('threshold reached') in queue; "
                f"breaker OPEN; next generate() rejected with CircuitBreakerError('timeout not elapsed')",
            )
    except Exception as exc:
        tracker.fail(label, f"Unexpected: {type(exc).__name__}: {exc}")
    finally:
        await http_client.aclose()


async def run_stage_2(tracker: TestTracker) -> None:
    _print_stage_header(2, "Circuit Breaker Scenarios")
    await stage_2_1_closed_breaker_allows_call(tracker)
    await stage_2_2_open_breaker_rejects(tracker)
    await stage_2_3_half_open_allows_trial(tracker)
    await stage_2_4_failure_counting_opens_breaker(tracker)
    await stage_2_5_stream_failure_trips_breaker(tracker)


# ===========================================================================
# STAGE 3 — Secret Store Scenarios
# ===========================================================================


def stage_3_1_env_store_missing_var(tracker: TestTracker) -> None:
    """3.1 — EnvironmentSecretStore raises KeyError when the env variable is not set."""
    label = "3.1  EnvironmentSecretStore: missing env variable raises KeyError"
    ref = "NONEXISTENT_SECRET_REF_UNIQUE_12345"
    os.environ.pop(ref, None)  # ensure it is unset
    store = EnvironmentSecretStore()
    try:
        store.get_secret(ref, tenant_id="any-tenant")
        tracker.fail(label, "No exception raised — expected KeyError")
    except KeyError as exc:
        assert ref in str(exc)
        tracker.pass_(label, f"KeyError raised for unset ref {ref!r}")
    except Exception as exc:
        tracker.fail(label, f"Wrong exception type: {type(exc).__name__}: {exc}")


def stage_3_2_env_store_correct_var(tracker: TestTracker) -> None:
    """3.2 — EnvironmentSecretStore returns the plaintext value of a set env variable."""
    label = "3.2  EnvironmentSecretStore: correctly set env variable is returned"
    ref = "STAGE_3_2_TEST_SECRET_VAR"
    expected = "sk-test-pipeline-value-xyz"
    os.environ[ref] = expected
    store = EnvironmentSecretStore()
    try:
        value = store.get_secret(ref, tenant_id="ignored")
        assert value == expected, f"Expected {expected!r}, got {value!r}"
        tracker.pass_(label, f"Returned correct value for ref {ref!r}")
    except Exception as exc:
        tracker.fail(label, f"{type(exc).__name__}: {exc}")
    finally:
        os.environ.pop(ref, None)


def stage_3_3_aesgcm_missing_reference(tracker: TestTracker) -> None:
    """3.3 — AESGCMSecretStore raises KeyError for a reference that was never registered."""
    label = "3.3  AESGCMSecretStore: unregistered reference raises KeyError"
    master_key_bytes = os.urandom(32)
    master_key_b64 = base64.b64encode(master_key_bytes).decode()
    store = AESGCMSecretStore(master_key_b64=master_key_b64)
    try:
        store.get_secret("secret/never-registered/key", tenant_id=str(TENANT_ID))
        tracker.fail(label, "No exception raised — expected KeyError")
    except KeyError as exc:
        assert "never-registered" in str(exc)
        tracker.pass_(label, "KeyError raised for unregistered reference")
    except Exception as exc:
        tracker.fail(label, f"Wrong exception type: {type(exc).__name__}: {exc}")


def stage_3_4_aesgcm_wrong_tenant_decryption_fails(tracker: TestTracker) -> None:
    """3.4 — AESGCMSecretStore raises ValueError when decrypting with a different tenant_id.

    Security invariant: each tenant has a unique HKDF-derived key, so ciphertext
    encrypted under tenant-A CANNOT be decrypted under tenant-B.
    """
    label = "3.4  AESGCMSecretStore: wrong tenant_id causes decryption failure (ValueError)"
    master_key_bytes = os.urandom(32)
    master_key_b64 = base64.b64encode(master_key_bytes).decode()
    tenant_a = "tenant-a-uuid-isolation-test"
    tenant_b = "tenant-b-uuid-isolation-test"
    plaintext = "sk-super-secret-api-key-for-tenant-a"

    # Encrypt with tenant-A's derived key
    ciphertext_b64 = encrypt_api_key(plaintext, master_key_bytes, tenant_a)

    store = AESGCMSecretStore(master_key_b64=master_key_b64)
    store.register_secret("secret/tenant-a/openai", ciphertext_b64)

    # Decrypt with tenant-A — must succeed
    try:
        recovered = store.get_secret("secret/tenant-a/openai", tenant_id=tenant_a)
        assert recovered == plaintext
    except Exception as exc:
        tracker.fail(label, f"Decrypt with correct tenant_id failed: {exc}")
        return

    # Decrypt with tenant-B — must fail (different HKDF-derived key → InvalidTag → ValueError)
    try:
        store.get_secret("secret/tenant-a/openai", tenant_id=tenant_b)
        tracker.fail(label, "Cross-tenant decryption succeeded — tenant isolation is broken!")
    except ValueError as exc:
        assert "Decryption failed" in str(exc), f"Unexpected ValueError message: {exc}"
        tracker.pass_(
            label,
            "Cross-tenant decryption correctly raises ValueError; "
            "tenant isolation confirmed via HKDF key separation",
        )
    except Exception as exc:
        tracker.fail(label, f"Wrong exception type: {type(exc).__name__}: {exc}")


async def run_stage_3(tracker: TestTracker) -> None:
    _print_stage_header(3, "Secret Store Scenarios")
    stage_3_1_env_store_missing_var(tracker)
    stage_3_2_env_store_correct_var(tracker)
    stage_3_3_aesgcm_missing_reference(tracker)
    stage_3_4_aesgcm_wrong_tenant_decryption_fails(tracker)


# ===========================================================================
# STAGE 4 — Context Adapter Edge Cases
# ===========================================================================


async def stage_4_1_adapter_deployment_path(tracker: TestTracker) -> None:
    """4.1 — context_to_deployment_config returns the original DeploymentConfig unchanged
    when resolution_source is TENANT_DEPLOYMENT (deployment_config is set).
    """
    label = "4.1  Context adapter: deployment path returns original DeploymentConfig"
    svc = _make_resolution_service()
    request = _make_resolution_request(user_id=USER_WITHOUT_OVERRIDE_ID)
    try:
        ctx = await svc.resolve(request)
        assert ctx.resolution_source == ResolutionSource.TENANT_DEPLOYMENT
        assert ctx.deployment_config is not None

        result_dc = context_to_deployment_config(ctx, ctx.deployment_config.deployment_key)

        # Must be the same object / same data (not a synthetic copy)
        assert result_dc.deployment_id == ctx.deployment_config.deployment_id
        assert result_dc.deployment_key == ctx.deployment_config.deployment_key
        assert result_dc is ctx.deployment_config, (
            "Deployment path should return the exact same DeploymentConfig object"
        )
        tracker.pass_(
            label,
            f"Returned original; deployment_id={result_dc.deployment_id}",
        )
    except Exception as exc:
        tracker.fail(label, f"{type(exc).__name__}: {exc}")


async def stage_4_2_adapter_entitlement_path(tracker: TestTracker) -> None:
    """4.2 — context_to_deployment_config synthesises a transient DeploymentConfig
    when resolution_source is USER_ENTITLEMENT (deployment_config is None).
    """
    label = "4.2  Context adapter: entitlement path synthesises DeploymentConfig"
    svc = _make_resolution_service()
    request = _make_resolution_request(
        user_id=USER_WITH_OVERRIDE_ID,
        requested_model_name="gpt-4o-mini",
    )
    try:
        ctx = await svc.resolve(request)
        assert ctx.resolution_source == ResolutionSource.USER_ENTITLEMENT, (
            f"Expected USER_ENTITLEMENT, got {ctx.resolution_source}"
        )
        assert ctx.deployment_config is None
        assert ctx.user_entitlement_config is not None

        dc_key = make_deployment_config().deployment_key
        synthetic_dc = context_to_deployment_config(ctx, dc_key)

        assert synthetic_dc.deployment_name.startswith("user-entitlement:"), (
            f"Expected synthetic name to start with 'user-entitlement:'; got {synthetic_dc.deployment_name!r}"
        )
        assert synthetic_dc.provider_name == ctx.provider_name
        assert synthetic_dc.model_name == ctx.model_name
        assert synthetic_dc.tenant_id == ctx.tenant_config.tenant_id
        # Must not be the same object as anything stored (it's freshly synthesised)
        assert synthetic_dc.deployment_key == dc_key
        tracker.pass_(
            label,
            f"Synthesised DeploymentConfig: name={synthetic_dc.deployment_name!r}; "
            f"id={synthetic_dc.deployment_id}",
        )
    except Exception as exc:
        tracker.fail(label, f"{type(exc).__name__}: {exc}")


async def stage_4_3_adapter_neither_raises(tracker: TestTracker) -> None:
    """4.3 — context_to_deployment_config raises ValueError when both
    deployment_config and user_entitlement_config are None.
    """
    label = "4.3  Context adapter: neither path raises ValueError"
    # SimpleNamespace satisfies the attribute-access contract of ResolvedExecutionContext
    fake_ctx = SimpleNamespace(
        deployment_config=None,
        user_entitlement_config=None,
    )
    try:
        context_to_deployment_config(fake_ctx, "any-deployment-key")  # type: ignore[arg-type]
        tracker.fail(label, "No exception raised — expected ValueError")
    except ValueError as exc:
        assert "neither deployment nor entitlement" in str(exc).lower(), (
            f"Unexpected ValueError message: {exc}"
        )
        tracker.pass_(label, f"ValueError raised: {exc}")
    except Exception as exc:
        tracker.fail(label, f"Wrong exception type: {type(exc).__name__}: {exc}")


async def run_stage_4(tracker: TestTracker) -> None:
    _print_stage_header(4, "Context Adapter Edge Cases")
    await stage_4_1_adapter_deployment_path(tracker)
    await stage_4_2_adapter_entitlement_path(tracker)
    await stage_4_3_adapter_neither_raises(tracker)


# ===========================================================================
# STAGE 5 — Protocol Store Edge Cases
# ===========================================================================


async def stage_5_1_tenant_store_hit(tracker: TestTracker) -> None:
    """5.1 — _TenantStore returns TenantConfig for the known tenant_id."""
    label = "5.1  _TenantStore: known tenant_id returns TenantConfig"
    tc = make_tenant_config()
    store = _TenantStore(tc)
    try:
        result = await store.get_tenant_config(TENANT_ID)
        assert result is not None
        assert result.tenant_id == tc.tenant_id
        assert result.tenant_slug == tc.tenant_slug
        tracker.pass_(label, f"Returned config for tenant_slug={result.tenant_slug!r}")
    except Exception as exc:
        tracker.fail(label, f"{type(exc).__name__}: {exc}")


async def stage_5_2_tenant_store_miss(tracker: TestTracker) -> None:
    """5.2 — _TenantStore returns None for an unknown tenant_id."""
    label = "5.2  _TenantStore: unknown tenant_id returns None"
    store = _TenantStore(make_tenant_config())
    try:
        result = await store.get_tenant_config(uuid4())
        assert result is None, f"Expected None, got {result!r}"
        tracker.pass_(label, "Returned None for unknown tenant_id")
    except Exception as exc:
        tracker.fail(label, f"{type(exc).__name__}: {exc}")


async def stage_5_3_entitlement_store_no_match(tracker: TestTracker) -> None:
    """5.3 — _EntitlementStore returns empty list when the user has no entitlements."""
    label = "5.3  _EntitlementStore: user with no entitlements returns []"
    uc = make_user_entitlement_config()
    store = _EntitlementStore(
        deployment_key="openai-direct-mini",
        entitlements={uc.user_id: (uc,)},
    )
    try:
        result = await store.find_matching_entitlements(
            tenant_id=TENANT_ID,
            user_id=uuid4(),  # user not in the store
            deployment_key="openai-direct-mini",
        )
        assert result == [], f"Expected [], got {result!r}"
        tracker.pass_(label, "Returned [] for unknown user_id")
    except Exception as exc:
        tracker.fail(label, f"{type(exc).__name__}: {exc}")


async def stage_5_4_entitlement_store_model_filter(tracker: TestTracker) -> None:
    """5.4 — _EntitlementStore model_name filter: user has two entitlements for different
    models; only the matching one is returned when requested_model_name is set.
    """
    label = "5.4  _EntitlementStore: model_name filter narrows multi-entitlement list"
    base_uc = make_user_entitlement_config()  # model_name="gpt-4o-mini"
    gpt4_uc = base_uc.model_copy(
        update={
            "entitlement_id": uuid4(),
            "entitlement_name": "Personal GPT-4o",
            "model_name": "gpt-4o",
        }
    )
    store = _EntitlementStore(
        deployment_key="openai-direct-mini",
        entitlements={USER_WITH_OVERRIDE_ID: (base_uc, gpt4_uc)},
    )
    try:
        # Filter to gpt-4o-mini only
        results = await store.find_matching_entitlements(
            tenant_id=TENANT_ID,
            user_id=USER_WITH_OVERRIDE_ID,
            deployment_key="openai-direct-mini",
            requested_model_name="gpt-4o-mini",
        )
        assert len(results) == 1, f"Expected 1 result, got {len(results)}"
        assert results[0].model_name == "gpt-4o-mini"

        # Filter to gpt-4o
        results_4o = await store.find_matching_entitlements(
            tenant_id=TENANT_ID,
            user_id=USER_WITH_OVERRIDE_ID,
            deployment_key="openai-direct-mini",
            requested_model_name="gpt-4o",
        )
        assert len(results_4o) == 1
        assert results_4o[0].model_name == "gpt-4o"

        tracker.pass_(
            label,
            "model_name filter correctly narrows [gpt-4o-mini, gpt-4o] to individual entries",
        )
    except Exception as exc:
        tracker.fail(label, f"{type(exc).__name__}: {exc}")


async def stage_5_5_entitlement_store_wrong_deployment_key(tracker: TestTracker) -> None:
    """5.5 — _EntitlementStore returns [] when the deployment_key does not match."""
    label = "5.5  _EntitlementStore: wrong deployment_key returns []"
    uc = make_user_entitlement_config()
    store = _EntitlementStore(
        deployment_key="openai-direct-mini",  # store only handles this key
        entitlements={USER_WITH_OVERRIDE_ID: (uc,)},
    )
    try:
        result = await store.find_matching_entitlements(
            tenant_id=TENANT_ID,
            user_id=USER_WITH_OVERRIDE_ID,
            deployment_key="completely-different-key",
        )
        assert result == [], f"Expected [], got {result!r}"
        tracker.pass_(label, "Returned [] for mismatched deployment_key")
    except Exception as exc:
        tracker.fail(label, f"{type(exc).__name__}: {exc}")


async def stage_5_6_entitlement_store_wrong_tenant(tracker: TestTracker) -> None:
    """5.6 — _EntitlementStore returns [] when tenant_id on the entitlement doesn't match."""
    label = "5.6  _EntitlementStore: wrong tenant_id returns []"
    uc = make_user_entitlement_config()  # tenant_id=TENANT_ID
    store = _EntitlementStore(
        deployment_key="openai-direct-mini",
        entitlements={USER_WITH_OVERRIDE_ID: (uc,)},
    )
    try:
        result = await store.find_matching_entitlements(
            tenant_id=uuid4(),  # different from uc.tenant_id
            user_id=USER_WITH_OVERRIDE_ID,
            deployment_key="openai-direct-mini",
        )
        assert result == [], f"Expected [], got {result!r}"
        tracker.pass_(label, "Returned [] when requested tenant_id != entitlement.tenant_id")
    except Exception as exc:
        tracker.fail(label, f"{type(exc).__name__}: {exc}")


async def stage_5_7_deployment_store_hit(tracker: TestTracker) -> None:
    """5.7 — _DeploymentStore returns DeploymentConfig for a known key and matching tenant."""
    label = "5.7  _DeploymentStore: known key + correct tenant returns DeploymentConfig"
    dc = make_deployment_config()
    store = _DeploymentStore(deployments={dc.deployment_key: dc})
    try:
        result = await store.resolve(dc.tenant_id, dc.deployment_key)
        assert result.deployment_id == dc.deployment_id
        assert result.deployment_key == dc.deployment_key
        tracker.pass_(
            label,
            f"Returned DeploymentConfig for key={result.deployment_key!r}",
        )
    except Exception as exc:
        tracker.fail(label, f"{type(exc).__name__}: {exc}")


async def stage_5_8_deployment_store_not_found(tracker: TestTracker) -> None:
    """5.8 — _DeploymentStore raises LegacyDeploymentNotFoundError for an unknown key."""
    label = "5.8  _DeploymentStore: unknown deployment_key raises LegacyDeploymentNotFoundError"
    dc = make_deployment_config()
    store = _DeploymentStore(deployments={dc.deployment_key: dc})
    try:
        await store.resolve(dc.tenant_id, "nonexistent-key-xyz")
        tracker.fail(label, "No exception raised — expected LegacyDeploymentNotFoundError")
    except LegacyDeploymentNotFoundError as exc:
        assert exc.deployment_key == "nonexistent-key-xyz"
        tracker.pass_(
            label,
            f"LegacyDeploymentNotFoundError raised for key={exc.deployment_key!r}",
        )
    except Exception as exc:
        tracker.fail(label, f"Wrong exception type: {type(exc).__name__}: {exc}")


async def stage_5_9_deployment_store_wrong_tenant(tracker: TestTracker) -> None:
    """5.9 — _DeploymentStore raises LegacyDeploymentNotFoundError when the deployment
    exists but belongs to a different tenant (tenant isolation enforced).
    """
    label = (
        "5.9  _DeploymentStore: key exists but wrong tenant_id raises LegacyDeploymentNotFoundError"
    )
    dc = make_deployment_config()  # tenant_id=TENANT_ID
    store = _DeploymentStore(deployments={dc.deployment_key: dc})
    different_tenant_id = uuid4()
    try:
        await store.resolve(different_tenant_id, dc.deployment_key)
        tracker.fail(label, "No exception raised — expected LegacyDeploymentNotFoundError")
    except LegacyDeploymentNotFoundError as exc:
        assert str(exc.tenant_id) == str(different_tenant_id), (
            f"Error should reference the requesting tenant {different_tenant_id}"
        )
        assert exc.deployment_key == dc.deployment_key
        tracker.pass_(
            label,
            f"LegacyDeploymentNotFoundError raised; "
            f"key={exc.deployment_key!r} blocked for cross-tenant lookup",
        )
    except Exception as exc:
        tracker.fail(label, f"Wrong exception type: {type(exc).__name__}: {exc}")


async def run_stage_5(tracker: TestTracker) -> None:
    _print_stage_header(5, "Protocol Store Edge Cases")
    await stage_5_1_tenant_store_hit(tracker)
    await stage_5_2_tenant_store_miss(tracker)
    await stage_5_3_entitlement_store_no_match(tracker)
    await stage_5_4_entitlement_store_model_filter(tracker)
    await stage_5_5_entitlement_store_wrong_deployment_key(tracker)
    await stage_5_6_entitlement_store_wrong_tenant(tracker)
    await stage_5_7_deployment_store_hit(tracker)
    await stage_5_8_deployment_store_not_found(tracker)
    await stage_5_9_deployment_store_wrong_tenant(tracker)


# ===========================================================================
# Entry Point
# ===========================================================================


async def main() -> None:
    print("=" * 66)
    print("  Resolution Pipeline Thorough Execution Tests")
    print("  No API endpoints | No mocking frameworks | Real service code")
    print("=" * 66)

    tracker = TestTracker()

    await run_stage_1(tracker)
    await run_stage_2(tracker)
    await run_stage_3(tracker)
    await run_stage_4(tracker)
    await run_stage_5(tracker)

    tracker.summary()

    # Exit with non-zero code if any test failed (useful for CI)
    failed_count = sum(1 for r in tracker.results if not r.passed)
    if failed_count:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
