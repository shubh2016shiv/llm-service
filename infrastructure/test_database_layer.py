"""
Database persistence layer integration test.

Exercises every persistence class in full CRUD + edge-case coverage:

  Phase 0  — PostgreSQL connectivity
  Phase 1  — CREATE (dependency order; fatal on failure)
  Phase 2  — READ by primary key
  Phase 3  — READ by secondary key (name / slug / key)
  Phase 4  — Secret isolation (secret_reference never leaks from standard reads)
  Phase 5  — UPDATE (partial field writes)
  Phase 6  — Helper shortcut methods
  Phase 7  — LIST and COUNT
  Phase 8  — Routing lookup methods
  Phase 9  — Duplicate detection (expected ValueError)
  Phase 10 — Input validation guards (expected ValueError)
  Phase 11 — CLEANUP (always runs; reverse FK order)

Set CLEANUP = True (below) to delete all test records after the run.
Set CLEANUP = False to leave records in the database for post-run inspection.

Usage (from project root):
    python infrastructure/test_database_layer.py

Exit codes:
    0  all steps passed
    1  one or more steps failed
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

# ---------------------------------------------------------------------------
# Bootstrap: project root on sys.path + .env loading
# ---------------------------------------------------------------------------


def _find_project_root() -> Path:
    try:
        root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return Path(root)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return Path(__file__).resolve().parent.parent


_PROJECT_ROOT = _find_project_root()
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _load_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        os.environ.setdefault(key.strip(), raw.strip().strip("'\""))


_load_env(_PROJECT_ROOT / ".env")

# Satisfy ApplicationSettings fields that are required but irrelevant to DB tests.
os.environ.setdefault(
    "ENCRYPTION_MASTER_KEY",
    "dGVzdC1tYXN0ZXIta2V5LWZvci1jcnVkLXRlc3RzLW9ubHktMzJieXRlcw==",
)
os.environ.setdefault(
    "JWT_SECRET_KEY",
    "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
)

import _ansi  # noqa: E402 — must come after sys.path bootstrap

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

# Set False to skip Phase 11 and leave test records in the database for inspection.
CLEANUP = False

# bcrypt hash of "test-password-123" — pre-computed; avoids bcrypt overhead.
_BCRYPT_HASH = "$2b$12$LJ3m4ys3GZfnYMz8kVsKaOCkPTLxONEcR1mGzBKQFJz5c7LlSGNkO"

# Fixed user UUID so create_user receives a pre-generated UUID as required.
_USER_ID = uuid4()

# Short run suffix so repeated runs never collide on unique columns.
_RUN = uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Step runner
# ---------------------------------------------------------------------------


class _Steps:
    """Lightweight step tracker. Each step prints [OK] or [FAIL] immediately."""

    def __init__(self) -> None:
        self._passed = 0
        self._failed = 0
        self._failed_names: list[str] = []

    @property
    def all_passed(self) -> bool:
        return self._failed == 0

    def ok(self, name: str, detail: str = "") -> None:
        self._passed += 1
        suffix = f"  {detail}" if detail else ""
        _ansi.ok(f"{name}{suffix}")

    def fail(self, name: str, exc: BaseException | None = None) -> None:
        self._failed += 1
        self._failed_names.append(name)
        msg = f"{type(exc).__name__}: {exc}" if exc else "assertion failed"
        _ansi.fail(f"{name} — {msg}")

    def assert_equal(self, name: str, actual: Any, expected: Any) -> bool:
        if actual == expected:
            self.ok(name, f"= {expected!r}")
            return True
        self.fail(name, AssertionError(f"expected {expected!r}, got {actual!r}"))
        return False

    def assert_not_none(self, name: str, value: Any) -> bool:
        if value is not None:
            self.ok(name)
            return True
        self.fail(name, AssertionError("expected a value, got None"))
        return False

    def assert_none(self, name: str, value: Any) -> bool:
        if value is None:
            self.ok(name)
            return True
        self.fail(name, AssertionError(f"expected None, got {value!r}"))
        return False

    def assert_not_in(self, name: str, key: str, mapping: dict[str, Any]) -> bool:
        if key not in mapping:
            self.ok(name, f"key '{key}' absent as expected")
            return True
        self.fail(name, AssertionError(f"key '{key}' found in result — secret leaked"))
        return False

    def assert_raises(
        self, name: str, exc_type: type[Exception], exc: BaseException | None
    ) -> bool:
        if exc is not None and isinstance(exc, exc_type):
            self.ok(name, f"{exc_type.__name__} raised as expected")
            return True
        if exc is None:
            self.fail(name, AssertionError(f"expected {exc_type.__name__}, nothing raised"))
        else:
            self.fail(
                name,
                AssertionError(f"expected {exc_type.__name__}, got {type(exc).__name__}"),
            )
        return False

    def summary(self) -> None:
        total = self._passed + self._failed
        _ansi.header("Test Summary")
        print(f"  Steps run  : {total}")
        print(f"  Passed     : {self._passed}")
        print(f"  Failed     : {self._failed}")
        if self._failed:
            print()
            for name in self._failed_names:
                _ansi.fail(name)
        print()
        if self.all_passed:
            _ansi.ok("All steps passed.")
        else:
            _ansi.fail(f"{self._failed} step(s) failed — see above.")


# ---------------------------------------------------------------------------
# Main test coroutine
# ---------------------------------------------------------------------------


async def main() -> int:
    from sqlalchemy import text

    from app.database import (
        DatabaseSessionManager,
        ModelCatalogPersistence,
        ProviderCatalogPersistence,
        TenantDeploymentPersistence,
        TenantMembershipPersistence,
        TenantPersistence,
        UserEntitlementPersistence,
        UserPersistence,
    )

    steps = _Steps()

    # Pre-initialise all IDs to None so the finally block is always safe.
    provider_id: UUID | None = None
    model_id: UUID | None = None
    tenant_id: UUID | None = None
    user_id: UUID | None = None
    membership_id: UUID | None = None
    deployment_id: UUID | None = None
    deployment_key: str = f"gpt4o-prod-{_RUN}"
    entitlement_id: UUID | None = None

    # provider_name must match '^[a-z][a-z0-9_]*$' — underscores only, no hyphens.
    provider_name: str = f"openai_test_{_RUN}"

    print()
    print(_ansi.bold("Database Persistence Layer — Integration Test"))
    print("=" * 50)
    print(f"  Run suffix : {_RUN}")
    print(f"  User UUID  : {_USER_ID}")
    print()

    manager = DatabaseSessionManager()

    provider_p = ProviderCatalogPersistence()
    model_p = ModelCatalogPersistence()
    tenant_p = TenantPersistence()
    user_p = UserPersistence()
    membership_p = TenantMembershipPersistence()
    deployment_p = TenantDeploymentPersistence()
    entitlement_p = UserEntitlementPersistence()

    try:
        # =====================================================================
        # Phase 0 — Connectivity
        # =====================================================================
        _ansi.header("Phase 0 — Connectivity")

        try:
            async with manager.get_session() as session:
                result = await session.execute(
                    text("SELECT version() AS pg_version, current_database() AS db")
                )
                info = dict(result.mappings().one())
            steps.ok("DatabaseSessionManager.get_session", str(info["pg_version"])[:60])
            steps.ok("Connected to database", str(info["db"]))
        except Exception as exc:
            steps.fail("DatabaseSessionManager.get_session", exc)
            _ansi.fail("Cannot reach the database — aborting.")
            steps.summary()
            return 1

        # =====================================================================
        # Phase 1 — CREATE (fatal: each entity depends on the previous)
        # =====================================================================
        _ansi.header("Phase 1 — CREATE")

        # 1a. Provider
        try:
            provider = await provider_p.create_provider(
                provider_name=provider_name,
                display_name="OpenAI (Integration Test)",
                provider_type="cloud_api",
                auth_mode="bearer_token",
                supported_operations=["chat", "embed", "image"],
                default_api_endpoint_url="https://api.openai.com/v1",
                provider_metadata={"tier": "paid", "region": "global"},
            )
            provider_id = provider["provider_id"]
            steps.ok("create_provider", f"id={provider_id}  name={provider['provider_name']}")
        except Exception as exc:
            steps.fail("create_provider", exc)
            _ansi.fail("Provider creation failed — cannot continue.")
            steps.summary()
            return 1
        assert provider_id is not None

        # 1b. Model
        try:
            model = await model_p.create_model(
                provider_id=provider_id,
                model_name="gpt-4o",
                supported_operations=["chat", "function_calling"],
                model_version="2024-08-06",
                display_name="GPT-4o",
                context_window_tokens=128_000,
                max_output_tokens=16_384,
                default_temperature=0.70,
                pricing_metadata={"input_per_1k": 0.005, "output_per_1k": 0.015},
            )
            model_id = model["model_id"]
            steps.ok(
                "create_model",
                f"id={model_id}  name={model['model_name']} v{model['model_version']}",
            )
        except Exception as exc:
            steps.fail("create_model", exc)
            _ansi.fail("Model creation failed — cannot continue.")
            steps.summary()
            return 1
        assert model_id is not None

        # 1c. Tenant
        try:
            tenant = await tenant_p.create_tenant(
                tenant_name="Acme Corp (Integration Test)",
                tenant_slug=f"acme-corp-{_RUN}",
                tier="starter",
                status="active",
                rate_limit_requests_per_minute=500,
                rate_limit_tokens_per_minute=50_000,
                rate_limit_concurrent_requests=5,
                allowed_provider_names=[provider_name],
            )
            tenant_id = tenant["tenant_id"]
            steps.ok("create_tenant", f"id={tenant_id}  slug={tenant['tenant_slug']}")
        except Exception as exc:
            steps.fail("create_tenant", exc)
            _ansi.fail("Tenant creation failed — cannot continue.")
            steps.summary()
            return 1
        assert tenant_id is not None

        # 1d. User
        try:
            user = await user_p.create_user(
                user_id=_USER_ID,
                username=f"alice-{_RUN}",
                email=f"alice-{_RUN}@acme-corp.example.com",
                first_name="Alice",
                last_name="Tester",
                password_hash=_BCRYPT_HASH,
                platform_role="admin",
                status="active",
            )
            user_id = user["user_id"]
            steps.ok("create_user", f"id={user_id}  username={user['username']}")
        except Exception as exc:
            steps.fail("create_user", exc)
            _ansi.fail("User creation failed — cannot continue.")
            steps.summary()
            return 1
        assert user_id is not None

        # 1e. Tenant Membership
        try:
            membership = await membership_p.create_membership(
                tenant_id=tenant_id,
                user_id=user_id,
                created_by_user_id=user_id,
                tenant_role="admin",
                status="active",
            )
            membership_id = membership["membership_id"]
            steps.ok(
                "create_membership",
                f"id={membership_id}  role={membership['tenant_role']}",
            )
        except Exception as exc:
            steps.fail("create_membership", exc)

        # 1f. Tenant Deployment
        try:
            deployment = await deployment_p.create_deployment(
                tenant_id=tenant_id,
                provider_id=provider_id,
                model_id=model_id,
                deployment_key=deployment_key,
                deployment_name="GPT-4o Production (Integration Test)",
                api_endpoint_url="https://api.openai.com/v1",
                secret_reference="secret/llm-provider-service/providers/openai/default",
                token_capacity_limit=100,
                token_lock_duration_seconds=70,
                created_by_user_id=user_id,
                status="active",
                is_default=True,
                routing_priority=10,
                cloud_provider="azure",
                cloud_region="eastus",
                extra_headers={"X-Custom-Header": "test"},
                extra_config={"retry_on_rate_limit": True},
            )
            deployment_id = deployment["deployment_id"]
            steps.ok(
                "create_deployment",
                f"id={deployment_id}  key={deployment['deployment_key']}",
            )
        except Exception as exc:
            steps.fail("create_deployment", exc)
            _ansi.fail("Deployment creation failed — cannot test entitlement.")

        # 1g. User Entitlement
        if deployment_id is not None:
            try:
                entitlement = await entitlement_p.create_entitlement(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    deployment_key=deployment_key,
                    provider_id=provider_id,
                    model_id=model_id,
                    entitlement_name=f"alice-gpt4o-{_RUN}",
                    api_endpoint_url="https://api.openai.com/v1",
                    secret_reference="secret/llm-provider-service/users/alice/openai",
                    created_by_user_id=user_id,
                    status="active",
                    cloud_provider="azure",
                    cloud_region="eastus",
                    extra_config={"max_tokens_override": 4096},
                )
                entitlement_id = entitlement["entitlement_id"]
                steps.ok(
                    "create_entitlement",
                    f"id={entitlement_id}  name={entitlement['entitlement_name']}",
                )
            except Exception as exc:
                steps.fail("create_entitlement", exc)

        # =====================================================================
        # Phase 2 — READ by primary key
        # =====================================================================
        _ansi.header("Phase 2 — READ by primary key")

        try:
            row = await provider_p.get_provider_by_id(provider_id)
            steps.assert_not_none("get_provider_by_id", row)
            if row is not None:
                steps.assert_equal("  provider_name", row["provider_name"], provider_name)
                steps.assert_equal("  is_active", row["is_active"], True)
        except Exception as exc:
            steps.fail("get_provider_by_id", exc)

        try:
            row = await model_p.get_model_by_id(model_id)
            steps.assert_not_none("get_model_by_id", row)
            if row is not None:
                steps.assert_equal("  model_name", row["model_name"], "gpt-4o")
                steps.assert_equal("  status", row["status"], "active")
        except Exception as exc:
            steps.fail("get_model_by_id", exc)

        try:
            row = await tenant_p.get_tenant_by_id(tenant_id)
            steps.assert_not_none("get_tenant_by_id", row)
            if row is not None:
                steps.assert_equal("  tier", row["tier"], "starter")
                steps.assert_equal("  status", row["status"], "active")
        except Exception as exc:
            steps.fail("get_tenant_by_id", exc)

        try:
            row = await user_p.get_user_by_id(user_id)
            steps.assert_not_none("get_user_by_id", row)
            if row is not None:
                steps.assert_equal("  platform_role", row["platform_role"], "admin")
                steps.assert_equal("  status", row["status"], "active")
        except Exception as exc:
            steps.fail("get_user_by_id", exc)

        if membership_id is not None:
            try:
                row = await membership_p.get_membership_by_id(membership_id)
                steps.assert_not_none("get_membership_by_id", row)
                if row is not None:
                    steps.assert_equal("  tenant_role", row["tenant_role"], "admin")
            except Exception as exc:
                steps.fail("get_membership_by_id", exc)

        if deployment_id is not None:
            try:
                row = await deployment_p.get_deployment_by_id(deployment_id)
                steps.assert_not_none("get_deployment_by_id", row)
                if row is not None:
                    steps.assert_equal("  status", row["status"], "active")
                    steps.assert_equal("  is_default", row["is_default"], True)
            except Exception as exc:
                steps.fail("get_deployment_by_id", exc)

        if entitlement_id is not None:
            try:
                row = await entitlement_p.get_entitlement_by_id(entitlement_id)
                steps.assert_not_none("get_entitlement_by_id", row)
                if row is not None:
                    steps.assert_equal("  status", row["status"], "active")
            except Exception as exc:
                steps.fail("get_entitlement_by_id", exc)

        # =====================================================================
        # Phase 3 — READ by secondary key
        # =====================================================================
        _ansi.header("Phase 3 — READ by secondary key")

        try:
            row = await provider_p.get_provider_by_name(provider_name)
            steps.assert_not_none("get_provider_by_name", row)
        except Exception as exc:
            steps.fail("get_provider_by_name", exc)

        try:
            row = await model_p.get_model_by_name(provider_id, "gpt-4o", "2024-08-06")
            steps.assert_not_none("get_model_by_name", row)
            if row is not None:
                steps.assert_equal("  model_version", row["model_version"], "2024-08-06")
        except Exception as exc:
            steps.fail("get_model_by_name", exc)

        try:
            row = await tenant_p.get_tenant_by_slug(f"acme-corp-{_RUN}")
            steps.assert_not_none("get_tenant_by_slug", row)
        except Exception as exc:
            steps.fail("get_tenant_by_slug", exc)

        try:
            row = await user_p.get_user_by_username(f"alice-{_RUN}")
            steps.assert_not_none("get_user_by_username", row)
        except Exception as exc:
            steps.fail("get_user_by_username", exc)

        try:
            row = await user_p.get_user_by_email(f"alice-{_RUN}@acme-corp.example.com")
            steps.assert_not_none("get_user_by_email", row)
        except Exception as exc:
            steps.fail("get_user_by_email", exc)

        try:
            row = await membership_p.get_membership(tenant_id, user_id)
            steps.assert_not_none("get_membership (tenant+user)", row)
        except Exception as exc:
            steps.fail("get_membership (tenant+user)", exc)

        if deployment_id is not None:
            try:
                row = await deployment_p.get_deployment_by_key(tenant_id, deployment_key)
                steps.assert_not_none("get_deployment_by_key", row)
            except Exception as exc:
                steps.fail("get_deployment_by_key", exc)

        # Not-found path: returns None, not an exception.
        try:
            missing = await provider_p.get_provider_by_name("does_not_exist_ever")
            steps.assert_none("get_provider_by_name (not found) -> None", missing)
        except Exception as exc:
            steps.fail("get_provider_by_name (not found)", exc)

        try:
            missing = await user_p.get_user_by_id(uuid4())
            steps.assert_none("get_user_by_id (random UUID) -> None", missing)
        except Exception as exc:
            steps.fail("get_user_by_id (not found)", exc)

        # =====================================================================
        # Phase 4 — Secret isolation
        # =====================================================================
        _ansi.header("Phase 4 — Secret isolation")

        if deployment_id is not None:
            try:
                row = await deployment_p.get_deployment_by_id(deployment_id)
                steps.assert_not_in(
                    "deployment standard read excludes secret_reference",
                    "secret_reference",
                    row or {},
                )
            except Exception as exc:
                steps.fail("deployment secret isolation check", exc)

            try:
                secret_ref = await deployment_p.get_deployment_secret_reference(deployment_id)
                steps.assert_equal(
                    "get_deployment_secret_reference returns pointer",
                    secret_ref,
                    "secret/llm-provider-service/providers/openai/default",
                )
            except Exception as exc:
                steps.fail("get_deployment_secret_reference", exc)

        if entitlement_id is not None:
            try:
                row = await entitlement_p.get_entitlement_by_id(entitlement_id)
                steps.assert_not_in(
                    "entitlement standard read excludes secret_reference",
                    "secret_reference",
                    row or {},
                )
            except Exception as exc:
                steps.fail("entitlement secret isolation check", exc)

            try:
                secret_ref = await entitlement_p.get_entitlement_secret_reference(entitlement_id)
                steps.assert_equal(
                    "get_entitlement_secret_reference returns pointer",
                    secret_ref,
                    "secret/llm-provider-service/users/alice/openai",
                )
            except Exception as exc:
                steps.fail("get_entitlement_secret_reference", exc)

        # =====================================================================
        # Phase 5 — UPDATE
        # =====================================================================
        _ansi.header("Phase 5 — UPDATE")

        try:
            updated = await tenant_p.update_tenant(
                tenant_id=tenant_id,
                tenant_name="Acme Corp (Updated)",
                tier="professional",
            )
            steps.assert_not_none("update_tenant", updated)
            if updated is not None:
                steps.assert_equal(
                    "  tenant_name updated", updated["tenant_name"], "Acme Corp (Updated)"
                )
                steps.assert_equal("  tier updated", updated["tier"], "professional")
        except Exception as exc:
            steps.fail("update_tenant", exc)

        try:
            updated = await user_p.update_user(user_id=user_id, platform_role="operator")
            steps.assert_not_none("update_user (platform_role)", updated)
            if updated is not None:
                steps.assert_equal("  platform_role updated", updated["platform_role"], "operator")
        except Exception as exc:
            steps.fail("update_user (platform_role)", exc)

        if membership_id is not None:
            try:
                updated = await membership_p.update_membership(
                    membership_id=membership_id,
                    tenant_role="developer",
                )
                steps.assert_not_none("update_membership", updated)
                if updated is not None:
                    steps.assert_equal("  tenant_role updated", updated["tenant_role"], "developer")
            except Exception as exc:
                steps.fail("update_membership", exc)

        if deployment_id is not None:
            try:
                updated = await deployment_p.update_deployment(
                    deployment_id=deployment_id,
                    token_capacity_limit=200,
                    routing_priority=20,
                )
                steps.assert_not_none("update_deployment", updated)
                if updated is not None:
                    steps.assert_equal(
                        "  token_capacity_limit updated",
                        updated["token_capacity_limit"],
                        200,
                    )
                    steps.assert_equal(
                        "  routing_priority updated", updated["routing_priority"], 20
                    )
            except Exception as exc:
                steps.fail("update_deployment", exc)

        if entitlement_id is not None:
            try:
                updated = await entitlement_p.update_entitlement(
                    entitlement_id=entitlement_id,
                    cloud_region="westus2",
                )
                steps.assert_not_none("update_entitlement", updated)
                if updated is not None:
                    steps.assert_equal("  cloud_region updated", updated["cloud_region"], "westus2")
            except Exception as exc:
                steps.fail("update_entitlement", exc)

        try:
            updated = await model_p.update_model(
                provider_id=provider_id,
                model_id=model_id,
                display_name="GPT-4o (Updated)",
                max_output_tokens=32_768,
            )
            steps.assert_not_none("update_model", updated)
            if updated is not None:
                steps.assert_equal(
                    "  display_name updated", updated["display_name"], "GPT-4o (Updated)"
                )
                steps.assert_equal(
                    "  max_output_tokens updated", updated["max_output_tokens"], 32_768
                )
        except Exception as exc:
            steps.fail("update_model", exc)

        # =====================================================================
        # Phase 6 — Helper shortcut methods
        # =====================================================================
        _ansi.header("Phase 6 — Helper shortcuts")

        try:
            result = await user_p.suspend_user(user_id)
            steps.assert_not_none("suspend_user", result)
            if result is not None:
                steps.assert_equal("  status=suspended", result["status"], "suspended")
            result = await user_p.activate_user(user_id)
            steps.assert_not_none("activate_user", result)
            if result is not None:
                steps.assert_equal("  status=active", result["status"], "active")
        except Exception as exc:
            steps.fail("suspend_user / activate_user", exc)

        if membership_id is not None:
            try:
                result = await membership_p.promote_to_admin(membership_id)
                steps.assert_not_none("promote_to_admin", result)
                if result is not None:
                    steps.assert_equal("  tenant_role=admin", result["tenant_role"], "admin")
            except Exception as exc:
                steps.fail("promote_to_admin", exc)

            try:
                result = await membership_p.suspend_membership(membership_id)
                steps.assert_not_none("suspend_membership", result)
                if result is not None:
                    steps.assert_equal("  status=suspended", result["status"], "suspended")
                # Restore for routing tests below.
                await membership_p.update_membership(membership_id=membership_id, status="active")
                steps.ok("restore membership status=active")
            except Exception as exc:
                steps.fail("suspend_membership", exc)

        if deployment_id is not None:
            try:
                result = await deployment_p.set_maintenance(deployment_id)
                steps.assert_not_none("set_maintenance", result)
                if result is not None:
                    steps.assert_equal("  status=maintenance", result["status"], "maintenance")
                result = await deployment_p.set_active(deployment_id)
                steps.assert_not_none("set_active", result)
                if result is not None:
                    steps.assert_equal("  status=active", result["status"], "active")
            except Exception as exc:
                steps.fail("set_maintenance / set_active", exc)

        try:
            result = await tenant_p.suspend_tenant(tenant_id)
            steps.assert_not_none("suspend_tenant", result)
            if result is not None:
                steps.assert_equal("  status=suspended", result["status"], "suspended")
            result = await tenant_p.activate_tenant(tenant_id)
            steps.assert_not_none("activate_tenant", result)
            if result is not None:
                steps.assert_equal("  status=active", result["status"], "active")
        except Exception as exc:
            steps.fail("suspend_tenant / activate_tenant", exc)

        try:
            result = await model_p.deprecate_model(provider_id, model_id)
            steps.assert_not_none("deprecate_model", result)
            if result is not None:
                steps.assert_equal("  status=deprecated", result["status"], "deprecated")
            # Restore to active so routing tests work.
            await model_p.update_model(provider_id=provider_id, model_id=model_id, status="active")
            steps.ok("restore model status=active")
        except Exception as exc:
            steps.fail("deprecate_model / restore", exc)

        # =====================================================================
        # Phase 7 — LIST and COUNT
        # =====================================================================
        _ansi.header("Phase 7 — LIST and COUNT")

        try:
            rows = await provider_p.list_active_providers()
            steps.ok("list_active_providers", f"{len(rows)} row(s)")
            count = await provider_p.count_active_providers()
            steps.ok("count_active_providers", str(count))
        except Exception as exc:
            steps.fail("list/count providers", exc)

        try:
            rows = await model_p.list_models_by_provider(provider_id, active_only=True)
            steps.ok("list_models_by_provider (active_only)", f"{len(rows)} row(s)")
            count = await model_p.count_models_by_provider(provider_id, active_only=True)
            steps.ok("count_models_by_provider", str(count))
        except Exception as exc:
            steps.fail("list/count models", exc)

        try:
            rows = await tenant_p.list_tenants()
            steps.ok("list_tenants", f"{len(rows)} row(s)")
            count = await tenant_p.count_tenants()
            steps.ok("count_tenants", str(count))
        except Exception as exc:
            steps.fail("list/count tenants", exc)

        try:
            rows = await user_p.get_all_users(status_filter="active")
            steps.ok("get_all_users (active)", f"{len(rows)} row(s)")
            count = await user_p.count_users_by_status("active")
            steps.ok("count_users_by_status (active)", str(count))
            count = await user_p.count_users_by_role("operator")
            steps.ok("count_users_by_role (operator)", str(count))
        except Exception as exc:
            steps.fail("list/count users", exc)

        try:
            rows = await membership_p.list_tenant_memberships(tenant_id)
            steps.ok("list_tenant_memberships", f"{len(rows)} row(s)")
            rows = await membership_p.list_user_memberships(user_id)
            steps.ok("list_user_memberships", f"{len(rows)} row(s)")
            count = await membership_p.count_tenant_members(tenant_id)
            steps.ok("count_tenant_members", str(count))
            count = await membership_p.count_user_tenants(user_id)
            steps.ok("count_user_tenants", str(count))
        except Exception as exc:
            steps.fail("list/count memberships", exc)

        if deployment_id is not None:
            try:
                rows = await deployment_p.list_deployments(tenant_id)
                steps.ok("list_deployments (all)", f"{len(rows)} row(s)")
                rows = await deployment_p.list_deployments(tenant_id, active_only=True)
                steps.ok("list_deployments (active_only)", f"{len(rows)} row(s)")
                count = await deployment_p.count_deployments(tenant_id)
                steps.ok("count_deployments", str(count))
            except Exception as exc:
                steps.fail("list/count deployments", exc)

        if entitlement_id is not None:
            try:
                rows = await entitlement_p.get_user_entitlements(tenant_id, user_id)
                steps.ok("get_user_entitlements", f"{len(rows)} row(s)")
                rows = await entitlement_p.get_tenant_entitlements(tenant_id)
                steps.ok("get_tenant_entitlements", f"{len(rows)} row(s)")
                count = await entitlement_p.count_user_entitlements(tenant_id, user_id)
                steps.ok("count_user_entitlements", str(count))
                count = await entitlement_p.count_tenant_entitlements(tenant_id)
                steps.ok("count_tenant_entitlements", str(count))
            except Exception as exc:
                steps.fail("list/count entitlements", exc)

        # =====================================================================
        # Phase 8 — Routing lookup methods
        # =====================================================================
        _ansi.header("Phase 8 — Routing lookups")

        if deployment_id is not None:
            try:
                row = await deployment_p.get_default_deployment(tenant_id, provider_id)
                steps.assert_not_none("get_default_deployment", row)
                if row is not None:
                    steps.assert_equal("  is_default=True", row["is_default"], True)
            except Exception as exc:
                steps.fail("get_default_deployment", exc)

            try:
                rows = await deployment_p.list_active_deployments_for_route(
                    tenant_id, provider_id, model_id
                )
                steps.ok("list_active_deployments_for_route", f"{len(rows)} row(s)")
            except Exception as exc:
                steps.fail("list_active_deployments_for_route", exc)

        if entitlement_id is not None:
            try:
                row = await entitlement_p.get_active_entitlement_for_route(
                    tenant_id, user_id, deployment_key, provider_id, model_id
                )
                steps.assert_not_none("get_active_entitlement_for_route", row)
            except Exception as exc:
                steps.fail("get_active_entitlement_for_route", exc)

        # =====================================================================
        # Phase 9 — Duplicate detection (expected ValueError)
        # =====================================================================
        _ansi.header("Phase 9 — Duplicate detection")

        caught: Exception | None = None
        try:
            await provider_p.create_provider(
                provider_name=provider_name,  # same name as created in Phase 1
                display_name="Duplicate",
                provider_type="cloud_api",
                auth_mode="bearer_token",
                supported_operations=["chat"],
            )
        except ValueError as exc:
            caught = exc
        steps.assert_raises("duplicate provider_name raises ValueError", ValueError, caught)

        caught = None
        try:
            await tenant_p.create_tenant(
                tenant_name="Duplicate Tenant",
                tenant_slug=f"acme-corp-{_RUN}",  # same slug as created in Phase 1
            )
        except ValueError as exc:
            caught = exc
        steps.assert_raises("duplicate tenant_slug raises ValueError", ValueError, caught)

        caught = None
        try:
            await membership_p.create_membership(
                tenant_id=tenant_id,
                user_id=user_id,  # already a member
                created_by_user_id=user_id,
            )
        except ValueError as exc:
            caught = exc
        steps.assert_raises("duplicate membership raises ValueError", ValueError, caught)

        if deployment_id is not None:
            caught = None
            try:
                await deployment_p.create_deployment(
                    tenant_id=tenant_id,
                    provider_id=provider_id,
                    model_id=model_id,
                    deployment_key=deployment_key,  # same key as created in Phase 1
                    deployment_name="Duplicate Deployment",
                    api_endpoint_url="https://api.openai.com/v1",
                    secret_reference="secret/some/ref",
                    token_capacity_limit=50,
                    created_by_user_id=user_id,
                )
            except ValueError as exc:
                caught = exc
            steps.assert_raises("duplicate deployment_key raises ValueError", ValueError, caught)

        # =====================================================================
        # Phase 10 — Input validation guards
        # =====================================================================
        _ansi.header("Phase 10 — Input validation")

        caught = None
        try:
            await provider_p.create_provider(
                provider_name="valid_name",
                display_name="Test",
                provider_type="invalid_type",  # not in enum
                auth_mode="bearer_token",
                supported_operations=["chat"],
            )
        except ValueError as exc:
            caught = exc
        steps.assert_raises("invalid provider_type raises ValueError", ValueError, caught)

        caught = None
        try:
            await tenant_p.create_tenant(
                tenant_name="Test",
                tenant_slug="valid-slug",
                tier="platinum",  # not in enum
            )
        except ValueError as exc:
            caught = exc
        steps.assert_raises("invalid tier raises ValueError", ValueError, caught)

        caught = None
        try:
            await user_p.create_user(
                user_id=uuid4(),
                username="validuser",
                email="bad-email-@@@@",
                first_name="X",
                last_name="Y",
                password_hash=_BCRYPT_HASH,
                platform_role="superadmin",  # not in enum
            )
        except ValueError as exc:
            caught = exc
        steps.assert_raises("invalid platform_role raises ValueError", ValueError, caught)

        caught = None
        try:
            await model_p.create_model(
                provider_id=provider_id,
                model_name="test-model",
                supported_operations=["chat"],
                default_temperature=3.5,  # out of [0.00, 2.00]
            )
        except ValueError as exc:
            caught = exc
        steps.assert_raises("temperature out of range raises ValueError", ValueError, caught)

    finally:
        # =====================================================================
        # Phase 11 — CLEANUP (always executes; reverse FK order)
        # =====================================================================
        _ansi.header("Phase 11 — CLEANUP")

        if not CLEANUP:
            _ansi.warn("CLEANUP=False — test records left in the database for inspection.")
            await manager.close()
            steps.summary()
            raise SystemExit(0 if steps.all_passed else 1)

        # 11a. Entitlement
        if entitlement_id is not None:
            try:
                deleted = await entitlement_p.delete_entitlement(entitlement_id)
                steps.ok("delete_entitlement", f"deleted={deleted}")
            except Exception as exc:
                steps.fail("delete_entitlement", exc)
                traceback.print_exc()

        # 11b. Deployment
        if deployment_id is not None:
            try:
                deleted = await deployment_p.delete_deployment(deployment_id)
                steps.ok("delete_deployment", f"deleted={deleted}")
            except Exception as exc:
                steps.fail("delete_deployment", exc)
                traceback.print_exc()

        # 11c. Membership
        if membership_id is not None:
            try:
                deleted = await membership_p.delete_membership_by_id(membership_id)
                steps.ok("delete_membership_by_id", f"deleted={deleted}")
            except Exception as exc:
                steps.fail("delete_membership_by_id", exc)
                traceback.print_exc()

        # 11d. User
        if user_id is not None:
            try:
                deleted = await user_p.delete_user(_USER_ID)
                steps.ok("delete_user", f"deleted={deleted}")
            except Exception as exc:
                steps.fail("delete_user", exc)
                traceback.print_exc()

        # 11e. Tenant (CASCADE removes any remaining memberships/deployments/entitlements)
        if tenant_id is not None:
            try:
                deleted = await tenant_p.delete_tenant(tenant_id)
                steps.ok("delete_tenant", f"deleted={deleted}")
            except Exception as exc:
                steps.fail("delete_tenant", exc)
                traceback.print_exc()

        # 11f. Model (requires provider_id; must precede provider deletion)
        if model_id is not None and provider_id is not None:
            try:
                deleted = await model_p.delete_model(provider_id, model_id)
                steps.ok("delete_model", f"deleted={deleted}")
            except Exception as exc:
                steps.fail("delete_model", exc)
                traceback.print_exc()

        # 11g. Provider (last — model_catalog FK is ON DELETE RESTRICT)
        if provider_id is not None:
            try:
                deleted = await provider_p.delete_provider(provider_id)
                steps.ok("delete_provider", f"deleted={deleted}")
            except Exception as exc:
                steps.fail("delete_provider", exc)
                traceback.print_exc()

        await manager.close()

    # -------------------------------------------------------------------------
    # Final summary
    # -------------------------------------------------------------------------
    steps.summary()
    return 0 if steps.all_passed else 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
