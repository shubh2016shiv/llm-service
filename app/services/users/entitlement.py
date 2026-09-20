"""
User Entitlement Service
========================

Business service for managing explicit user routing grants, called entitlements.

What is an entitlement?
    An entitlement is the exact authorization record for one user and one
    tenant deployment. Inference never searches for a fallback entitlement;
    the caller supplies the authorized entitlement ID explicitly.

What this service adds beyond CRUD:
    - Enforces authorization based on caller role and identity.
    - Normalizes persistence validation failures into domain exceptions.
    - Invalidates route-specific authorization cache entries after changes so
      inference routing reflects entitlement updates immediately.

Enterprise Pattern: Authorization-Aware CRUD Service Pattern
    Mutating operations apply authorization, persist changes, and refresh the
    exact cache route impacted by the entitlement.

Author: Shubham Singh
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.core.exceptions import ManagementValidationError, ResourceNotFoundError
from app.schemas.enums import ProviderCatalogAuthMode
from app.services.credential_encoding import build_credential_path, encode_credential
from app.services.management_helpers import (
    Row,
    clean_row,
    clean_rows,
    raise_clean_validation_error,
)

if TYPE_CHECKING:
    from app.auth.authorization.authorization_grant_cache import AuthorizationGrantCache
    from app.auth.authorization.tenant_access import TenantAccessService
    from app.database import TenantDeploymentPersistence, UserEntitlementPersistence
    from app.schemas.auth_schema import AuthTokenPayload
    from app.schemas.management_schema import EntitlementCreateRequest, EntitlementUpdateRequest
    from app.services.credential_encoding import CredentialWriter


class UserEntitlementService:
    """Manage user-specific deployment entitlement records."""

    def __init__(
        self,
        entitlement_persistence: UserEntitlementPersistence,
        access_service: TenantAccessService,
        deployment_persistence: TenantDeploymentPersistence,
        credential_writer: CredentialWriter | None,
        *,
        authorization_cache: AuthorizationGrantCache,
    ) -> None:
        """Initialize persistence, access, secrets, and cache coherence."""
        self._entitlements = entitlement_persistence
        self._access = access_service
        self._deployments = deployment_persistence
        self._credential_writer = credential_writer
        self._authorization_cache = authorization_cache

    async def create_entitlement(
        self, user_id: UUID, request: EntitlementCreateRequest, current_user: AuthTokenPayload
    ) -> Row:
        """Create an entitlement linking a user to a tenant deployment.

        Caller must be tenant admin for the target tenant. When the request
        supplies its own credential (a personal override — see
        user_entitlements' schema comment), it is stored under its own Vault
        path, distinct from the underlying deployment's credential. When it
        does not, the entitlement inherits the deployment's own credential
        reference directly — unlike a deployment, an entitlement has no
        "ambient infrastructure credential" case (there is no ensure_role
        for a user, the way there is IAM for a Bedrock deployment), so
        omitting a credential here always means "use the deployment's",
        never "no credential is needed." On success, the route-specific
        authorization cache is invalidated for immediate effect.
        """
        await self._access.ensure_tenant_admin(request.tenant_id, current_user)
        deployment = await self._require_entitlement_source(
            request.tenant_id,
            request.deployment_key,
            request.provider_id,
            request.model_id,
        )
        if request.credential is None:
            secret_reference = self._inherit_deployment_credential(deployment)
        else:
            secret_reference = await encode_credential(
                request.credential,
                build_credential_path(
                    "user-entitlements",
                    str(request.tenant_id),
                    str(user_id),
                    request.entitlement_name,
                ),
                str(request.tenant_id),
                self._credential_writer,
                ProviderCatalogAuthMode(str(deployment["auth_mode"])),
            )
        payload = request.model_dump(exclude={"credential", "extra_config"})
        payload["secret_reference"] = secret_reference
        payload["extra_config"] = request.extra_config
        try:
            row = await self._entitlements.create_entitlement(
                user_id=user_id,
                created_by_user_id=current_user.user_id,
                **payload,
            )
        except ValueError as exc:
            raise_clean_validation_error(exc)
        await self._invalidate_entitlement_route(clean_row(row))
        return clean_row(row)

    async def _require_entitlement_source(
        self,
        tenant_id: UUID,
        deployment_key: str,
        provider_id: UUID,
        model_id: UUID,
    ) -> dict[str, object]:
        """Load the source deployment and reject a mismatched route request."""
        deployment = await self._deployments.get_entitlement_source_by_key(
            tenant_id,
            deployment_key,
        )
        if deployment is None:
            raise ResourceNotFoundError("TenantDeployment", deployment_key)
        if str(deployment["provider_id"]) != str(provider_id):
            raise ManagementValidationError(
                "Entitlement provider_id must match its source deployment."
            )
        if str(deployment["model_id"]) != str(model_id):
            raise ManagementValidationError("Entitlement model_id must match its source deployment.")
        return deployment

    @staticmethod
    def _inherit_deployment_credential(
        deployment: dict[str, object],
    ) -> str:
        """Point an entitlement at the same secret its underlying deployment uses.

        No new secret is written. The entitlement stores the deployment's
        existing Vault reference (or ``iam:default`` for AWS SigV4). The
        provider catalog remains the only source of authentication policy.
        """
        return str(deployment["secret_reference"])

    async def list_user_entitlements(
        self,
        tenant_id: UUID,
        user_id: UUID,
        current_user: AuthTokenPayload,
        limit: int,
        offset: int,
    ) -> list[Row]:
        """List one user's entitlements within a tenant.

        Access requires both:
            - self-or-admin identity permission for the target user, and
            - tenant-read permission for the target tenant.
        """
        self._access.ensure_self_or_admin(user_id, current_user)
        await self._access.ensure_tenant_read(tenant_id, current_user)
        rows = await self._entitlements.get_user_entitlements(tenant_id, user_id, limit, offset)
        return clean_rows(rows)

    async def count_user_entitlements(
        self, tenant_id: UUID, user_id: UUID, current_user: AuthTokenPayload
    ) -> int:
        """Count entitlements for one user in one tenant with matching auth rules."""
        self._access.ensure_self_or_admin(user_id, current_user)
        await self._access.ensure_tenant_read(tenant_id, current_user)
        return await self._entitlements.count_user_entitlements(tenant_id, user_id)

    async def get_entitlement(
        self,
        user_id: UUID,
        entitlement_id: UUID,
        current_user: AuthTokenPayload,
    ) -> Row:
        """Retrieve a single entitlement after identity and tenant authorization.

        The method first confirms user ownership scope and then verifies
        tenant-read access for the entitlement's tenant.
        """
        self._access.ensure_self_or_admin(user_id, current_user)
        row = await self._entitlements.get_entitlement_by_id(entitlement_id)
        if row is None or str(row.get("user_id")) != str(user_id):
            raise ResourceNotFoundError("UserEntitlement", str(entitlement_id))
        await self._access.ensure_tenant_read(UUID(str(row["tenant_id"])), current_user)
        return clean_row(row)

    async def update_entitlement(
        self,
        user_id: UUID,
        entitlement_id: UUID,
        request: EntitlementUpdateRequest,
        current_user: AuthTokenPayload,
    ) -> Row:
        """Partially update an entitlement after tenant-admin authorization.

        Tenant admin check is evaluated against the entitlement's existing
        tenant association to prevent unauthorized cross-tenant updates.
        """
        existing = await self.get_entitlement(user_id, entitlement_id, current_user)
        tenant_id = UUID(str(existing["tenant_id"]))
        await self._access.ensure_tenant_admin(tenant_id, current_user)
        update_fields = request.model_dump(exclude_unset=True, exclude={"credential", "extra_config"})
        extra_config = request.extra_config if "extra_config" in request.model_fields_set else None
        if "credential" in request.model_fields_set:
            deployment = await self._require_entitlement_source(
                tenant_id,
                str(existing["deployment_key"]),
                UUID(str(existing["provider_id"])),
                UUID(str(existing["model_id"])),
            )
            secret_reference = await encode_credential(
                request.credential,
                build_credential_path(
                    "user-entitlements",
                    str(tenant_id),
                    str(user_id),
                    str(existing["entitlement_name"]),
                ),
                str(tenant_id),
                self._credential_writer,
                ProviderCatalogAuthMode(str(deployment["auth_mode"])),
            )
            update_fields["secret_reference"] = secret_reference
        if "extra_config" in request.model_fields_set:
            update_fields["extra_config"] = extra_config
        try:
            row = await self._entitlements.update_entitlement(
                entitlement_id=entitlement_id,
                **update_fields,
            )
        except ValueError as exc:
            raise_clean_validation_error(exc)
        if row is None:
            raise ResourceNotFoundError("UserEntitlement", str(entitlement_id))
        await self._invalidate_entitlement_route(clean_row(row))
        return clean_row(row)

    async def delete_entitlement(
        self, user_id: UUID, entitlement_id: UUID, current_user: AuthTokenPayload
    ) -> None:
        """Delete an entitlement after tenant-admin authorization checks."""
        existing = await self.get_entitlement(user_id, entitlement_id, current_user)
        await self._access.ensure_tenant_admin(UUID(str(existing["tenant_id"])), current_user)
        deleted = await self._entitlements.delete_entitlement(entitlement_id)
        if not deleted:
            raise ResourceNotFoundError("UserEntitlement", str(entitlement_id))
        await self._invalidate_entitlement_route(existing)

    async def _invalidate_entitlement_route(self, entitlement: Row) -> None:
        """Invalidate cached authorization for one entitlement route tuple.

        The tuple is ``(tenant_id, user_id, deployment_key)`` which uniquely
        identifies routing decisions affected by entitlement updates.
        """
        await self._authorization_cache.invalidate_route(
            tenant_id=UUID(str(entitlement["tenant_id"])),
            user_id=UUID(str(entitlement["user_id"])),
            deployment_key=str(entitlement["deployment_key"]),
        )
