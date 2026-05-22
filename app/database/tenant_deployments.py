"""
TenantDeploymentPersistence
---------------------------
PostgreSQL CRUD for the `tenant_deployments` table.

This is the central routing table of the LLM service. Every inference request
that reaches the routing layer resolves to a tenant_deployment row which
provides the endpoint URL, capacity limit, and secret reference needed to
execute the call.

Design notes:
  - secret_reference is stored but never returned by default read methods.
    Use get_deployment_secret_reference() when the routing layer legitimately
    needs the credential pointer — this keeps accidental exposure a deliberate
    choice, not a default.
  - The DB enforces UNIQUE (tenant_id, deployment_key) via constraint and
    UNIQUE (tenant_id, provider_id) WHERE is_default = TRUE via partial index.
    The persistence layer pre-checks both to give callers a clean ValueError
    rather than a DB-level IntegrityError.
  - token_capacity_limit and token_lock_duration_seconds feed the token manager
    capacity-check logic. They must be positive and present on every active row.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from app.database.base import BasePersistence
from app.database.queries.tenant_deployment_queries import (
    CHECK_DEFAULT_DEPLOYMENT_EXISTS_SQL,
    CHECK_DEPLOYMENT_KEY_EXISTS_SQL,
    CREATE_DEPLOYMENT_SQL,
    DELETE_DEPLOYMENT_BY_ID_SQL,
    DEPLOYMENT_SAFE_COLUMNS,
    GET_DEFAULT_DEPLOYMENT_SQL,
    GET_DEPLOYMENT_BY_ID_SQL,
    GET_DEPLOYMENT_BY_KEY_SQL,
    GET_DEPLOYMENT_FOR_ROUTING_BY_KEY_SQL,
    GET_DEPLOYMENT_SECRET_REFERENCE_SQL,
    LIST_ACTIVE_DEPLOYMENTS_BY_PROVIDER_AND_MODEL_SQL,
    build_tenant_deployment_count_query,
    build_tenant_deployment_list_query,
)
from app.database.session import DatabaseSessionManager
from app.schemas.management_filters import TenantDeploymentListFilters

if TYPE_CHECKING:
    from uuid import UUID

logger = logging.getLogger(__name__)

_VALID_STATUSES: list[str] = ["active", "inactive", "maintenance"]
_TEMPERATURE_MIN = Decimal("0.00")
_TEMPERATURE_MAX = Decimal("2.00")
_TOP_P_MIN = Decimal("0.000")
_TOP_P_MAX = Decimal("1.000")


class TenantDeploymentPersistence(BasePersistence):
    """Persistence for tenant-scoped LLM routing and capacity configuration."""

    def __init__(self, database_manager: DatabaseSessionManager | None = None) -> None:
        super().__init__(database_manager)

    # =========================================================================
    # VALIDATION HELPERS
    # =========================================================================

    def _validate_temperature(self, value: float, param_name: str) -> None:
        v = Decimal(str(value))
        if v < _TEMPERATURE_MIN or v > _TEMPERATURE_MAX:
            raise ValueError(
                f"{param_name} must be in [{_TEMPERATURE_MIN}, {_TEMPERATURE_MAX}], got {v}"
            )

    def _validate_top_p(self, value: float, param_name: str) -> None:
        v = Decimal(str(value))
        if v < _TOP_P_MIN or v > _TOP_P_MAX:
            raise ValueError(f"{param_name} must be in [{_TOP_P_MIN}, {_TOP_P_MAX}], got {v}")

    async def deployment_key_exists(self, tenant_id: UUID, deployment_key: str) -> bool:
        """Return True if this (tenant_id, deployment_key) is already taken."""
        async with self.get_session() as session:
            result = await session.execute(
                text(CHECK_DEPLOYMENT_KEY_EXISTS_SQL),
                {"tenant_id": str(tenant_id), "deployment_key": deployment_key},
            )
            return result.first() is not None

    async def default_deployment_exists(self, tenant_id: UUID, provider_id: UUID) -> bool:
        """Return True if the tenant already has a default deployment for this provider."""
        async with self.get_session() as session:
            result = await session.execute(
                text(CHECK_DEFAULT_DEPLOYMENT_EXISTS_SQL),
                {"tenant_id": str(tenant_id), "provider_id": str(provider_id)},
            )
            return result.first() is not None

    # =========================================================================
    # CREATE
    # =========================================================================

    async def create_deployment(
        self,
        tenant_id: UUID,
        provider_id: UUID,
        model_id: UUID,
        deployment_key: str,
        deployment_name: str,
        api_endpoint_url: str,
        secret_reference: str,
        token_capacity_limit: int,
        created_by_user_id: UUID,
        status: str = "active",
        cloud_provider: str | None = None,
        cloud_region: str | None = None,
        provider_deployment_name: str | None = None,
        token_lock_duration_seconds: int = 70,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        default_temperature: float = 0.70,
        default_top_p: float = 1.000,
        default_max_output_tokens: int | None = None,
        is_default: bool = False,
        routing_priority: int = 0,
        extra_headers: dict[str, Any] | None = None,
        extra_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new tenant deployment (routing record).

        Args:
            tenant_id: Owning tenant.
            provider_id: Provider UUID from provider_catalog.
            model_id: Model UUID from model_catalog (must pair with provider_id).
            deployment_key: URL-safe slug unique within the tenant, e.g. 'gpt4-prod'.
            deployment_name: Human-readable label.
            api_endpoint_url: Provider API base URL for this deployment.
            secret_reference: Secret store pointer for the provider credential.
            token_capacity_limit: Max concurrent reserved tokens for this endpoint.
            created_by_user_id: Admin who created this deployment.
            status: Operational status. Defaults to 'active'.
            cloud_provider: Optional cloud platform (aws, azure, gcp).
            cloud_region: Optional geographic region.
            provider_deployment_name: Optional provider-side deployment name.
            token_lock_duration_seconds: Seconds a token allocation is held. Default 70.
            timeout_seconds: Optional per-request timeout in seconds.
            max_retries: Optional retry limit.
            default_temperature: Default sampling temperature [0.00, 2.00].
            default_top_p: Default nucleus sampling probability [0.000, 1.000].
            default_max_output_tokens: Optional output token ceiling.
            is_default: Whether this is the default deployment for this provider.
            routing_priority: Higher value = preferred in multi-deployment routing.
            extra_headers: Optional JSONB headers forwarded to the provider.
            extra_config: Optional JSONB catch-all configuration.

        Returns:
            Created deployment row dict (without secret_reference).

        Raises:
            ValueError: On validation failure or duplicate deployment_key/default.
        """
        # ── Type/format validation ──────────────────────────────────────────
        self.validate_uuid(tenant_id, "tenant_id")
        self.validate_uuid(provider_id, "provider_id")
        self.validate_uuid(model_id, "model_id")
        self.validate_uuid(created_by_user_id, "created_by_user_id")
        self.validate_string_not_empty(deployment_key, "deployment_key")
        self.validate_string_not_empty(deployment_name, "deployment_name")
        self.validate_string_not_empty(api_endpoint_url, "api_endpoint_url")
        self.validate_string_not_empty(secret_reference, "secret_reference")
        self.validate_positive_integer(token_capacity_limit, "token_capacity_limit")
        self.validate_positive_integer(token_lock_duration_seconds, "token_lock_duration_seconds")
        self.validate_enum_value(status, _VALID_STATUSES, "status")
        self._validate_temperature(default_temperature, "default_temperature")
        self._validate_top_p(default_top_p, "default_top_p")
        self.validate_positive_integer(routing_priority, "routing_priority", allow_zero=True)

        if max_retries is not None:
            self.validate_positive_integer(max_retries, "max_retries", allow_zero=True)
        if default_max_output_tokens is not None:
            self.validate_positive_integer(default_max_output_tokens, "default_max_output_tokens")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError(f"timeout_seconds must be positive, got {timeout_seconds}")

        headers_json = self._validate_and_serialize_json(extra_headers, "extra_headers")
        config_json = self._validate_and_serialize_json(extra_config, "extra_config")

        # ── Uniqueness pre-checks ───────────────────────────────────────────
        if await self.deployment_key_exists(tenant_id, deployment_key):
            raise ValueError(
                f"Deployment key '{deployment_key}' already exists for tenant '{tenant_id}'"
            )

        if is_default and await self.default_deployment_exists(tenant_id, provider_id):
            raise ValueError(
                f"Tenant '{tenant_id}' already has a default deployment for provider '{provider_id}'. "
                "Clear is_default on the existing deployment before marking a new one as default."
            )

        # ── Insert ───────────────────────────────────────────────────────────
        params = {
            "tenant_id": str(tenant_id),
            "provider_id": str(provider_id),
            "model_id": str(model_id),
            "deployment_key": deployment_key,
            "deployment_name": deployment_name,
            "status": status,
            "api_endpoint_url": api_endpoint_url,
            "secret_reference": secret_reference,
            "cloud_provider": cloud_provider,
            "cloud_region": cloud_region,
            "provider_deployment_name": provider_deployment_name,
            "token_capacity_limit": token_capacity_limit,
            "token_lock_duration_seconds": token_lock_duration_seconds,
            "timeout_seconds": timeout_seconds,
            "max_retries": max_retries,
            "default_temperature": str(default_temperature),
            "default_top_p": str(default_top_p),
            "default_max_output_tokens": default_max_output_tokens,
            "is_default": is_default,
            "routing_priority": routing_priority,
            "extra_headers": headers_json or "{}",
            "extra_config": config_json or "{}",
            "created_by_user_id": str(created_by_user_id),
        }

        try:
            async with self.get_session() as session:
                result = await session.execute(text(CREATE_DEPLOYMENT_SQL), params)
                row = result.mappings().one_or_none()
                if not row:
                    raise RuntimeError("INSERT returned no row")
                logger.info(
                    "TenantDeploymentPersistence: created deployment — key=%s tenant=%s id=%s",
                    deployment_key,
                    tenant_id,
                    row["deployment_id"],
                )
                return dict(row)
        except (ValueError, RuntimeError):
            raise
        except Exception as exc:
            self.raise_for_foreign_key_violation(
                exc,
                {
                    "tenant_deployments_tenant_id_fkey": ("Tenant", str(tenant_id)),
                    "tenant_deployments_provider_id_fkey": ("Provider", str(provider_id)),
                    "tenant_deployments_model_id_fkey": ("Model", str(model_id)),
                    "tenant_deployments_created_by_user_id_fkey": (
                        "User",
                        str(created_by_user_id),
                    ),
                },
            )
            logger.error(
                "TenantDeploymentPersistence: create_deployment failed — key=%s tenant=%s",
                deployment_key,
                tenant_id,
                exc_info=True,
            )
            raise

    # =========================================================================
    # READ
    # =========================================================================

    async def get_deployment_by_id(self, deployment_id: UUID | str) -> dict[str, Any] | None:
        """Return a deployment by UUID (without secret_reference)."""
        self.validate_uuid(deployment_id, "deployment_id")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(GET_DEPLOYMENT_BY_ID_SQL), {"deployment_id": str(deployment_id)}
                )
                row = result.mappings().one_or_none()
                return dict(row) if row else None
        except Exception:
            logger.error(
                "TenantDeploymentPersistence: get_deployment_by_id failed — id=%s",
                deployment_id,
                exc_info=True,
            )
            raise

    async def get_deployment_by_key(
        self, tenant_id: UUID, deployment_key: str
    ) -> dict[str, Any] | None:
        """Return a deployment by its (tenant_id, deployment_key) route (without secret_reference)."""
        self.validate_uuid(tenant_id, "tenant_id")
        self.validate_string_not_empty(deployment_key, "deployment_key")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(GET_DEPLOYMENT_BY_KEY_SQL),
                    {"tenant_id": str(tenant_id), "deployment_key": deployment_key},
                )
                row = result.mappings().one_or_none()
                return dict(row) if row else None
        except Exception:
            logger.error(
                "TenantDeploymentPersistence: get_deployment_by_key failed — key=%s",
                deployment_key,
                exc_info=True,
            )
            raise

    async def get_deployment_config_for_routing(
        self, tenant_id: UUID | str, deployment_key: str
    ) -> dict[str, Any] | None:
        """Return the full routing projection for a deployment, or None if not found.

        Unlike get_deployment_by_key, this projection:
          - includes secret_reference (required by the routing layer for credential lookup)
          - resolves provider_name and model_name via JOIN (routing works with names, not UUIDs)
        """
        self.validate_uuid(tenant_id, "tenant_id")
        self.validate_string_not_empty(deployment_key, "deployment_key")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(GET_DEPLOYMENT_FOR_ROUTING_BY_KEY_SQL),
                    {"tenant_id": str(tenant_id), "deployment_key": deployment_key},
                )
                row = result.mappings().one_or_none()
                return dict(row) if row else None
        except Exception:
            logger.error(
                "TenantDeploymentPersistence: get_deployment_config_for_routing failed "
                "— tenant_id=%s deployment_key=%s",
                tenant_id,
                deployment_key,
                exc_info=True,
            )
            raise

    async def get_deployment_secret_reference(self, deployment_id: UUID | str) -> str | None:
        """Return ONLY the secret_reference for use by the routing layer.

        Intentionally separate from the standard read path to make credential
        access an explicit, grep-visible decision.
        """
        self.validate_uuid(deployment_id, "deployment_id")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(GET_DEPLOYMENT_SECRET_REFERENCE_SQL),
                    {"deployment_id": str(deployment_id)},
                )
                row = result.one_or_none()
                return row[0] if row else None
        except Exception:
            logger.error(
                "TenantDeploymentPersistence: get_deployment_secret_reference failed — id=%s",
                deployment_id,
                exc_info=True,
            )
            raise

    async def get_default_deployment(
        self, tenant_id: UUID, provider_id: UUID
    ) -> dict[str, Any] | None:
        """Return the default active deployment for a tenant/provider combination."""
        self.validate_uuid(tenant_id, "tenant_id")
        self.validate_uuid(provider_id, "provider_id")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(GET_DEFAULT_DEPLOYMENT_SQL),
                    {"tenant_id": str(tenant_id), "provider_id": str(provider_id)},
                )
                row = result.mappings().one_or_none()
                return dict(row) if row else None
        except Exception:
            logger.error(
                "TenantDeploymentPersistence: get_default_deployment failed", exc_info=True
            )
            raise

    async def list_deployments(
        self,
        tenant_id: UUID,
        filters: TenantDeploymentListFilters,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return deployments for a tenant, with optional provider and status filters."""
        self.validate_uuid(tenant_id, "tenant_id")
        self.validate_pagination_parameters(limit, offset)
        if filters.provider_id is not None:
            self.validate_uuid(filters.provider_id, "provider_id")
        sql, params = build_tenant_deployment_list_query(
            str(tenant_id),
            filters,
            DEPLOYMENT_SAFE_COLUMNS,
            limit,
            offset,
        )

        try:
            async with self.get_session() as session:
                result = await session.execute(text(sql), params)
                return [dict(row) for row in result.mappings().all()]
        except Exception:
            logger.error(
                "TenantDeploymentPersistence: list_deployments failed — tenant=%s",
                tenant_id,
                exc_info=True,
            )
            raise

    async def list_active_deployments_for_route(
        self,
        tenant_id: UUID,
        provider_id: UUID,
        model_id: UUID,
    ) -> list[dict[str, Any]]:
        """Return all active deployments for a (tenant, provider, model) routing path.

        Results are ordered by routing_priority DESC then deployment_name. The
        routing layer uses this list to select the best available endpoint.
        """
        self.validate_uuid(tenant_id, "tenant_id")
        self.validate_uuid(provider_id, "provider_id")
        self.validate_uuid(model_id, "model_id")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(LIST_ACTIVE_DEPLOYMENTS_BY_PROVIDER_AND_MODEL_SQL),
                    {
                        "tenant_id": str(tenant_id),
                        "provider_id": str(provider_id),
                        "model_id": str(model_id),
                    },
                )
                return [dict(row) for row in result.mappings().all()]
        except Exception:
            logger.error(
                "TenantDeploymentPersistence: list_active_deployments_for_route failed",
                exc_info=True,
            )
            raise

    async def count_deployments(
        self,
        tenant_id: UUID,
        filters: TenantDeploymentListFilters,
    ) -> int:
        """Return deployment count for a tenant."""
        self.validate_uuid(tenant_id, "tenant_id")
        if filters.provider_id is not None:
            self.validate_uuid(filters.provider_id, "provider_id")
        sql, params = build_tenant_deployment_count_query(str(tenant_id), filters)
        try:
            async with self.get_session() as session:
                result = await session.execute(text(sql), params)
                return result.scalar_one_or_none() or 0
        except Exception:
            logger.error("TenantDeploymentPersistence: count_deployments failed", exc_info=True)
            raise

    # =========================================================================
    # UPDATE
    # =========================================================================

    async def update_deployment(
        self,
        deployment_id: UUID,
        deployment_name: str | None = None,
        status: str | None = None,
        api_endpoint_url: str | None = None,
        secret_reference: str | None = None,
        cloud_provider: str | None = None,
        cloud_region: str | None = None,
        provider_deployment_name: str | None = None,
        token_capacity_limit: int | None = None,
        token_lock_duration_seconds: int | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        default_temperature: float | None = None,
        default_top_p: float | None = None,
        default_max_output_tokens: int | None = None,
        is_default: bool | None = None,
        routing_priority: int | None = None,
        extra_headers: dict[str, Any] | None = None,
        extra_config: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Partially update a deployment. Returns updated row (without secret_reference) or None."""
        self.validate_uuid(deployment_id, "deployment_id")

        update_fields: dict[str, Any] = {}

        if deployment_name is not None:
            self.validate_string_not_empty(deployment_name, "deployment_name")
            update_fields["deployment_name"] = deployment_name
        if status is not None:
            self.validate_enum_value(status, _VALID_STATUSES, "status")
            update_fields["status"] = status
        if api_endpoint_url is not None:
            self.validate_string_not_empty(api_endpoint_url, "api_endpoint_url")
            update_fields["api_endpoint_url"] = api_endpoint_url
        if secret_reference is not None:
            self.validate_string_not_empty(secret_reference, "secret_reference")
            update_fields["secret_reference"] = secret_reference
        if cloud_provider is not None:
            update_fields["cloud_provider"] = cloud_provider
        if cloud_region is not None:
            update_fields["cloud_region"] = cloud_region
        if provider_deployment_name is not None:
            update_fields["provider_deployment_name"] = provider_deployment_name
        if token_capacity_limit is not None:
            self.validate_positive_integer(token_capacity_limit, "token_capacity_limit")
            update_fields["token_capacity_limit"] = token_capacity_limit
        if token_lock_duration_seconds is not None:
            self.validate_positive_integer(
                token_lock_duration_seconds, "token_lock_duration_seconds"
            )
            update_fields["token_lock_duration_seconds"] = token_lock_duration_seconds
        if timeout_seconds is not None:
            if timeout_seconds <= 0:
                raise ValueError(f"timeout_seconds must be positive, got {timeout_seconds}")
            update_fields["timeout_seconds"] = timeout_seconds
        if max_retries is not None:
            self.validate_positive_integer(max_retries, "max_retries", allow_zero=True)
            update_fields["max_retries"] = max_retries
        if default_temperature is not None:
            self._validate_temperature(default_temperature, "default_temperature")
            update_fields["default_temperature"] = str(default_temperature)
        if default_top_p is not None:
            self._validate_top_p(default_top_p, "default_top_p")
            update_fields["default_top_p"] = str(default_top_p)
        if default_max_output_tokens is not None:
            self.validate_positive_integer(default_max_output_tokens, "default_max_output_tokens")
            update_fields["default_max_output_tokens"] = default_max_output_tokens
        if is_default is not None:
            update_fields["is_default"] = is_default
        if routing_priority is not None:
            self.validate_positive_integer(routing_priority, "routing_priority", allow_zero=True)
            update_fields["routing_priority"] = routing_priority
        if extra_headers is not None:
            update_fields["extra_headers"] = self._validate_and_serialize_json(
                extra_headers, "extra_headers"
            )
        if extra_config is not None:
            update_fields["extra_config"] = self._validate_and_serialize_json(
                extra_config, "extra_config"
            )

        if not update_fields:
            return await self.get_deployment_by_id(deployment_id)

        # Build a custom RETURNING clause that excludes secret_reference.
        returning_cols = (
            "deployment_id, tenant_id, provider_id, model_id, deployment_key, "
            "deployment_name, status, api_endpoint_url, cloud_provider, cloud_region, "
            "provider_deployment_name, token_capacity_limit, token_lock_duration_seconds, "
            "timeout_seconds, max_retries, default_temperature, default_top_p, "
            "default_max_output_tokens, is_default, routing_priority, extra_headers, "
            "extra_config, created_by_user_id, created_at, updated_at"
        )
        set_clauses = ["updated_at = CURRENT_TIMESTAMP"]
        params: dict[str, Any] = {"deployment_id": str(deployment_id)}
        for field, value in update_fields.items():
            key = f"set_{field}"
            set_clauses.append(f"{field} = :{key}")
            params[key] = value

        sql = (
            f"UPDATE tenant_deployments "
            f"SET {', '.join(set_clauses)} "
            f"WHERE deployment_id = :deployment_id "
            f"RETURNING {returning_cols}"
        )

        try:
            async with self.get_session() as session:
                result = await session.execute(text(sql), params)
                row = result.mappings().one_or_none()
                if row:
                    self.log_operation("UPDATE", deployment_id)
                    return dict(row)
                return None
        except Exception:
            logger.error(
                "TenantDeploymentPersistence: update_deployment failed — id=%s",
                deployment_id,
                exc_info=True,
            )
            raise

    async def set_maintenance(self, deployment_id: UUID) -> dict[str, Any] | None:
        """Put a deployment into maintenance mode."""
        return await self.update_deployment(deployment_id=deployment_id, status="maintenance")

    async def set_active(self, deployment_id: UUID) -> dict[str, Any] | None:
        """Return a deployment to active status."""
        return await self.update_deployment(deployment_id=deployment_id, status="active")

    # =========================================================================
    # DELETE
    # =========================================================================

    async def delete_deployment(self, deployment_id: UUID) -> bool:
        """Delete a deployment. CASCADE removes linked user_entitlements.

        Returns:
            True if deleted; False if not found.
        """
        self.validate_uuid(deployment_id, "deployment_id")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(DELETE_DEPLOYMENT_BY_ID_SQL), {"deployment_id": str(deployment_id)}
                )
                deleted = getattr(result, "rowcount", 0) > 0
                if deleted:
                    self.log_operation("DELETE", deployment_id)
                return bool(deleted)
        except Exception:
            logger.error(
                "TenantDeploymentPersistence: delete_deployment failed — id=%s",
                deployment_id,
                exc_info=True,
            )
            raise
