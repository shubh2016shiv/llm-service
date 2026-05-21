"""
ProviderCatalogPersistence
--------------------------
PostgreSQL CRUD for the `provider_catalog` table.

provider_catalog is the global registry of LLM providers the platform
supports. It answers "what providers exist?" independent of any tenant
configuration. Tenant-specific routing lives in tenant_deployments.

supported_operations is a PostgreSQL TEXT[] array. asyncpg handles Python
lists ↔ PostgreSQL arrays transparently when the column is declared TEXT[].
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from app.database.base import BasePersistence
from app.database.queries.provider_catalog_queries import (
    CHECK_PROVIDER_EXISTS_BY_ID_SQL,
    CHECK_PROVIDER_EXISTS_BY_NAME_SQL,
    COUNT_ACTIVE_PROVIDERS_SQL,
    COUNT_ALL_PROVIDERS_SQL,
    CREATE_PROVIDER_SQL,
    DELETE_PROVIDER_BY_ID_SQL,
    GET_PROVIDER_BY_ID_SQL,
    GET_PROVIDER_BY_NAME_SQL,
    LIST_ACTIVE_PROVIDERS_SQL,
    LIST_ALL_PROVIDERS_SQL,
)
from app.database.session import DatabaseSessionManager

if TYPE_CHECKING:
    from uuid import UUID

logger = logging.getLogger(__name__)

_VALID_PROVIDER_TYPES: list[str] = ["direct_api", "cloud_api", "self_hosted", "gateway"]
_VALID_AUTH_MODES: list[str] = ["bearer_token", "api_key_header", "aws_sigv4", "oauth", "custom"]


class ProviderCatalogPersistence(BasePersistence):
    """Persistence for the global provider catalog.

    provider_name must match the regex '^[a-z][a-z0-9_]*$' (enforced by DB).
    supported_operations must be non-empty (enforced by DB).
    """

    def __init__(self, database_manager: DatabaseSessionManager | None = None) -> None:
        super().__init__(database_manager)

    # =========================================================================
    # VALIDATION HELPERS
    # =========================================================================

    async def provider_name_exists(self, provider_name: str) -> bool:
        """Return True if a provider with this name is already registered."""
        async with self.get_session() as session:
            result = await session.execute(
                text(CHECK_PROVIDER_EXISTS_BY_NAME_SQL),
                {"provider_name": provider_name},
            )
            return result.first() is not None

    async def provider_id_exists(self, provider_id: UUID) -> bool:
        """Return True if a provider with this UUID exists."""
        async with self.get_session() as session:
            result = await session.execute(
                text(CHECK_PROVIDER_EXISTS_BY_ID_SQL),
                {"provider_id": str(provider_id)},
            )
            return result.first() is not None

    # =========================================================================
    # CREATE
    # =========================================================================

    async def create_provider(
        self,
        provider_name: str,
        display_name: str,
        provider_type: str,
        auth_mode: str,
        supported_operations: list[str],
        default_api_endpoint_url: str | None = None,
        is_active: bool = True,
        provider_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Register a new provider in the catalog.

        Args:
            provider_name: Lowercase slug, e.g. 'openai'. Must match '^[a-z][a-z0-9_]*$'.
            display_name: Human-readable name, e.g. 'OpenAI'.
            provider_type: One of the valid provider_type values.
            auth_mode: One of the valid auth_mode values.
            supported_operations: Non-empty list of operation strings.
            default_api_endpoint_url: Optional base URL for this provider.
            is_active: Whether the provider is available for routing. Defaults to True.
            provider_metadata: Optional JSONB metadata blob.

        Returns:
            Created provider row dict.

        Raises:
            ValueError: On validation failure or duplicate provider_name.
        """
        self.validate_string_not_empty(provider_name, "provider_name")
        self.validate_string_not_empty(display_name, "display_name")
        self.validate_enum_value(provider_type, _VALID_PROVIDER_TYPES, "provider_type")
        self.validate_enum_value(auth_mode, _VALID_AUTH_MODES, "auth_mode")

        if not supported_operations:
            raise ValueError("supported_operations must contain at least one operation")

        metadata_json = self._validate_and_serialize_json(provider_metadata, "provider_metadata")

        if await self.provider_name_exists(provider_name):
            raise ValueError(f"Provider '{provider_name}' is already registered")

        params = {
            "provider_name": provider_name,
            "display_name": display_name,
            "provider_type": provider_type,
            "auth_mode": auth_mode,
            "default_api_endpoint_url": default_api_endpoint_url,
            "supported_operations": supported_operations,
            "is_active": is_active,
            "provider_metadata": metadata_json or "{}",
        }

        try:
            async with self.get_session() as session:
                result = await session.execute(text(CREATE_PROVIDER_SQL), params)
                row = result.mappings().one_or_none()
                if not row:
                    raise RuntimeError("INSERT returned no row")
                logger.info(
                    "ProviderCatalogPersistence: created provider — name=%s id=%s",
                    provider_name,
                    row["provider_id"],
                )
                return dict(row)
        except (ValueError, RuntimeError):
            raise
        except Exception:
            logger.error(
                "ProviderCatalogPersistence: create_provider failed — name=%s",
                provider_name,
                exc_info=True,
            )
            raise

    # =========================================================================
    # READ
    # =========================================================================

    async def get_provider_by_id(self, provider_id: UUID | str) -> dict[str, Any] | None:
        """Return a provider by UUID, or None if not found."""
        self.validate_uuid(provider_id, "provider_id")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(GET_PROVIDER_BY_ID_SQL), {"provider_id": str(provider_id)}
                )
                row = result.mappings().one_or_none()
                return dict(row) if row else None
        except Exception:
            logger.error(
                "ProviderCatalogPersistence: get_provider_by_id failed — id=%s",
                provider_id,
                exc_info=True,
            )
            raise

    async def get_provider_by_name(self, provider_name: str) -> dict[str, Any] | None:
        """Return a provider by its unique slug name, or None if not found."""
        self.validate_string_not_empty(provider_name, "provider_name")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(GET_PROVIDER_BY_NAME_SQL), {"provider_name": provider_name}
                )
                row = result.mappings().one_or_none()
                return dict(row) if row else None
        except Exception:
            logger.error(
                "ProviderCatalogPersistence: get_provider_by_name failed — name=%s",
                provider_name,
                exc_info=True,
            )
            raise

    async def list_active_providers(
        self, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Return all active providers, ordered by provider_name."""
        self.validate_pagination_parameters(limit, offset)
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(LIST_ACTIVE_PROVIDERS_SQL), {"limit": limit, "offset": offset}
                )
                return [dict(row) for row in result.mappings().all()]
        except Exception:
            logger.error("ProviderCatalogPersistence: list_active_providers failed", exc_info=True)
            raise

    async def list_all_providers(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """Return all providers including inactive ones, ordered by provider_name."""
        self.validate_pagination_parameters(limit, offset)
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(LIST_ALL_PROVIDERS_SQL), {"limit": limit, "offset": offset}
                )
                return [dict(row) for row in result.mappings().all()]
        except Exception:
            logger.error("ProviderCatalogPersistence: list_all_providers failed", exc_info=True)
            raise

    async def count_active_providers(self) -> int:
        """Return the count of currently active providers."""
        try:
            async with self.get_session() as session:
                result = await session.execute(text(COUNT_ACTIVE_PROVIDERS_SQL))
                return result.scalar_one_or_none() or 0
        except Exception:
            logger.error("ProviderCatalogPersistence: count_active_providers failed", exc_info=True)
            raise

    async def count_all_providers(self) -> int:
        """Return the count of all providers including inactive ones."""
        try:
            async with self.get_session() as session:
                result = await session.execute(text(COUNT_ALL_PROVIDERS_SQL))
                return result.scalar_one_or_none() or 0
        except Exception:
            logger.error("ProviderCatalogPersistence: count_all_providers failed", exc_info=True)
            raise

    # =========================================================================
    # UPDATE
    # =========================================================================

    async def update_provider(
        self,
        provider_id: UUID,
        display_name: str | None = None,
        default_api_endpoint_url: str | None = None,
        is_active: bool | None = None,
        supported_operations: list[str] | None = None,
        provider_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Partially update a provider record.

        Returns the updated row dict or None if not found.
        """
        self.validate_uuid(provider_id, "provider_id")

        update_fields: dict[str, Any] = {}
        if display_name is not None:
            self.validate_string_not_empty(display_name, "display_name")
            update_fields["display_name"] = display_name
        if default_api_endpoint_url is not None:
            update_fields["default_api_endpoint_url"] = default_api_endpoint_url
        if is_active is not None:
            update_fields["is_active"] = is_active
        if supported_operations is not None:
            if not supported_operations:
                raise ValueError("supported_operations must contain at least one operation")
            update_fields["supported_operations"] = supported_operations
        if provider_metadata is not None:
            update_fields["provider_metadata"] = self._validate_and_serialize_json(
                provider_metadata, "provider_metadata"
            )

        if not update_fields:
            return await self.get_provider_by_id(provider_id)

        sql, params = self.build_dynamic_update_query(
            table_name="provider_catalog",
            update_fields=update_fields,
            where_clause="provider_id = :provider_id",
            where_parameters={"provider_id": str(provider_id)},
        )

        try:
            async with self.get_session() as session:
                result = await session.execute(text(sql), params)
                row = result.mappings().one_or_none()
                if row:
                    self.log_operation("UPDATE", provider_id)
                    return dict(row)
                return None
        except Exception:
            logger.error(
                "ProviderCatalogPersistence: update_provider failed — id=%s",
                provider_id,
                exc_info=True,
            )
            raise

    async def deactivate_provider(self, provider_id: UUID) -> dict[str, Any] | None:
        """Set is_active=False for a provider."""
        return await self.update_provider(provider_id=provider_id, is_active=False)

    async def activate_provider(self, provider_id: UUID) -> dict[str, Any] | None:
        """Set is_active=True for a provider."""
        return await self.update_provider(provider_id=provider_id, is_active=True)

    # =========================================================================
    # DELETE
    # =========================================================================

    async def delete_provider(self, provider_id: UUID) -> bool:
        """Delete a provider from the catalog.

        The DB enforces ON DELETE RESTRICT from model_catalog and tenant_deployments,
        so this will fail if any models or deployments reference this provider.

        Returns:
            True if deleted; False if not found.
        """
        self.validate_uuid(provider_id, "provider_id")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(DELETE_PROVIDER_BY_ID_SQL), {"provider_id": str(provider_id)}
                )
                deleted = getattr(result, "rowcount", 0) > 0
                if deleted:
                    self.log_operation("DELETE", provider_id)
                return bool(deleted)
        except Exception:
            logger.error(
                "ProviderCatalogPersistence: delete_provider failed — id=%s",
                provider_id,
                exc_info=True,
            )
            raise
