"""
UserEntitlementPersistence
--------------------------
PostgreSQL CRUD for the `user_entitlements` table.

Schema differs substantially from the legacy token-manager pattern:
  - entitlement_id is UUID (not SERIAL INTEGER)
  - tenant-scoped (tenant_id required)
  - references provider_catalog and model_catalog by UUID (not by text name)
  - secret_reference is a pointer into the secret store — never the raw credential
  - cloud_region replaces deployment_region
  - provider_deployment_name replaces deployment_name

Validation chain for create_entitlement:
  1. Input types and non-empty strings
  2. tenant_id exists in tenants
  3. user_id exists in users
  4. provider_id exists in provider_catalog (and is active)
  5. (provider_id, model_id) exists in model_catalog (and is active)
  6. (tenant_id, deployment_key) exists in tenant_deployments
  7. No active duplicate: (tenant_id, user_id, deployment_key, provider_id, model_id)
  8. entitlement_name is unique per (tenant_id, user_id)

Steps 2-8 are async pre-flight checks that run before the INSERT session opens,
giving callers a clear ValueError instead of a database-level constraint violation.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import text

from app.database.base import BasePersistence
from app.database.queries.user_entitlement_queries import (
    CHECK_ENTITLEMENT_EXISTS_SQL,
    CHECK_ENTITLEMENT_NAME_EXISTS_SQL,
    CHECK_MODEL_EXISTS_FOR_ENTITLEMENT_SQL,
    CHECK_PROVIDER_EXISTS_FOR_ENTITLEMENT_SQL,
    CHECK_TENANT_DEPLOYMENT_EXISTS_SQL,
    CHECK_TENANT_EXISTS_FOR_ENTITLEMENT_SQL,
    CHECK_USER_EXISTS_FOR_ENTITLEMENT_SQL,
    COUNT_TENANT_ENTITLEMENTS_SQL,
    COUNT_USER_ENTITLEMENTS_SQL,
    CREATE_USER_ENTITLEMENT_SQL,
    DELETE_ENTITLEMENT_BY_ID_SQL,
    GET_ACTIVE_ENTITLEMENT_FOR_ROUTE_SQL,
    GET_ENTITLEMENT_BY_ID_SQL,
    GET_ENTITLEMENT_SECRET_REFERENCE_SQL,
    LIST_TENANT_ENTITLEMENTS_SQL,
    LIST_USER_ENTITLEMENTS_SQL,
    REVOKE_USER_ENTITLEMENTS_SQL,
)
from app.database.session import DatabaseSessionManager

if TYPE_CHECKING:
    from uuid import UUID

logger = logging.getLogger(__name__)


class UserEntitlementPersistence(BasePersistence):
    """Persistence for user-scoped LLM routing overrides (user_entitlements).

    Every entitlement is tenant-scoped, tied to an approved tenant deployment,
    and references provider/model via UUID foreign keys from the global catalog.

    secret_reference is stored but never returned by any public read method.
    Callers that legitimately need it (the routing layer) must call
    get_entitlement_secret_reference() explicitly, making accidental exposure
    an explicit code decision rather than a default behaviour.
    """

    VALID_ENTITLEMENT_STATUSES: ClassVar[list[str]] = ["active", "inactive", "revoked"]

    def __init__(self, database_manager: DatabaseSessionManager | None = None) -> None:
        super().__init__(database_manager)

    # =========================================================================
    # PRE-FLIGHT VALIDATION HELPERS
    # =========================================================================

    async def _tenant_exists(self, tenant_id: UUID) -> bool:
        async with self.get_session() as session:
            result = await session.execute(
                text(CHECK_TENANT_EXISTS_FOR_ENTITLEMENT_SQL),
                {"tenant_id": str(tenant_id)},
            )
            return result.first() is not None

    async def _user_exists(self, user_id: UUID) -> bool:
        async with self.get_session() as session:
            result = await session.execute(
                text(CHECK_USER_EXISTS_FOR_ENTITLEMENT_SQL),
                {"user_id": str(user_id)},
            )
            return result.first() is not None

    async def _provider_exists(self, provider_id: UUID) -> bool:
        async with self.get_session() as session:
            result = await session.execute(
                text(CHECK_PROVIDER_EXISTS_FOR_ENTITLEMENT_SQL),
                {"provider_id": str(provider_id)},
            )
            return result.first() is not None

    async def _model_exists(self, provider_id: UUID, model_id: UUID) -> bool:
        async with self.get_session() as session:
            result = await session.execute(
                text(CHECK_MODEL_EXISTS_FOR_ENTITLEMENT_SQL),
                {"provider_id": str(provider_id), "model_id": str(model_id)},
            )
            return result.first() is not None

    async def _tenant_deployment_exists(self, tenant_id: UUID, deployment_key: str) -> bool:
        async with self.get_session() as session:
            result = await session.execute(
                text(CHECK_TENANT_DEPLOYMENT_EXISTS_SQL),
                {"tenant_id": str(tenant_id), "deployment_key": deployment_key},
            )
            return result.first() is not None

    async def _active_entitlement_exists(
        self,
        tenant_id: UUID,
        user_id: UUID,
        deployment_key: str,
        provider_id: UUID,
        model_id: UUID,
    ) -> bool:
        async with self.get_session() as session:
            result = await session.execute(
                text(CHECK_ENTITLEMENT_EXISTS_SQL),
                {
                    "tenant_id": str(tenant_id),
                    "user_id": str(user_id),
                    "deployment_key": deployment_key,
                    "provider_id": str(provider_id),
                    "model_id": str(model_id),
                },
            )
            return result.first() is not None

    async def _entitlement_name_taken(
        self, tenant_id: UUID, user_id: UUID, entitlement_name: str
    ) -> bool:
        async with self.get_session() as session:
            result = await session.execute(
                text(CHECK_ENTITLEMENT_NAME_EXISTS_SQL),
                {
                    "tenant_id": str(tenant_id),
                    "user_id": str(user_id),
                    "entitlement_name": entitlement_name,
                },
            )
            return result.first() is not None

    # =========================================================================
    # CREATE
    # =========================================================================

    async def create_entitlement(
        self,
        tenant_id: UUID,
        user_id: UUID,
        deployment_key: str,
        provider_id: UUID,
        model_id: UUID,
        entitlement_name: str,
        api_endpoint_url: str,
        secret_reference: str,
        created_by_user_id: UUID,
        status: str = "active",
        cloud_provider: str | None = None,
        cloud_region: str | None = None,
        provider_deployment_name: str | None = None,
        extra_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new user entitlement after a full pre-flight validation chain.

        Args:
            tenant_id: Owning tenant.
            user_id: User receiving the entitlement.
            deployment_key: Tenant deployment route this entitlement overrides.
            provider_id: UUID from provider_catalog.
            model_id: UUID from model_catalog (paired with provider_id).
            entitlement_name: Human-readable, unique per (tenant, user).
            api_endpoint_url: Provider API endpoint for this override.
            secret_reference: Secret store pointer for the user-owned credential.
            created_by_user_id: Admin who issued this entitlement.
            status: Initial status (default 'active').
            cloud_provider: Optional cloud platform name.
            cloud_region: Optional geographic region.
            provider_deployment_name: Optional provider-side deployment label.
            extra_config: Optional JSONB override settings.

        Returns:
            Row dict (without secret_reference).

        Raises:
            ValueError: On any validation failure.
            sqlalchemy.exc.SQLAlchemyError: On database errors.
        """
        # ── Type/format validation ──────────────────────────────────────────
        self.validate_uuid(tenant_id, "tenant_id")
        self.validate_uuid(user_id, "user_id")
        self.validate_uuid(provider_id, "provider_id")
        self.validate_uuid(model_id, "model_id")
        self.validate_uuid(created_by_user_id, "created_by_user_id")
        self.validate_string_not_empty(deployment_key, "deployment_key")
        self.validate_string_not_empty(entitlement_name, "entitlement_name")
        self.validate_string_not_empty(api_endpoint_url, "api_endpoint_url")
        self.validate_string_not_empty(secret_reference, "secret_reference")
        self.validate_enum_value(status, self.VALID_ENTITLEMENT_STATUSES, "status")

        extra_config_json = self._validate_and_serialize_json(extra_config, "extra_config")

        # ── Pre-flight database checks ──────────────────────────────────────
        if not await self._tenant_exists(tenant_id):
            raise ValueError(f"Tenant '{tenant_id}' does not exist")

        if not await self._user_exists(user_id):
            raise ValueError(f"User '{user_id}' does not exist")

        if not await self._provider_exists(provider_id):
            raise ValueError(f"Provider '{provider_id}' does not exist or is not active")

        if not await self._model_exists(provider_id, model_id):
            raise ValueError(
                f"Model '{model_id}' for provider '{provider_id}' does not exist or is not active"
            )

        if not await self._tenant_deployment_exists(tenant_id, deployment_key):
            raise ValueError(
                f"Deployment key '{deployment_key}' does not exist for tenant '{tenant_id}'. "
                "The entitlement must reference an existing tenant deployment."
            )

        if await self._active_entitlement_exists(
            tenant_id, user_id, deployment_key, provider_id, model_id
        ):
            raise ValueError(
                f"An active entitlement already exists for user '{user_id}' "
                f"on deployment '{deployment_key}' with provider/model "
                f"'{provider_id}/{model_id}'"
            )

        if await self._entitlement_name_taken(tenant_id, user_id, entitlement_name):
            raise ValueError(
                f"Entitlement name '{entitlement_name}' is already used by user '{user_id}' "
                f"in tenant '{tenant_id}'"
            )

        # ── Insert ───────────────────────────────────────────────────────────
        now = datetime.now(UTC)
        params = {
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "deployment_key": deployment_key,
            "provider_id": str(provider_id),
            "model_id": str(model_id),
            "entitlement_name": entitlement_name,
            "status": status,
            "api_endpoint_url": api_endpoint_url,
            "secret_reference": secret_reference,
            "cloud_provider": cloud_provider,
            "cloud_region": cloud_region,
            "provider_deployment_name": provider_deployment_name,
            "extra_config": extra_config_json or "{}",
            "created_by_user_id": str(created_by_user_id),
            "created_at": now,
            "updated_at": now,
        }

        try:
            async with self.get_session() as session:
                result = await session.execute(text(CREATE_USER_ENTITLEMENT_SQL), params)
                created_row = result.mappings().one_or_none()
                if not created_row:
                    raise RuntimeError("INSERT returned no row — this should not happen")
                logger.info(
                    "UserEntitlementPersistence: created entitlement — "
                    "entitlement_id=%s user_id=%s tenant_id=%s",
                    created_row["entitlement_id"],
                    user_id,
                    tenant_id,
                )
                return dict(created_row)
        except (ValueError, RuntimeError):
            raise
        except Exception:
            logger.error(
                "UserEntitlementPersistence: create_entitlement failed — user_id=%s",
                user_id,
                exc_info=True,
            )
            raise

    # =========================================================================
    # READ
    # =========================================================================

    async def get_entitlement_by_id(self, entitlement_id: UUID | str) -> dict[str, Any] | None:
        """Return a single entitlement by its UUID, or None if not found.

        secret_reference is excluded from the returned dict.
        """
        self.validate_uuid(entitlement_id, "entitlement_id")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(GET_ENTITLEMENT_BY_ID_SQL),
                    {"entitlement_id": str(entitlement_id)},
                )
                row = result.mappings().one_or_none()
                return dict(row) if row else None
        except Exception:
            logger.error(
                "UserEntitlementPersistence: get_entitlement_by_id failed — id=%s",
                entitlement_id,
                exc_info=True,
            )
            raise

    async def get_entitlement_secret_reference(self, entitlement_id: UUID | str) -> str | None:
        """Return ONLY the secret_reference for use by the routing layer.

        This method is intentionally separate from the normal read path to
        make credential access a deliberate, grep-visible operation.

        Returns:
            secret_reference string, or None if the entitlement does not exist.
        """
        self.validate_uuid(entitlement_id, "entitlement_id")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(GET_ENTITLEMENT_SECRET_REFERENCE_SQL),
                    {"entitlement_id": str(entitlement_id)},
                )
                row = result.one_or_none()
                return row[0] if row else None
        except Exception:
            logger.error(
                "UserEntitlementPersistence: get_entitlement_secret_reference failed — id=%s",
                entitlement_id,
                exc_info=True,
            )
            raise

    async def get_active_entitlement_for_route(
        self,
        tenant_id: UUID,
        user_id: UUID,
        deployment_key: str,
        provider_id: UUID,
        model_id: UUID,
    ) -> dict[str, Any] | None:
        """Return the active entitlement for a specific routing combination.

        Used by the routing layer to check whether a user has a personal
        override for a given (tenant, deployment, provider, model) path.
        Returns None when no override exists and tenant deployment routing applies.
        """
        self.validate_uuid(tenant_id, "tenant_id")
        self.validate_uuid(user_id, "user_id")
        self.validate_uuid(provider_id, "provider_id")
        self.validate_uuid(model_id, "model_id")
        self.validate_string_not_empty(deployment_key, "deployment_key")

        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(GET_ACTIVE_ENTITLEMENT_FOR_ROUTE_SQL),
                    {
                        "tenant_id": str(tenant_id),
                        "user_id": str(user_id),
                        "deployment_key": deployment_key,
                        "provider_id": str(provider_id),
                        "model_id": str(model_id),
                    },
                )
                row = result.mappings().one_or_none()
                return dict(row) if row else None
        except Exception:
            logger.error(
                "UserEntitlementPersistence: get_active_entitlement_for_route failed",
                exc_info=True,
            )
            raise

    async def get_user_entitlements(
        self,
        tenant_id: UUID,
        user_id: UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return all entitlements for a user within a tenant, paginated.

        Args:
            tenant_id: Scoping tenant.
            user_id: Target user.
            limit: Max rows (1-1000).
            offset: Rows to skip.

        Returns:
            List of entitlement row dicts ordered by created_at DESC.
        """
        self.validate_uuid(tenant_id, "tenant_id")
        self.validate_uuid(user_id, "user_id")
        self.validate_pagination_parameters(limit, offset)

        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(LIST_USER_ENTITLEMENTS_SQL),
                    {
                        "tenant_id": str(tenant_id),
                        "user_id": str(user_id),
                        "limit": limit,
                        "offset": offset,
                    },
                )
                rows = result.mappings().all()
                logger.debug(
                    "UserEntitlementPersistence: get_user_entitlements returned %d rows",
                    len(rows),
                )
                return [dict(row) for row in rows]
        except Exception:
            logger.error(
                "UserEntitlementPersistence: get_user_entitlements failed — user_id=%s",
                user_id,
                exc_info=True,
            )
            raise

    async def get_tenant_entitlements(
        self,
        tenant_id: UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return all entitlements within a tenant (all users), paginated."""
        self.validate_uuid(tenant_id, "tenant_id")
        self.validate_pagination_parameters(limit, offset)

        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(LIST_TENANT_ENTITLEMENTS_SQL),
                    {"tenant_id": str(tenant_id), "limit": limit, "offset": offset},
                )
                return [dict(row) for row in result.mappings().all()]
        except Exception:
            logger.error(
                "UserEntitlementPersistence: get_tenant_entitlements failed — tenant_id=%s",
                tenant_id,
                exc_info=True,
            )
            raise

    async def count_user_entitlements(self, tenant_id: UUID, user_id: UUID) -> int:
        """Return the total number of entitlements for a user within a tenant."""
        self.validate_uuid(tenant_id, "tenant_id")
        self.validate_uuid(user_id, "user_id")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(COUNT_USER_ENTITLEMENTS_SQL),
                    {"tenant_id": str(tenant_id), "user_id": str(user_id)},
                )
                return result.scalar_one_or_none() or 0
        except Exception:
            logger.error(
                "UserEntitlementPersistence: count_user_entitlements failed — user_id=%s",
                user_id,
                exc_info=True,
            )
            raise

    async def count_tenant_entitlements(self, tenant_id: UUID) -> int:
        """Return the total number of entitlements within a tenant."""
        self.validate_uuid(tenant_id, "tenant_id")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(COUNT_TENANT_ENTITLEMENTS_SQL),
                    {"tenant_id": str(tenant_id)},
                )
                return result.scalar_one_or_none() or 0
        except Exception:
            logger.error(
                "UserEntitlementPersistence: count_tenant_entitlements failed — tenant_id=%s",
                tenant_id,
                exc_info=True,
            )
            raise

    # =========================================================================
    # UPDATE
    # =========================================================================

    async def update_entitlement(
        self,
        entitlement_id: UUID,
        api_endpoint_url: str | None = None,
        secret_reference: str | None = None,
        status: str | None = None,
        cloud_provider: str | None = None,
        cloud_region: str | None = None,
        provider_deployment_name: str | None = None,
        extra_config: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Partially update an entitlement. Only non-None fields are written.

        Returns:
            Updated row dict (without secret_reference) or None if not found.
        """
        self.validate_uuid(entitlement_id, "entitlement_id")
        if status is not None:
            self.validate_enum_value(status, self.VALID_ENTITLEMENT_STATUSES, "status")
        if api_endpoint_url is not None:
            self.validate_string_not_empty(api_endpoint_url, "api_endpoint_url")
        if secret_reference is not None:
            self.validate_string_not_empty(secret_reference, "secret_reference")

        update_fields: dict[str, Any] = {}
        if api_endpoint_url is not None:
            update_fields["api_endpoint_url"] = api_endpoint_url
        if secret_reference is not None:
            update_fields["secret_reference"] = secret_reference
        if status is not None:
            update_fields["status"] = status
        if cloud_provider is not None:
            update_fields["cloud_provider"] = cloud_provider
        if cloud_region is not None:
            update_fields["cloud_region"] = cloud_region
        if provider_deployment_name is not None:
            update_fields["provider_deployment_name"] = provider_deployment_name
        if extra_config is not None:
            update_fields["extra_config"] = self._validate_and_serialize_json(
                extra_config, "extra_config"
            )

        if not update_fields:
            logger.warning(
                "UserEntitlementPersistence: update_entitlement called with no fields — id=%s",
                entitlement_id,
            )
            return await self.get_entitlement_by_id(entitlement_id)

        # Build a custom RETURNING clause that excludes secret_reference.
        returning_cols = (
            "entitlement_id, tenant_id, user_id, deployment_key, "
            "provider_id, model_id, entitlement_name, status, api_endpoint_url, "
            "cloud_provider, cloud_region, provider_deployment_name, extra_config, "
            "created_by_user_id, created_at, updated_at"
        )
        set_clauses = ["updated_at = CURRENT_TIMESTAMP"]
        params: dict[str, Any] = {"entitlement_id": str(entitlement_id)}
        for field, value in update_fields.items():
            key = f"set_{field}"
            set_clauses.append(f"{field} = :{key}")
            params[key] = value

        sql = (
            f"UPDATE user_entitlements "
            f"SET {', '.join(set_clauses)} "
            f"WHERE entitlement_id = :entitlement_id "
            f"RETURNING {returning_cols}"
        )

        try:
            async with self.get_session() as session:
                result = await session.execute(text(sql), params)
                updated_row = result.mappings().one_or_none()
                if updated_row:
                    self.log_operation("UPDATE", entitlement_id)
                    return dict(updated_row)
                logger.warning(
                    "UserEntitlementPersistence: update_entitlement — not found — id=%s",
                    entitlement_id,
                )
                return None
        except Exception:
            logger.error(
                "UserEntitlementPersistence: update_entitlement failed — id=%s",
                entitlement_id,
                exc_info=True,
            )
            raise

    # =========================================================================
    # DELETE / REVOKE
    # =========================================================================

    async def delete_entitlement(self, entitlement_id: UUID | str) -> bool:
        """Permanently delete an entitlement row.

        Returns:
            True if the row existed and was deleted; False if not found.
        """
        self.validate_uuid(entitlement_id, "entitlement_id")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(DELETE_ENTITLEMENT_BY_ID_SQL),
                    {"entitlement_id": str(entitlement_id)},
                )
                deleted = getattr(result, "rowcount", 0) > 0
                if deleted:
                    self.log_operation("DELETE", entitlement_id)
                return bool(deleted)
        except Exception:
            logger.error(
                "UserEntitlementPersistence: delete_entitlement failed — id=%s",
                entitlement_id,
                exc_info=True,
            )
            raise

    async def revoke_all_user_entitlements(self, tenant_id: UUID, user_id: UUID) -> int:
        """Set all active entitlements for a user to 'revoked'.

        Used when a user is suspended or removed from a tenant.

        Returns:
            Number of rows updated.
        """
        self.validate_uuid(tenant_id, "tenant_id")
        self.validate_uuid(user_id, "user_id")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(REVOKE_USER_ENTITLEMENTS_SQL),
                    {"tenant_id": str(tenant_id), "user_id": str(user_id)},
                )
                count = getattr(result, "rowcount", 0)
                logger.info(
                    "UserEntitlementPersistence: revoked %d entitlements — user_id=%s tenant_id=%s",
                    count,
                    user_id,
                    tenant_id,
                )
                return count
        except Exception:
            logger.error(
                "UserEntitlementPersistence: revoke_all_user_entitlements failed — user_id=%s",
                user_id,
                exc_info=True,
            )
            raise
