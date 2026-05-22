"""
User Entitlement Routes
=======================

This router manages which user can use which tenant deployment routes through
entitlement records.

Enterprise Pattern: Thin Router Pattern
    Route handlers stay focused on HTTP and call services for all domain rules.

Author: Shubham Singh
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.dependencies import get_user_entitlement_service
from app.api.exception_handlers import translate_management_error
from app.auth import AuthTokenPayload, require_admin, require_developer
from app.core.exceptions import LLMServiceError
from app.schemas.management_schema import (
    EntitlementCreateRequest,
    EntitlementUpdateRequest,
    PaginatedResponse,
    ResourceResponse,
)
from app.services import UserEntitlementService

router = APIRouter(prefix="/api/v1/users", tags=["User Entitlements"])
TenantIdQuery = Annotated[UUID, Query(description="Tenant scope for entitlement lookup.")]


@router.post("/{user_id}/entitlements", response_model=ResourceResponse, status_code=201)
async def create_entitlement(
    user_id: UUID,
    body: EntitlementCreateRequest,
    service: Annotated[UserEntitlementService, Depends(get_user_entitlement_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Create a user entitlement."""
    try:
        row = await service.create_entitlement(user_id, body, current_user)
        return ResourceResponse.model_validate(row)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.get("/{user_id}/entitlements", response_model=PaginatedResponse)
async def list_entitlements(
    user_id: UUID,
    service: Annotated[UserEntitlementService, Depends(get_user_entitlement_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
    tenant_id: TenantIdQuery,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse:
    """List user entitlements within a tenant."""
    try:
        rows = await service.list_user_entitlements(tenant_id, user_id, current_user, limit, offset)
        total = await service.count_user_entitlements(tenant_id, user_id, current_user)
        return PaginatedResponse(items=rows, total=total, limit=limit, offset=offset)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.get("/{user_id}/entitlements/{entitlement_id}", response_model=ResourceResponse)
async def get_entitlement(
    user_id: UUID,
    entitlement_id: UUID,
    service: Annotated[UserEntitlementService, Depends(get_user_entitlement_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
) -> ResourceResponse:
    """Retrieve one user entitlement."""
    try:
        row = await service.get_entitlement(user_id, entitlement_id, current_user)
        return ResourceResponse.model_validate(row)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.patch("/{user_id}/entitlements/{entitlement_id}", response_model=ResourceResponse)
async def update_entitlement(
    user_id: UUID,
    entitlement_id: UUID,
    body: EntitlementUpdateRequest,
    service: Annotated[UserEntitlementService, Depends(get_user_entitlement_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Partially update one user entitlement."""
    try:
        row = await service.update_entitlement(user_id, entitlement_id, body, current_user)
        return ResourceResponse.model_validate(row)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.delete("/{user_id}/entitlements/{entitlement_id}", status_code=204)
async def delete_entitlement(
    user_id: UUID,
    entitlement_id: UUID,
    service: Annotated[UserEntitlementService, Depends(get_user_entitlement_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> Response:
    """Delete one user entitlement."""
    try:
        await service.delete_entitlement(user_id, entitlement_id, current_user)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except LLMServiceError as exc:
        translate_management_error(exc)
