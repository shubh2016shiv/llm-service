"""Tenant-membership HTTP endpoints.

Membership changes alter authorization, so write operations use administrator
checks and the service invalidates affected authorization-cache entries.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.exception_handlers import translate_management_error
from app.api.management_dependencies import get_tenant_membership_service
from app.auth import AuthTokenPayload, require_admin, require_developer
from app.core.exceptions import LLMServiceError
from app.schemas.management_filters import TenantMembershipListFilters
from app.schemas.management_schema import (
    MembershipCreateRequest,
    MembershipUpdateRequest,
    PaginatedResponse,
    ResourceResponse,
)
from app.services import TenantMembershipService

router = APIRouter(prefix="/api/v1/tenants", tags=["Tenant Memberships"])
MembershipServiceDependency = Annotated[
    TenantMembershipService,
    Depends(get_tenant_membership_service),
]


@router.post(
    "/{tenant_id}/members",
    response_model=ResourceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_member(
    tenant_id: UUID,
    body: MembershipCreateRequest,
    service: MembershipServiceDependency,
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Add one user to a tenant with a validated tenant role."""
    try:
        result = await service.create_membership(tenant_id, body, current_user)
        return ResourceResponse.model_validate(result)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.get("/{tenant_id}/members", response_model=PaginatedResponse)
async def list_members(
    tenant_id: UUID,
    service: MembershipServiceDependency,
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
    tenant_role_filter: str | None = Query(default=None),
    active_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse:
    """Return a filtered, bounded membership page for one tenant."""
    try:
        filters = TenantMembershipListFilters(
            tenant_role_filter=tenant_role_filter,
            active_only=active_only,
        )
        rows = await service.list_tenant_memberships(
            tenant_id, current_user, filters, limit, offset
        )
        total = await service.count_tenant_members(tenant_id, current_user, filters)
        return PaginatedResponse(items=rows, total=total, limit=limit, offset=offset)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.get("/{tenant_id}/members/{membership_id}", response_model=ResourceResponse)
async def get_member(
    tenant_id: UUID,
    membership_id: UUID,
    service: MembershipServiceDependency,
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
) -> ResourceResponse:
    """Return one membership within its tenant scope."""
    try:
        result = await service.get_tenant_membership(tenant_id, membership_id, current_user)
        return ResourceResponse.model_validate(result)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.patch("/{tenant_id}/members/{membership_id}", response_model=ResourceResponse)
async def update_member(
    tenant_id: UUID,
    membership_id: UUID,
    body: MembershipUpdateRequest,
    service: MembershipServiceDependency,
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Update one membership and revoke its cached access decisions."""
    try:
        result = await service.update_membership(
            tenant_id, membership_id, body, current_user
        )
        return ResourceResponse.model_validate(result)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.delete("/{tenant_id}/members/{membership_id}", status_code=204)
async def delete_member(
    tenant_id: UUID,
    membership_id: UUID,
    service: MembershipServiceDependency,
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> Response:
    """Remove one membership and revoke its cached authorization."""
    try:
        await service.delete_membership(tenant_id, membership_id, current_user)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except LLMServiceError as exc:
        translate_management_error(exc)
