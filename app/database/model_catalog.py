"""
ModelCatalogPersistence
-----------------------
PostgreSQL CRUD for the `model_catalog` table.

model_catalog answers "what models exist for a provider?" It carries global
metadata about a model's capabilities and pricing — not tenant-specific routing
details. Those live in tenant_deployments.

Uniqueness is enforced at the DB layer via:
  UNIQUE (provider_id, model_name, COALESCE(model_version, ''))

A model without a version (model_version IS NULL) and a model with an explicit
version ('2024-08') are different rows. Queries that do not care about version
should use LIST_ACTIVE_MODELS_BY_PROVIDER_SQL which returns all versions.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from app.database.base import BasePersistence
from app.database.queries.model_catalog_queries import (
    CHECK_MODEL_EXISTS_BY_ID_SQL,
    CHECK_MODEL_EXISTS_BY_NAME_SQL,
    COUNT_ACTIVE_MODELS_BY_PROVIDER_SQL,
    COUNT_MODELS_BY_PROVIDER_SQL,
    CREATE_MODEL_SQL,
    DELETE_MODEL_BY_ID_SQL,
    GET_MODEL_BY_ID_SQL,
    GET_MODEL_BY_NAME_SQL,
    GET_MODEL_BY_PROVIDER_AND_ID_SQL,
    LIST_ACTIVE_MODELS_BY_PROVIDER_SQL,
    LIST_MODELS_BY_OPERATION_SQL,
    LIST_MODELS_BY_PROVIDER_SQL,
)
from app.database.session import DatabaseSessionManager

if TYPE_CHECKING:
    from uuid import UUID

logger = logging.getLogger(__name__)

_VALID_MODEL_STATUSES: list[str] = ["active", "deprecated", "retired"]

# Numeric constraints matching the DB CHECK constraints
_TEMPERATURE_MIN = Decimal("0.00")
_TEMPERATURE_MAX = Decimal("2.00")
_TOP_P_MIN = Decimal("0.000")
_TOP_P_MAX = Decimal("1.000")


class ModelCatalogPersistence(BasePersistence):
    """Persistence for the global model catalog.

    All write operations validate against provider_catalog (provider must exist)
    before touching model_catalog, producing a clear ValueError rather than a
    FK constraint violation.
    """

    def __init__(self, database_manager: DatabaseSessionManager | None = None) -> None:
        super().__init__(database_manager)

    # =========================================================================
    # VALIDATION HELPERS
    # =========================================================================

    def _validate_temperature(self, temperature: float | Decimal, param_name: str) -> None:
        val = Decimal(str(temperature))
        if val < _TEMPERATURE_MIN or val > _TEMPERATURE_MAX:
            raise ValueError(
                f"{param_name} must be between {_TEMPERATURE_MIN} and {_TEMPERATURE_MAX}, got {val}"
            )

    def _validate_top_p(self, top_p: float | Decimal, param_name: str) -> None:
        val = Decimal(str(top_p))
        if val < _TOP_P_MIN or val > _TOP_P_MAX:
            raise ValueError(
                f"{param_name} must be between {_TOP_P_MIN} and {_TOP_P_MAX}, got {val}"
            )

    async def model_exists_by_id(self, provider_id: UUID, model_id: UUID) -> bool:
        """Return True if (provider_id, model_id) exists in model_catalog."""
        async with self.get_session() as session:
            result = await session.execute(
                text(CHECK_MODEL_EXISTS_BY_ID_SQL),
                {"provider_id": str(provider_id), "model_id": str(model_id)},
            )
            return result.first() is not None

    async def model_exists_by_name(
        self, provider_id: UUID, model_name: str, model_version: str | None = None
    ) -> bool:
        """Return True if a matching (provider, name, version) row exists."""
        async with self.get_session() as session:
            result = await session.execute(
                text(CHECK_MODEL_EXISTS_BY_NAME_SQL),
                {
                    "provider_id": str(provider_id),
                    "model_name": model_name,
                    "model_version": model_version,
                },
            )
            return result.first() is not None

    # =========================================================================
    # CREATE
    # =========================================================================

    async def create_model(
        self,
        provider_id: UUID,
        model_name: str,
        supported_operations: list[str],
        model_version: str | None = None,
        display_name: str | None = None,
        context_window_tokens: int | None = None,
        max_output_tokens: int | None = None,
        default_temperature: float = 0.70,
        default_top_p: float = 1.000,
        pricing_metadata: dict[str, Any] | None = None,
        model_metadata: dict[str, Any] | None = None,
        status: str = "active",
    ) -> dict[str, Any]:
        """Register a new model in the catalog.

        Args:
            provider_id: UUID of the owning provider (must exist in provider_catalog).
            model_name: Canonical model identifier, e.g. 'gpt-4o'.
            supported_operations: Non-empty list, e.g. ['chat', 'embed'].
            model_version: Optional version string, e.g. '2024-08'.
            display_name: Optional human-readable label.
            context_window_tokens: Max context window size (positive int or None).
            max_output_tokens: Max tokens the model can generate (positive int or None).
            default_temperature: Default sampling temperature [0.00, 2.00].
            default_top_p: Default nucleus sampling probability [0.000, 1.000].
            pricing_metadata: Optional JSONB pricing facts.
            model_metadata: Optional JSONB catch-all metadata.
            status: Initial lifecycle status (default 'active').

        Returns:
            Created model row dict.

        Raises:
            ValueError: On validation failure or duplicate (provider, name, version).
        """
        self.validate_uuid(provider_id, "provider_id")
        self.validate_string_not_empty(model_name, "model_name")
        self.validate_enum_value(status, _VALID_MODEL_STATUSES, "status")
        self._validate_temperature(default_temperature, "default_temperature")
        self._validate_top_p(default_top_p, "default_top_p")

        if not supported_operations:
            raise ValueError("supported_operations must contain at least one operation")
        if context_window_tokens is not None:
            self.validate_positive_integer(context_window_tokens, "context_window_tokens")
        if max_output_tokens is not None:
            self.validate_positive_integer(max_output_tokens, "max_output_tokens")

        pricing_json = self._validate_and_serialize_json(pricing_metadata, "pricing_metadata")
        model_meta_json = self._validate_and_serialize_json(model_metadata, "model_metadata")

        if await self.model_exists_by_name(provider_id, model_name, model_version):
            version_label = f"version '{model_version}'" if model_version else "no version"
            raise ValueError(
                f"Model '{model_name}' ({version_label}) already exists for provider '{provider_id}'"
            )

        params = {
            "provider_id": str(provider_id),
            "model_name": model_name,
            "model_version": model_version,
            "display_name": display_name,
            "supported_operations": supported_operations,
            "context_window_tokens": context_window_tokens,
            "max_output_tokens": max_output_tokens,
            "default_temperature": str(default_temperature),
            "default_top_p": str(default_top_p),
            "pricing_metadata": pricing_json or "{}",
            "model_metadata": model_meta_json or "{}",
            "status": status,
        }

        try:
            async with self.get_session() as session:
                result = await session.execute(text(CREATE_MODEL_SQL), params)
                row = result.mappings().one_or_none()
                if not row:
                    raise RuntimeError("INSERT returned no row")
                logger.info(
                    "ModelCatalogPersistence: created model — name=%s version=%s provider=%s",
                    model_name,
                    model_version,
                    provider_id,
                )
                return dict(row)
        except (ValueError, RuntimeError):
            raise
        except Exception:
            logger.error(
                "ModelCatalogPersistence: create_model failed — name=%s", model_name, exc_info=True
            )
            raise

    # =========================================================================
    # READ
    # =========================================================================

    async def get_model_by_id(self, model_id: UUID | str) -> dict[str, Any] | None:
        """Return a model by its UUID alone (no provider scoping)."""
        self.validate_uuid(model_id, "model_id")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(GET_MODEL_BY_ID_SQL), {"model_id": str(model_id)}
                )
                row = result.mappings().one_or_none()
                return dict(row) if row else None
        except Exception:
            logger.error(
                "ModelCatalogPersistence: get_model_by_id failed — id=%s", model_id, exc_info=True
            )
            raise

    async def get_model_by_provider_and_id(
        self, provider_id: UUID, model_id: UUID
    ) -> dict[str, Any] | None:
        """Return a model scoped to a specific provider UUID."""
        self.validate_uuid(provider_id, "provider_id")
        self.validate_uuid(model_id, "model_id")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(GET_MODEL_BY_PROVIDER_AND_ID_SQL),
                    {"provider_id": str(provider_id), "model_id": str(model_id)},
                )
                row = result.mappings().one_or_none()
                return dict(row) if row else None
        except Exception:
            logger.error(
                "ModelCatalogPersistence: get_model_by_provider_and_id failed", exc_info=True
            )
            raise

    async def get_model_by_name(
        self,
        provider_id: UUID,
        model_name: str,
        model_version: str | None = None,
    ) -> dict[str, Any] | None:
        """Return a model by provider + name + optional version."""
        self.validate_uuid(provider_id, "provider_id")
        self.validate_string_not_empty(model_name, "model_name")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(GET_MODEL_BY_NAME_SQL),
                    {
                        "provider_id": str(provider_id),
                        "model_name": model_name,
                        "model_version": model_version,
                    },
                )
                row = result.mappings().one_or_none()
                return dict(row) if row else None
        except Exception:
            logger.error(
                "ModelCatalogPersistence: get_model_by_name failed — name=%s",
                model_name,
                exc_info=True,
            )
            raise

    async def list_models_by_provider(
        self,
        provider_id: UUID,
        active_only: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return models for a provider, optionally filtered to active only."""
        self.validate_uuid(provider_id, "provider_id")
        self.validate_pagination_parameters(limit, offset)
        sql = LIST_ACTIVE_MODELS_BY_PROVIDER_SQL if active_only else LIST_MODELS_BY_PROVIDER_SQL
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(sql),
                    {"provider_id": str(provider_id), "limit": limit, "offset": offset},
                )
                return [dict(row) for row in result.mappings().all()]
        except Exception:
            logger.error(
                "ModelCatalogPersistence: list_models_by_provider failed — provider=%s",
                provider_id,
                exc_info=True,
            )
            raise

    async def list_models_by_operation(
        self, operation: str, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Return all active models that support the given operation."""
        self.validate_string_not_empty(operation, "operation")
        self.validate_pagination_parameters(limit, offset)
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(LIST_MODELS_BY_OPERATION_SQL),
                    {"operation": operation, "limit": limit, "offset": offset},
                )
                return [dict(row) for row in result.mappings().all()]
        except Exception:
            logger.error(
                "ModelCatalogPersistence: list_models_by_operation failed — op=%s",
                operation,
                exc_info=True,
            )
            raise

    async def count_models_by_provider(self, provider_id: UUID, active_only: bool = True) -> int:
        """Return the model count for a provider."""
        self.validate_uuid(provider_id, "provider_id")
        sql = COUNT_ACTIVE_MODELS_BY_PROVIDER_SQL if active_only else COUNT_MODELS_BY_PROVIDER_SQL
        try:
            async with self.get_session() as session:
                result = await session.execute(text(sql), {"provider_id": str(provider_id)})
                return result.scalar_one_or_none() or 0
        except Exception:
            logger.error("ModelCatalogPersistence: count_models_by_provider failed", exc_info=True)
            raise

    # =========================================================================
    # UPDATE
    # =========================================================================

    async def update_model(
        self,
        provider_id: UUID,
        model_id: UUID,
        display_name: str | None = None,
        status: str | None = None,
        context_window_tokens: int | None = None,
        max_output_tokens: int | None = None,
        default_temperature: float | None = None,
        default_top_p: float | None = None,
        pricing_metadata: dict[str, Any] | None = None,
        model_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Partially update a model record. Returns updated row or None."""
        self.validate_uuid(provider_id, "provider_id")
        self.validate_uuid(model_id, "model_id")

        update_fields: dict[str, Any] = {}
        if display_name is not None:
            update_fields["display_name"] = display_name
        if status is not None:
            self.validate_enum_value(status, _VALID_MODEL_STATUSES, "status")
            update_fields["status"] = status
        if context_window_tokens is not None:
            self.validate_positive_integer(context_window_tokens, "context_window_tokens")
            update_fields["context_window_tokens"] = context_window_tokens
        if max_output_tokens is not None:
            self.validate_positive_integer(max_output_tokens, "max_output_tokens")
            update_fields["max_output_tokens"] = max_output_tokens
        if default_temperature is not None:
            self._validate_temperature(default_temperature, "default_temperature")
            update_fields["default_temperature"] = str(default_temperature)
        if default_top_p is not None:
            self._validate_top_p(default_top_p, "default_top_p")
            update_fields["default_top_p"] = str(default_top_p)
        if pricing_metadata is not None:
            update_fields["pricing_metadata"] = self._validate_and_serialize_json(
                pricing_metadata, "pricing_metadata"
            )
        if model_metadata is not None:
            update_fields["model_metadata"] = self._validate_and_serialize_json(
                model_metadata, "model_metadata"
            )

        if not update_fields:
            return await self.get_model_by_provider_and_id(provider_id, model_id)

        sql, params = self.build_dynamic_update_query(
            table_name="model_catalog",
            update_fields=update_fields,
            where_clause="provider_id = :provider_id AND model_id = :model_id",
            where_parameters={
                "provider_id": str(provider_id),
                "model_id": str(model_id),
            },
        )

        try:
            async with self.get_session() as session:
                result = await session.execute(text(sql), params)
                row = result.mappings().one_or_none()
                if row:
                    self.log_operation("UPDATE", model_id)
                    return dict(row)
                return None
        except Exception:
            logger.error(
                "ModelCatalogPersistence: update_model failed — id=%s", model_id, exc_info=True
            )
            raise

    async def deprecate_model(self, provider_id: UUID, model_id: UUID) -> dict[str, Any] | None:
        """Set model status to 'deprecated'."""
        return await self.update_model(
            provider_id=provider_id, model_id=model_id, status="deprecated"
        )

    async def retire_model(self, provider_id: UUID, model_id: UUID) -> dict[str, Any] | None:
        """Set model status to 'retired'."""
        return await self.update_model(provider_id=provider_id, model_id=model_id, status="retired")

    # =========================================================================
    # DELETE
    # =========================================================================

    async def delete_model(self, provider_id: UUID, model_id: UUID) -> bool:
        """Delete a model from the catalog.

        The DB enforces ON DELETE RESTRICT from tenant_deployments and
        user_entitlements, so this fails if active deployments reference the model.

        Returns:
            True if deleted; False if not found.
        """
        self.validate_uuid(provider_id, "provider_id")
        self.validate_uuid(model_id, "model_id")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(DELETE_MODEL_BY_ID_SQL),
                    {"provider_id": str(provider_id), "model_id": str(model_id)},
                )
                deleted = getattr(result, "rowcount", 0) > 0
                if deleted:
                    self.log_operation("DELETE", model_id)
                return bool(deleted)
        except Exception:
            logger.error(
                "ModelCatalogPersistence: delete_model failed — id=%s", model_id, exc_info=True
            )
            raise
