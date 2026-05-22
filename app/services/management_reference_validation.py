"""
Management Reference Validation Service
=======================================

Validates that referenced resources (tenants, users, providers, and models)
exist before a management write operation is attempted.

Why this service exists:
    Many create/update payloads contain foreign-key style identifiers.
    Without pre-validation, missing references are discovered only when the
    database rejects the write. That typically yields low-level constraint
    errors that are hard for API consumers to interpret.

    This service performs explicit "does this ID exist?" checks before writes
    so callers receive precise not-found errors (for example, "Tenant not
    found") with the exact identifier that failed.

Enterprise Pattern: Reference Integrity Service Pattern
    A dedicated service owns all cross-entity existence checks. Write-focused
    services call it as a pre-flight step to keep validation consistent and
    avoid duplicated lookups.

Author: Shubham Singh
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.exceptions import ResourceNotFoundError

if TYPE_CHECKING:
    from uuid import UUID

    from app.database import (
        ModelCatalogPersistence,
        ProviderCatalogPersistence,
        TenantPersistence,
        UserPersistence,
    )


class ManagementReferenceValidationService:
    """Pre-flight validator for referenced entity identifiers.

    This service centralizes existence checks used by membership, deployment,
    and other management operations that depend on related records.
    """

    def __init__(
        self,
        tenant_persistence: TenantPersistence,
        user_persistence: UserPersistence,
        provider_persistence: ProviderCatalogPersistence,
        model_persistence: ModelCatalogPersistence,
    ) -> None:
        """Initialize with persistence dependencies for each entity type."""
        self._tenants = tenant_persistence
        self._users = user_persistence
        self._providers = provider_persistence
        self._models = model_persistence

    async def ensure_membership_create_references(self, tenant_id: UUID, user_id: UUID) -> None:
        """Validate tenant and user references before creating a membership.

        This method is called before persisting a membership record to fail
        fast with a clear domain error when either ID is invalid.
        """
        await self.ensure_tenant_exists(tenant_id)
        await self.ensure_user_exists(user_id)

    async def ensure_deployment_create_references(
        self,
        tenant_id: UUID,
        provider_id: UUID,
        model_id: UUID,
    ) -> None:
        """Validate tenant, provider, and model references before deployment create.

        Checks are intentionally sequential so the first missing dependency is
        reported deterministically.
        """
        await self.ensure_tenant_exists(tenant_id)
        await self.ensure_provider_exists(provider_id)
        await self.ensure_model_exists(model_id)

    async def ensure_tenant_exists(self, tenant_id: UUID) -> None:
        """Ensure a tenant exists for the provided identifier.

        Args:
            tenant_id: Tenant identifier referenced by a write operation.

        Raises:
            ResourceNotFoundError: If the tenant does not exist.
        """
        tenant = await self._tenants.get_tenant_by_id(tenant_id)
        if tenant is None:
            raise ResourceNotFoundError("Tenant", str(tenant_id))

    async def ensure_user_exists(self, user_id: UUID) -> None:
        """Ensure a user exists for the provided identifier.

        Args:
            user_id: User identifier referenced by a write operation.

        Raises:
            ResourceNotFoundError: If the user does not exist.
        """
        user = await self._users.get_user_by_id(user_id)
        if user is None:
            raise ResourceNotFoundError("User", str(user_id))

    async def ensure_provider_exists(self, provider_id: UUID) -> None:
        """Ensure a provider exists for the provided identifier.

        Args:
            provider_id: Provider identifier referenced by a write operation.

        Raises:
            ResourceNotFoundError: If the provider does not exist.
        """
        provider = await self._providers.get_provider_by_id(provider_id)
        if provider is None:
            raise ResourceNotFoundError("Provider", str(provider_id))

    async def ensure_model_exists(self, model_id: UUID) -> None:
        """Ensure a model exists for the provided identifier.

        Args:
            model_id: Model identifier referenced by a write operation.

        Raises:
            ResourceNotFoundError: If the model does not exist.
        """
        model = await self._models.get_model_by_id(model_id)
        if model is None:
            raise ResourceNotFoundError("Model", str(model_id))
