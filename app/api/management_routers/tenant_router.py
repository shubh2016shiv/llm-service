from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.dependencies import (
    get_tenant_membership_service,
    get_tenant_service,
)
from app.api.exception_handlers import translate_management_error
from app.auth import (
    AuthTokenPayload,
    require_admin,
    require_developer,
    require_operator,
    require_owner,
)
from app.core.exceptions import LLMServiceError
from app.services import TenantMembershipService, TenantService
from app.schemas.management_filters import TenantListFilters, TenantMembershipListFilters
from app.schemas.management_schema import (
    MembershipCreateRequest,
    MembershipUpdateRequest,
    PaginatedResponse,
    ResourceResponse,
    TenantCreateRequest,
    TenantUpdateRequest,
)

router = APIRouter(prefix="/api/v1/tenants", tags=["Tenant Management"])


@router.post("", response_model=ResourceResponse, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    body: TenantCreateRequest,
    service: Annotated[TenantService, Depends(get_tenant_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Create a tenant."""
    try:
        return ResourceResponse.model_validate(await service.create_tenant(body))
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.get("", response_model=PaginatedResponse)
async def list_tenants(
    service: Annotated[TenantService, Depends(get_tenant_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_operator)],
    status_filter: str | None = Query(default=None),
    tier_filter: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse:
    """List tenants for platform operators."""
    filters = TenantListFilters(status_filter=status_filter, tier_filter=tier_filter)
    rows = await service.list_tenants(filters, limit, offset)
    total = await service.count_tenants(filters)
    return PaginatedResponse(items=rows, total=total, limit=limit, offset=offset)


@router.get("/{tenant_id}", response_model=ResourceResponse)
async def get_tenant(
    tenant_id: UUID,
    service: Annotated[TenantService, Depends(get_tenant_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
) -> ResourceResponse:
    """Retrieve one tenant after tenant access checks."""
    try:
        return ResourceResponse.model_validate(await service.get_tenant(tenant_id, current_user))
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.patch("/{tenant_id}", response_model=ResourceResponse)
async def update_tenant(
    tenant_id: UUID,
    body: TenantUpdateRequest,
    service: Annotated[TenantService, Depends(get_tenant_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Partially update one tenant."""
    try:
        return ResourceResponse.model_validate(
            await service.update_tenant(tenant_id, body, current_user)
        )
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.patch("/{tenant_id}/suspend", response_model=ResourceResponse)
async def suspend_tenant(
    tenant_id: UUID,
    service: Annotated[TenantService, Depends(get_tenant_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Suspend a tenant."""
    try:
        return ResourceResponse.model_validate(await service.suspend_tenant(tenant_id, current_user))
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.patch("/{tenant_id}/activate", response_model=ResourceResponse)
async def activate_tenant(
    tenant_id: UUID,
    service: Annotated[TenantService, Depends(get_tenant_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Activate a tenant."""
    try:
        return ResourceResponse.model_validate(await service.activate_tenant(tenant_id, current_user))
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.delete("/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tenant(
    tenant_id: UUID,
    service: Annotated[TenantService, Depends(get_tenant_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_owner)],
) -> Response:
    """Delete a tenant."""
    try:
        await service.delete_tenant(tenant_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.post("/{tenant_id}/members", response_model=ResourceResponse, status_code=201)
async def create_member(
    tenant_id: UUID,
    body: MembershipCreateRequest,
    service: Annotated[TenantMembershipService, Depends(get_tenant_membership_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Add a member to a tenant."""
    try:
        return ResourceResponse.model_validate(
            await service.create_membership(tenant_id, body, current_user)
        )
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.get("/{tenant_id}/members", response_model=PaginatedResponse)
async def list_members(
    tenant_id: UUID,
    service: Annotated[TenantMembershipService, Depends(get_tenant_membership_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
    tenant_role_filter: str | None = Query(default=None),
    active_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse:
    """List tenant members."""
    try:
        filters = TenantMembershipListFilters(
            tenant_role_filter=tenant_role_filter,
            active_only=active_only,
        )
        rows = await service.list_tenant_memberships(tenant_id, current_user, filters, limit, offset)
        total = await service.count_tenant_members(tenant_id, filters)
        return PaginatedResponse(items=rows, total=total, limit=limit, offset=offset)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.get("/{tenant_id}/members/{membership_id}", response_model=ResourceResponse)
async def get_member(
    tenant_id: UUID,
    membership_id: UUID,
    service: Annotated[TenantMembershipService, Depends(get_tenant_membership_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
) -> ResourceResponse:
    """Retrieve one tenant membership."""
    try:
        row = await service.get_tenant_membership(tenant_id, membership_id, current_user)
        return ResourceResponse.model_validate(row)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.patch("/{tenant_id}/members/{membership_id}", response_model=ResourceResponse)
async def update_member(
    tenant_id: UUID,
    membership_id: UUID,
    body: MembershipUpdateRequest,
    service: Annotated[TenantMembershipService, Depends(get_tenant_membership_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Partially update one tenant membership."""
    try:
        row = await service.update_membership(tenant_id, membership_id, body, current_user)
        return ResourceResponse.model_validate(row)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.delete("/{tenant_id}/members/{membership_id}", status_code=204)
async def delete_member(
    tenant_id: UUID,
    membership_id: UUID,
    service: Annotated[TenantMembershipService, Depends(get_tenant_membership_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> Response:
    """Delete one tenant membership."""
    try:
        await service.delete_membership(tenant_id, membership_id, current_user)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except LLMServiceError as exc:
        translate_management_error(exc)
