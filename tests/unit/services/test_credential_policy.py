"""Specification tests for service-layer credential storage policy."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.auth.authorization.tenant_access import TenantAccessService
from app.core.exceptions import (
    DatabaseUnavailableError,
    ManagementValidationError,
    ResourceNotFoundError,
    SecretBackendUnavailableError,
)
from app.schemas.auth_schema import AuthTokenPayload
from app.schemas.enums import ProviderCatalogAuthMode
from app.schemas.management_schema import (
    BearerCredential,
    DeploymentCreateRequest,
    DeploymentUpdateRequest,
)
from app.services.credential_encoding import (
    CredentialWriter,
    build_credential_path,
    credential_owner_segment,
    delete_orphaned_secret,
    encode_credential,
)
from app.services.management_reference_validation import ManagementReferenceValidationService
from app.services.tenants.deployment import TenantDeploymentService

if TYPE_CHECKING:
    from app.auth.authorization.authorization_grant_cache import AuthorizationGrantCache
    from app.database import (
        ModelCatalogPersistence,
        ProviderCatalogPersistence,
        TenantDeploymentPersistence,
        TenantMembershipPersistence,
        TenantPersistence,
        UserPersistence,
    )

TENANT_ID = UUID("10000000-0000-0000-0000-000000000001")
PROVIDER_ID = UUID("20000000-0000-0000-0000-000000000001")
MODEL_ID = UUID("30000000-0000-0000-0000-000000000001")

ADMIN_USER = AuthTokenPayload(
    user_id=UUID("40000000-0000-0000-0000-000000000001"),
    role="admin",
    token_id=UUID("50000000-0000-0000-0000-000000000001"),
    expires_at=datetime.now(UTC) + timedelta(hours=1),
    issued_at=datetime.now(UTC),
)


class RecordingCredentialWriter:
    """Record write and delete boundaries without storing a real secret."""

    def __init__(self, *, delete_error: Exception | None = None) -> None:
        self.paths: list[str] = []
        self.deleted_paths: list[str] = []
        self._delete_error = delete_error

    async def write_secret(
        self,
        path: str,
        *,
        tenant_id: str,
        fields: dict[str, str | None],
    ) -> str:
        self.paths.append(path)
        return path

    async def delete_secret(self, secret_reference: str, *, tenant_id: str) -> None:
        if self._delete_error is not None:
            raise self._delete_error
        self.deleted_paths.append(secret_reference)


class UncertainWriteCredentialWriter(RecordingCredentialWriter):
    """Simulate Vault accepting a write before the caller loses its response."""

    def __init__(self, error: BaseException) -> None:
        super().__init__()
        self.error = error

    async def write_secret(
        self,
        path: str,
        *,
        tenant_id: str,
        fields: dict[str, str | None],
    ) -> str:
        self.paths.append(path)
        raise self.error


class ReferencePersistence:
    """Minimal repositories for deployment reference-policy tests."""

    def __init__(
        self, *, model_matches_provider: bool = True, auth_mode: str = "bearer_token"
    ) -> None:
        self.model_matches_provider = model_matches_provider
        self.auth_mode = auth_mode

    async def get_tenant_by_id(self, tenant_id: UUID) -> dict[str, object]:
        return {"tenant_id": tenant_id}

    async def get_provider_by_id(self, provider_id: UUID) -> dict[str, object]:
        return {
            "provider_id": provider_id,
            "auth_mode": self.auth_mode,
            "is_active": True,
        }

    async def get_model_by_provider_and_id(
        self,
        provider_id: UUID,
        model_id: UUID,
    ) -> dict[str, object] | None:
        if not self.model_matches_provider:
            return None
        return {"provider_id": provider_id, "model_id": model_id, "status": "active"}


@pytest.mark.asyncio
async def test_encode_credential_missing_for_secret_provider_rejects_request() -> None:
    """A bearer provider cannot silently fall back to AWS ambient identity."""
    with pytest.raises(ManagementValidationError, match="credential is required"):
        await encode_credential(
            None,
            "tenant-deployments/t-1/prod",
            "t-1",
            None,
            ProviderCatalogAuthMode.BEARER_TOKEN,
        )


@pytest.mark.asyncio
async def test_encode_credential_mismatched_mode_rejects_request() -> None:
    """The provider catalog, rather than request input, owns auth policy."""
    credential = BearerCredential(
        auth_mode=ProviderCatalogAuthMode.BEARER_TOKEN,
        api_key="secret",
    )

    with pytest.raises(ManagementValidationError, match="does not match"):
        await encode_credential(
            credential,
            "tenant-deployments/t-1/prod",
            "t-1",
            None,
            ProviderCatalogAuthMode.API_KEY_HEADER,
        )


@pytest.mark.asyncio
async def test_encode_credential_without_writer_reports_backend_unavailable() -> None:
    """Missing infrastructure is a retryable server error, not bad user input."""
    credential = BearerCredential(
        auth_mode=ProviderCatalogAuthMode.BEARER_TOKEN,
        api_key="secret",
    )

    with pytest.raises(SecretBackendUnavailableError):
        await encode_credential(
            credential,
            "tenant-deployments/t-1/prod",
            "t-1",
            None,
            ProviderCatalogAuthMode.BEARER_TOKEN,
        )


@pytest.mark.asyncio
async def test_encode_credential_each_write_uses_unique_version_path() -> None:
    """A duplicate database request must never overwrite an active secret."""
    writer = RecordingCredentialWriter()
    credential = BearerCredential(
        auth_mode=ProviderCatalogAuthMode.BEARER_TOKEN,
        api_key="secret",
    )

    first_reference = await encode_credential(
        credential,
        "tenant-deployments/t-1/prod",
        "t-1",
        cast("CredentialWriter", writer),
        ProviderCatalogAuthMode.BEARER_TOKEN,
    )
    second_reference = await encode_credential(
        credential,
        "tenant-deployments/t-1/prod",
        "t-1",
        cast("CredentialWriter", writer),
        ProviderCatalogAuthMode.BEARER_TOKEN,
    )

    assert first_reference != second_reference
    assert all("/versions/" in path for path in writer.paths)


def test_entitlement_label_becomes_safe_single_vault_segment() -> None:
    """REQ: friendly names and slashes cannot break the Vault path structure."""
    from app.adapters.secret_management.vault_client import normalize_vault_path

    owner = credential_owner_segment("Team Alpha / staging")
    path = build_credential_path("user-entitlements", "tenant", "user", owner)

    assert len(path.split("/")) == 4
    assert normalize_vault_path(path, field_name="reference") == path


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_error"),
    [
        (asyncio.CancelledError(), asyncio.CancelledError),
        (SecretBackendUnavailableError("vault", "path", "timeout"), SecretBackendUnavailableError),
    ],
)
async def test_encode_credential_cleans_up_uncertain_vault_write(
    error: BaseException, expected_error: type[BaseException]
) -> None:
    """REQ: an interrupted write cannot leave an unreferenced version behind."""
    writer = UncertainWriteCredentialWriter(error)
    credential = BearerCredential(
        auth_mode=ProviderCatalogAuthMode.BEARER_TOKEN,
        api_key="secret",
    )

    with pytest.raises(expected_error):
        await encode_credential(
            credential,
            "tenant-deployments/t-1/prod",
            "t-1",
            cast("CredentialWriter", writer),
            ProviderCatalogAuthMode.BEARER_TOKEN,
        )

    assert writer.deleted_paths == writer.paths


@pytest.mark.asyncio
async def test_reference_validation_model_from_different_provider_is_not_found() -> None:
    """Individually valid IDs cannot form a cross-provider deployment pair."""
    repositories = ReferencePersistence(model_matches_provider=False)
    service = ManagementReferenceValidationService(
        cast("TenantPersistence", repositories),
        cast("UserPersistence", repositories),
        cast("ProviderCatalogPersistence", repositories),
        cast("ModelCatalogPersistence", repositories),
    )

    with pytest.raises(ResourceNotFoundError, match="model for selected provider"):
        await service.ensure_deployment_create_references(TENANT_ID, PROVIDER_ID, MODEL_ID)


def test_deployment_update_direct_secret_reference_is_rejected() -> None:
    """Management clients rotate credentials; they never select Vault paths."""
    with pytest.raises(ValidationError):
        DeploymentUpdateRequest.model_validate({"secret_reference": "other-tenant/secret"})


@pytest.mark.asyncio
async def test_delete_orphaned_secret_calls_writer_delete() -> None:
    """A persistence failure after a real write must clean up the orphaned secret."""
    writer = RecordingCredentialWriter()

    await delete_orphaned_secret(
        cast("CredentialWriter", writer), "tenant-deployments/t-1/prod/versions/abc", "t-1"
    )

    assert writer.deleted_paths == ["tenant-deployments/t-1/prod/versions/abc"]


@pytest.mark.asyncio
async def test_delete_orphaned_secret_is_noop_without_a_writer() -> None:
    """No writer means no secret was ever written; there is nothing to compensate."""
    await delete_orphaned_secret(None, "tenant-deployments/t-1/prod/versions/abc", "t-1")


class FailingCreateDeploymentPersistence:
    """Simulate a persistence write that fails after a credential was already stored."""

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error or ValueError("duplicate deployment_key for tenant")

    async def create_deployment(self, **_kwargs: object) -> dict[str, object]:
        raise self.error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("persistence_error", "expected_error", "should_delete"),
    [
        (ValueError("duplicate deployment_key for tenant"), ManagementValidationError, True),
        (DatabaseUnavailableError("commit"), DatabaseUnavailableError, False),
        (RuntimeError("database unavailable"), RuntimeError, False),
        (asyncio.CancelledError(), asyncio.CancelledError, False),
    ],
)
async def test_create_deployment_compensates_only_certain_rejections(
    persistence_error: BaseException,
    expected_error: type[BaseException],
    should_delete: bool,
) -> None:
    """REQ: uncertain commit outcomes must not lose a possibly referenced secret."""
    writer = RecordingCredentialWriter()
    access_service = TenantAccessService(cast("TenantMembershipPersistence", object()))
    reference_service = ManagementReferenceValidationService(
        cast("TenantPersistence", ReferencePersistence()),
        cast("UserPersistence", ReferencePersistence()),
        cast("ProviderCatalogPersistence", ReferencePersistence()),
        cast("ModelCatalogPersistence", ReferencePersistence()),
    )
    service = TenantDeploymentService(
        cast("TenantDeploymentPersistence", FailingCreateDeploymentPersistence(persistence_error)),
        access_service,
        reference_service,
        cast("CredentialWriter", writer),
        authorization_cache=cast("AuthorizationGrantCache", object()),
    )
    request = DeploymentCreateRequest(
        provider_id=PROVIDER_ID,
        model_id=MODEL_ID,
        deployment_key="gpt4-prod",
        deployment_name="GPT-4 Production",
        api_endpoint_url="https://api.openai.com/v1",
        credential=BearerCredential(
            auth_mode=ProviderCatalogAuthMode.BEARER_TOKEN,
            api_key="sk-live-example",
        ),
        token_capacity_limit=1000,
    )

    with pytest.raises(expected_error):
        await service.create_deployment(TENANT_ID, request, ADMIN_USER)

    assert writer.deleted_paths == (writer.paths if should_delete else [])


@pytest.mark.asyncio
async def test_create_deployment_without_credential_never_calls_delete() -> None:
    """No credential means no write happened; a failure must not call delete_secret."""
    writer = RecordingCredentialWriter()
    access_service = TenantAccessService(cast("TenantMembershipPersistence", object()))
    reference_service = ManagementReferenceValidationService(
        cast("TenantPersistence", ReferencePersistence()),
        cast("UserPersistence", ReferencePersistence()),
        cast(
            "ProviderCatalogPersistence",
            ReferencePersistence(auth_mode="aws_sigv4"),
        ),
        cast("ModelCatalogPersistence", ReferencePersistence()),
    )
    service = TenantDeploymentService(
        cast("TenantDeploymentPersistence", FailingCreateDeploymentPersistence()),
        access_service,
        reference_service,
        cast("CredentialWriter", writer),
        authorization_cache=cast("AuthorizationGrantCache", object()),
    )
    request = DeploymentCreateRequest(
        provider_id=PROVIDER_ID,
        model_id=MODEL_ID,
        deployment_key="bedrock-prod",
        deployment_name="Bedrock Production",
        api_endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        credential=None,
        token_capacity_limit=1000,
    )

    with pytest.raises(ManagementValidationError):
        await service.create_deployment(TENANT_ID, request, ADMIN_USER)

    assert writer.paths == []
    assert writer.deleted_paths == []


@pytest.mark.asyncio
async def test_delete_orphaned_secret_swallows_cleanup_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A cleanup failure must never mask the original persistence error."""
    writer = RecordingCredentialWriter(delete_error=RuntimeError("vault unreachable"))

    with caplog.at_level(logging.WARNING, logger="app.services.credential_encoding"):
        await delete_orphaned_secret(
            cast("CredentialWriter", writer), "tenant-deployments/t-1/prod/versions/abc", "t-1"
        )

    assert any(
        "Failed to delete orphaned credential" in record.getMessage() for record in caplog.records
    )
