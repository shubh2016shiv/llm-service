"""
Provider Catalog Service
========================

Business service for managing AI provider records (OpenAI, Anthropic, AWS
Bedrock, and similar integrations) registered in the platform.

What this service does:
    Provider records are foundational metadata used by model and deployment
    workflows. This service owns provider lifecycle operations: create, list,
    count, retrieve, update, and delete.

Responsibilities:
    - Enforce request-shape validation at the service boundary.
    - Translate persistence ``ValueError`` failures into typed domain errors.
    - Sanitize returned rows so secret-bearing fields cannot leak upward.
    - Emit resource-specific not-found errors for missing identifiers.

Enterprise Pattern: CRUD Service Pattern
    Route handlers remain thin while this service centralizes provider
    business behavior, persistence orchestration, and error normalization.

Author: Shubham Singh
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.exceptions import ResourceNotFoundError
from app.services.management_helpers import (
    Row,
    clean_row,
    clean_rows,
    raise_clean_validation_error,
)

if TYPE_CHECKING:
    from uuid import UUID

    from app.database import ProviderCatalogPersistence
    from app.schemas.management_schema import ProviderCreateRequest, ProviderUpdateRequest


class ProviderCatalogService:
    """Manage the lifecycle of provider metadata records."""

    def __init__(self, provider_persistence: ProviderCatalogPersistence) -> None:
        """Initialize with a persistence adapter for provider storage operations."""
        self._providers = provider_persistence

    async def create_provider(self, request: ProviderCreateRequest) -> Row:
        """Register a new provider in the catalog.

        The payload typically includes provider identity, auth mode, endpoint
        defaults, and capability metadata used by downstream routing.
        """
        try:
            row = await self._providers.create_provider(**request.model_dump())
            return clean_row(row)
        except ValueError as exc:
            raise_clean_validation_error(exc)

    async def list_providers(self, include_inactive: bool, limit: int, offset: int) -> list[Row]:
        """List providers using offset pagination and optional status filtering.

        Args:
            include_inactive: When ``True``, include inactive providers.
            limit: Maximum providers to return for the current page.
            offset: Number of records skipped before page retrieval.
        """
        rows = (
            await self._providers.list_all_providers(limit, offset)
            if include_inactive
            else await self._providers.list_active_providers(limit, offset)
        )
        return clean_rows(rows)

    async def count_providers(self, include_inactive: bool = False) -> int:
        """Count providers using the same active/inactive filter as listing.

        This companion count method helps API callers compute pagination
        metadata without duplicating filtering logic.
        """
        if include_inactive:
            return await self._providers.count_all_providers()
        return await self._providers.count_active_providers()

    async def get_provider(self, provider_id: UUID) -> Row:
        """Retrieve one provider record by identifier."""
        row = await self._providers.get_provider_by_id(provider_id)
        if row is None:
            raise ResourceNotFoundError("Provider", str(provider_id))
        return clean_row(row)

    async def update_provider(self, provider_id: UUID, request: ProviderUpdateRequest) -> Row:
        """Partially update provider metadata fields.

        Only supplied fields are written, which prevents accidental reset of
        omitted attributes during patch-style updates.
        """
        try:
            row = await self._providers.update_provider(
                provider_id=provider_id,
                **request.model_dump(exclude_unset=True),
            )
        except ValueError as exc:
            raise_clean_validation_error(exc)
        if row is None:
            raise ResourceNotFoundError("Provider", str(provider_id))
        return clean_row(row)

    async def delete_provider(self, provider_id: UUID) -> None:
        """Delete a provider record permanently.

        This operation does not perform cascading business cleanup. If other
        records still reference the provider, persistence-level constraints
        may reject the delete and surface through domain exception handling.
        """
        deleted = await self._providers.delete_provider(provider_id)
        if not deleted:
            raise ResourceNotFoundError("Provider", str(provider_id))
