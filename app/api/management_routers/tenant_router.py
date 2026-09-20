"""Tenant lifecycle HTTP endpoints.

This router translates HTTP inputs and outputs only. ``TenantService`` owns
business rules, PostgreSQL owns persistence, and the global API boundary is the
final exception safety net. Membership endpoints live in ``membership_router``
because membership is a separate resource with different permissions.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.exception_handlers import translate_management_error
from app.api.management_dependencies import get_tenant_service
from app.auth import (
    AuthTokenPayload,
    require_admin,
    require_developer,
    require_operator,
    require_owner,
)
from app.core.exceptions import LLMServiceError
from app.schemas.management_filters import TenantListFilters
from app.schemas.management_schema import (
    PaginatedResponse,
    ResourceResponse,
    TenantCreateRequest,
    TenantUpdateRequest,
)
from app.services import TenantService

router = APIRouter(prefix="/api/v1/tenants", tags=["Tenant Management"])
TenantServiceDependency = Annotated[TenantService, Depends(get_tenant_service)]


@router.post("", response_model=ResourceResponse, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    body: TenantCreateRequest,
    service: TenantServiceDependency,
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Create a tenant; platform-administrator authority is required."""
    try:
        return ResourceResponse.model_validate(await service.create_tenant(body))
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.get("", response_model=PaginatedResponse)
async def list_tenants(
    service: TenantServiceDependency,
    current_user: Annotated[AuthTokenPayload, Depends(require_operator)],
    status_filter: str | None = Query(default=None),
    tier_filter: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse:
    """Return one bounded page of tenant records."""
    filters = TenantListFilters(status_filter=status_filter, tier_filter=tier_filter)
    rows = await service.list_tenants(filters, limit, offset)
    total = await service.count_tenants(filters)
    return PaginatedResponse(items=rows, total=total, limit=limit, offset=offset)


@router.get("/{tenant_id}", response_model=ResourceResponse)
async def get_tenant(
    tenant_id: UUID,
    service: TenantServiceDependency,
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
) -> ResourceResponse:
    """Return one tenant after tenant-scoped read authorization."""
    try:
        return ResourceResponse.model_validate(await service.get_tenant(tenant_id, current_user))
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.patch("/{tenant_id}", response_model=ResourceResponse)
async def update_tenant(
    tenant_id: UUID,
    body: TenantUpdateRequest,
    service: TenantServiceDependency,
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Apply supplied fields and invalidate stale authorization state."""
    try:
        result = await service.update_tenant(tenant_id, body, current_user)
        return ResourceResponse.model_validate(result)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.patch("/{tenant_id}/suspend", response_model=ResourceResponse)
async def suspend_tenant(
    tenant_id: UUID,
    service: TenantServiceDependency,
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Suspend a tenant and revoke cached access decisions."""
    try:
        result = await service.suspend_tenant(tenant_id, current_user)
        return ResourceResponse.model_validate(result)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.patch("/{tenant_id}/activate", response_model=ResourceResponse)
async def activate_tenant(
    tenant_id: UUID,
    service: TenantServiceDependency,
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Activate a tenant and clear stale authorization state."""
    try:
        result = await service.activate_tenant(tenant_id, current_user)
        return ResourceResponse.model_validate(result)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.delete("/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tenant(
    tenant_id: UUID,
    service: TenantServiceDependency,
    current_user: Annotated[AuthTokenPayload, Depends(require_owner)],
) -> Response:
    """Delete a tenant; owner authority is required for this destructive action."""
    try:
        await service.delete_tenant(tenant_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except LLMServiceError as exc:
        translate_management_error(exc)
