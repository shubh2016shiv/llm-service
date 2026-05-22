"""
Model Catalog Service
=====================

Business service for managing AI model records under provider records.

What this service does:
    Every provider (OpenAI, Anthropic, and others) can expose multiple
    models. This service handles the model-side lifecycle: create, list,
    retrieve, update, and status transitions (activate/deprecate).

Responsibilities:
    - Validate create and update payloads through schema contracts.
    - Translate low-level persistence ``ValueError`` failures into typed
      domain exceptions via ``raise_clean_validation_error``.
    - Remove secret-bearing fields from every returned row.
    - Raise resource-specific not-found errors for missing model lookups.

Enterprise Pattern: CRUD Service Pattern
    API routes call service methods, and service methods coordinate
    validation, persistence, error translation, and response shaping.

Author: Shubham Singh
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.exceptions import ResourceNotFoundError
from app.schemas.management_schema import ModelCreateRequest, ModelUpdateRequest
from app.services.management_helpers import Row, clean_row, clean_rows, raise_clean_validation_error

if TYPE_CHECKING:
    from uuid import UUID

    from app.database import ModelCatalogPersistence


class ModelCatalogService:
    """Manage lifecycle operations for provider-scoped model records."""

    def __init__(self, model_persistence: ModelCatalogPersistence) -> None:
        """Initialize with a persistence adapter for model storage operations."""
        self._models = model_persistence

    async def create_model(self, provider_id: UUID, request: ModelCreateRequest) -> Row:
        """Create a model record under a specific provider.

        The provider identifier defines ownership scope. The request contains
        model metadata such as operation support, context limits, and default
        runtime parameters.
        """
        try:
            row = await self._models.create_model(provider_id=provider_id, **request.model_dump())
            return clean_row(row)
        except ValueError as exc:
            raise_clean_validation_error(exc)

    async def list_models(
        self, provider_id: UUID, active_only: bool, limit: int, offset: int
    ) -> list[Row]:
        """List models for one provider using offset pagination.

        Args:
            provider_id: Provider whose models should be returned.
            active_only: When ``True``, include only currently active models.
            limit: Maximum rows to return for this page.
            offset: Number of rows to skip from the full result set.
        """
        rows = await self._models.list_models_by_provider(provider_id, active_only, limit, offset)
        return clean_rows(rows)

    async def count_models(self, provider_id: UUID, active_only: bool) -> int:
        """Count provider models for pagination metadata.

        This method mirrors ``list_models`` filtering so API layers can return
        both page data and total item count from consistent criteria.
        """
        return await self._models.count_models_by_provider(provider_id, active_only)

    async def get_model(self, provider_id: UUID, model_id: UUID) -> Row:
        """Fetch one provider-scoped model by identifier.

        The lookup is scoped by ``provider_id`` to avoid cross-provider access
        mistakes where a model ID exists but belongs to a different provider.
        """
        row = await self._models.get_model_by_provider_and_id(provider_id, model_id)
        if row is None:
            raise ResourceNotFoundError("Model", str(model_id))
        return clean_row(row)

    async def update_model(
        self, provider_id: UUID, model_id: UUID, request: ModelUpdateRequest
    ) -> Row:
        """Partially update mutable model metadata.

        Only explicitly provided fields are forwarded to persistence via
        ``exclude_unset=True`` so omitted fields retain current values.
        """
        try:
            row = await self._models.update_model(
                provider_id=provider_id,
                model_id=model_id,
                **request.model_dump(exclude_unset=True),
            )
        except ValueError as exc:
            raise_clean_validation_error(exc)
        if row is None:
            raise ResourceNotFoundError("Model", str(model_id))
        return clean_row(row)

    async def activate_model(self, provider_id: UUID, model_id: UUID) -> Row:
        """Set model status to ``active`` so it is eligible for deployments.

        This convenience method keeps status transition semantics in one place
        by delegating to ``update_model``.
        """
        request = ModelUpdateRequest(status="active")
        return await self.update_model(provider_id, model_id, request)

    async def deactivate_model(self, provider_id: UUID, model_id: UUID) -> Row:
        """Set model status to a non-active state used for retirement.

        "Deactivation" in this codebase means preventing new deployment usage
        while preserving historical references and existing auditability.
        """
        row = await self._models.deprecate_model(provider_id, model_id)
        if row is None:
            raise ResourceNotFoundError("Model", str(model_id))
        return clean_row(row)
