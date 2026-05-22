"""
User Management Router.

Architecture:
-------------
    +------------------------------+
    ¦ admin/developer caller       ¦
    +------------------------------+
                   ?
    +------------------------------+
    ¦ user router (`/api/v1/users`)|
    +------------------------------+
           +------------------------+
           ?                        ?
    +-------------------+    +------------------------+
    ¦ UserService       ¦    ¦ TenantMembershipService¦
    +-------------------+    +------------------------+
              ?                           ?
    +-------------------+        +---------------------+
    ¦ user persistence  ¦        ¦ membership storage  ¦
    +-------------------+        +---------------------+

Purpose:
    Provide user lifecycle APIs and user-to-tenant membership lookup APIs.

Rationale:
    User data is platform-scoped, while memberships are tenant-scoped. This
    router surfaces both together because operational workflows often traverse
    from user identity to tenant access posture.

Author: Shubham Singh
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.dependencies import (
    get_tenant_membership_service,
    get_user_service,
)
from app.api.exception_handlers import translate_management_error
from app.auth import AuthTokenPayload, require_admin, require_developer
from app.core.exceptions import LLMServiceError
from app.schemas.management_schema import (
    PaginatedResponse,
    ResourceResponse,
    UserCreateRequest,
    UserUpdateRequest,
)
from app.services import TenantMembershipService, UserService

router = APIRouter(prefix="/api/v1/users", tags=["User Management"])


@router.post("", response_model=ResourceResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreateRequest,
    service: Annotated[UserService, Depends(get_user_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Create a platform user record.

    Args:
        body: User creation payload.
        service: User business service.
        current_user: Authenticated admin caller.

    Returns:
        ResourceResponse: Created user envelope.
    """
    try:
        return ResourceResponse.model_validate(await service.create_user(body))
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.get("", response_model=PaginatedResponse)
async def list_users(
    service: Annotated[UserService, Depends(get_user_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
    platform_role_filter: str | None = Query(default=None),
    status_filter: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse:
    """List users with optional role/status filters.

    Args:
        service: User business service.
        current_user: Authenticated admin caller.
        platform_role_filter: Optional platform role filter.
        status_filter: Optional user status filter.
        limit: Maximum rows to return.
        offset: Pagination offset.

    Returns:
        PaginatedResponse: User rows and pagination metadata.
    """
    rows = await service.list_users(platform_role_filter, status_filter, limit, offset)
    total = await service.count_users(platform_role_filter, status_filter)
    return PaginatedResponse(items=rows, total=total, limit=limit, offset=offset)


@router.get("/email/{email}", response_model=ResourceResponse)
async def get_user_by_email(
    email: str,
    service: Annotated[UserService, Depends(get_user_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
) -> ResourceResponse:
    """Fetch one user by email address.

    Args:
        email: Target email to look up.
        service: User business service.
        current_user: Authenticated developer-or-higher caller.

    Returns:
        ResourceResponse: Requested user envelope.
    """
    try:
        return ResourceResponse.model_validate(await service.get_user_by_email(email))
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.get("/{user_id}", response_model=ResourceResponse)
async def get_user(
    user_id: UUID,
    service: Annotated[UserService, Depends(get_user_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
) -> ResourceResponse:
    """Fetch one user by UUID.

    Args:
        user_id: User identifier.
        service: User business service.
        current_user: Authenticated developer-or-higher caller.

    Returns:
        ResourceResponse: Requested user envelope.
    """
    try:
        return ResourceResponse.model_validate(await service.get_user(user_id))
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.patch("/{user_id}", response_model=ResourceResponse)
async def update_user(
    user_id: UUID,
    body: UserUpdateRequest,
    service: Annotated[UserService, Depends(get_user_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Apply partial updates to one user.

    Args:
        user_id: User identifier.
        body: Partial user updates.
        service: User business service.
        current_user: Authenticated admin caller.

    Returns:
        ResourceResponse: Updated user envelope.
    """
    try:
        return ResourceResponse.model_validate(await service.update_user(user_id, body))
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.patch("/{user_id}/suspend", response_model=ResourceResponse)
async def suspend_user(
    user_id: UUID,
    service: Annotated[UserService, Depends(get_user_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Suspend a user account.

    Args:
        user_id: User identifier.
        service: User business service.
        current_user: Authenticated admin caller.

    Returns:
        ResourceResponse: Suspended user envelope.
    """
    try:
        return ResourceResponse.model_validate(await service.suspend_user(user_id))
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.patch("/{user_id}/activate", response_model=ResourceResponse)
async def activate_user(
    user_id: UUID,
    service: Annotated[UserService, Depends(get_user_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Activate a suspended/inactive user account.

    Args:
        user_id: User identifier.
        service: User business service.
        current_user: Authenticated admin caller.

    Returns:
        ResourceResponse: Activated user envelope.
    """
    try:
        return ResourceResponse.model_validate(await service.activate_user(user_id))
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: UUID,
    service: Annotated[UserService, Depends(get_user_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> Response:
    """Delete a user record.

    Args:
        user_id: User identifier.
        service: User business service.
        current_user: Authenticated admin caller.

    Returns:
        Response: Empty HTTP 204 response on success.
    """
    try:
        await service.delete_user(user_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.get("/{user_id}/memberships", response_model=PaginatedResponse)
async def list_user_memberships(
    user_id: UUID,
    service: Annotated[TenantMembershipService, Depends(get_tenant_membership_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse:
    """List tenant memberships for one user.

    Args:
        user_id: User identifier.
        service: Membership business service.
        current_user: Authenticated developer-or-higher caller.
        limit: Maximum rows to return.
        offset: Pagination offset.

    Returns:
        PaginatedResponse: Membership rows and pagination metadata.
    """
    try:
        rows = await service.list_user_memberships(user_id, current_user, limit, offset)
        total = await service.count_user_tenants(user_id, current_user)
        return PaginatedResponse(items=rows, total=total, limit=limit, offset=offset)
    except LLMServiceError as exc:
        translate_management_error(exc)

