"""Specification tests for service-layer credential storage policy."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.core.exceptions import (
    ManagementValidationError,
    ResourceNotFoundError,
    SecretBackendUnavailableError,
)
from app.schemas.enums import ProviderCatalogAuthMode
from app.schemas.management_schema import BearerCredential, DeploymentUpdateRequest
from app.services.credential_encoding import CredentialWriter, encode_credential
from app.services.management_reference_validation import ManagementReferenceValidationService

if TYPE_CHECKING:
    from app.database import (
        ModelCatalogPersistence,
        ProviderCatalogPersistence,
        TenantPersistence,
        UserPersistence,
    )

TENANT_ID = UUID("10000000-0000-0000-0000-000000000001")
PROVIDER_ID = UUID("20000000-0000-0000-0000-000000000001")
MODEL_ID = UUID("30000000-0000-0000-0000-000000000001")


class RecordingCredentialWriter:
    """Record write boundaries without storing a real secret."""

    def __init__(self) -> None:
        self.paths: list[str] = []

    async def write_secret(
        self,
        path: str,
        *,
        tenant_id: str,
        fields: dict[str, str | None],
    ) -> str:
        self.paths.append(path)
        return path


class ReferencePersistence:
    """Minimal repositories for deployment reference-policy tests."""

    def __init__(self, *, model_matches_provider: bool = True) -> None:
        self.model_matches_provider = model_matches_provider

    async def get_tenant_by_id(self, tenant_id: UUID) -> dict[str, object]:
        return {"tenant_id": tenant_id}

    async def get_provider_by_id(self, provider_id: UUID) -> dict[str, object]:
        return {
            "provider_id": provider_id,
            "auth_mode": "bearer_token",
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
