"""
User Management Router.

Architecture:
-------------
    ┌───────────────────────────────┐
    │ admin/developer caller        │
    └───────────────┬───────────────┘
                     ▼
    ┌───────────────────────────────┐
    │ user router (`/api/v1/users`) │
    └───────────────┬───────────────┘
            ┌────────┴────────┐
            ▼                 ▼
    ┌───────────────┐  ┌─────────────────────────┐
    │ UserService   │  │ TenantMembershipService │
    └───────┬───────┘  └────────────┬────────────┘
            ▼                       ▼
    ┌───────────────────┐  ┌──────────────────────┐
    │ user persistence   │  │ membership storage   │
    └───────────────────┘  └──────────────────────┘

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

from app.api.exception_handlers import translate_management_error
from app.api.management_dependencies import (
    get_tenant_membership_service,
    get_user_service,
)
from app.auth import AuthTokenPayload, require_admin, require_developer
from app.core.exceptions import LLMServiceError
from app.schemas.auth_schema import UserRole
from app.schemas.enums import UserAccountStatus
from app.schemas.management_filters import UserListFilters
from app.schemas.management_responses import MembershipResponse, PaginatedResponse, UserResponse
from app.schemas.management_schema import (
    UserCreateRequest,
    UserUpdateRequest,
)
from app.services import TenantMembershipService, UserService

router = APIRouter(prefix="/api/v1/users", tags=["User Management"])


# Stage 8:1 - Check administrator access, hash the password, save the user, and return safe fields.
@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreateRequest,
    service: Annotated[UserService, Depends(get_user_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> UserResponse:
    """Create a platform user record.

    Args:
        body: User creation payload.
        service: User business service.
        current_user: Authenticated admin caller.

    Returns:
        UserResponse: Created user projection.
    """
    try:
        return UserResponse.model_validate(await service.create_user(body))
    except LLMServiceError as exc:
        translate_management_error(exc)


# Stage 8:2 - Check administrator access, list matching users, and return safe fields and totals.
@router.get("", response_model=PaginatedResponse[UserResponse])
async def list_users(
    service: Annotated[UserService, Depends(get_user_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
    platform_role_filter: Annotated[UserRole | None, Query()] = None,
    status_filter: Annotated[UserAccountStatus | None, Query()] = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[UserResponse]:
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
    filters = UserListFilters(platform_role=platform_role_filter, status=status_filter)
    rows = await service.list_users(filters, limit, offset)
    total = await service.count_users(filters)
    return PaginatedResponse[UserResponse](
        items=[UserResponse.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


# Stage 8:3 - Check caller access, find a user by email, and return safe fields or not-found.
@router.get("/email/{email}", response_model=UserResponse)
async def get_user_by_email(
    email: str,
    service: Annotated[UserService, Depends(get_user_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
) -> UserResponse:
    """Fetch one user by email address.

    Args:
        email: Target email to look up.
        service: User business service.
        current_user: Authenticated developer-or-higher caller.

    Returns:
        UserResponse: Requested user projection.
    """
    try:
        return UserResponse.model_validate(await service.get_user_by_email(email))
    except LLMServiceError as exc:
        translate_management_error(exc)


# Stage 8:4 - Check caller access, find a user by ID, and return safe fields or not-found.
@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: UUID,
    service: Annotated[UserService, Depends(get_user_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
) -> UserResponse:
    """Fetch one user by UUID.

    Args:
        user_id: User identifier.
        service: User business service.
        current_user: Authenticated developer-or-higher caller.

    Returns:
        UserResponse: Requested user projection.
    """
    try:
        return UserResponse.model_validate(await service.get_user(user_id))
    except LLMServiceError as exc:
        translate_management_error(exc)


# Stage 8:5 - Check administrator access, update supplied user fields, and return safe fields.
@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    body: UserUpdateRequest,
    service: Annotated[UserService, Depends(get_user_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> UserResponse:
    """Apply partial updates to one user.

    Args:
        user_id: User identifier.
        body: Partial user updates.
        service: User business service.
        current_user: Authenticated admin caller.

    Returns:
        UserResponse: Updated user projection.
    """
    try:
        return UserResponse.model_validate(await service.update_user(user_id, body))
    except LLMServiceError as exc:
        translate_management_error(exc)


# Stage 8:6 - Check administrator access, suspend the account, and return its new state.
@router.patch("/{user_id}/suspend", response_model=UserResponse)
async def suspend_user(
    user_id: UUID,
    service: Annotated[UserService, Depends(get_user_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> UserResponse:
    """Suspend a user account.

    Args:
        user_id: User identifier.
        service: User business service.
        current_user: Authenticated admin caller.

    Returns:
        UserResponse: Suspended user projection.
    """
    try:
        return UserResponse.model_validate(await service.suspend_user(user_id))
    except LLMServiceError as exc:
        translate_management_error(exc)


# Stage 8:7 - Check administrator access, activate the account, and return its new state.
@router.patch("/{user_id}/activate", response_model=UserResponse)
async def activate_user(
    user_id: UUID,
    service: Annotated[UserService, Depends(get_user_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> UserResponse:
    """Activate a suspended/inactive user account.

    Args:
        user_id: User identifier.
        service: User business service.
        current_user: Authenticated admin caller.

    Returns:
        UserResponse: Activated user projection.
    """
    try:
        return UserResponse.model_validate(await service.activate_user(user_id))
    except LLMServiceError as exc:
        translate_management_error(exc)


# Stage 8:8 - Check administrator access, delete the user, and return an empty success response.
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


# Stage 8:9 - Check user-or-administrator access and list the tenants that user belongs to.
@router.get("/{user_id}/memberships", response_model=PaginatedResponse[MembershipResponse])
async def list_user_memberships(
    user_id: UUID,
    service: Annotated[TenantMembershipService, Depends(get_tenant_membership_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[MembershipResponse]:
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
        return PaginatedResponse[MembershipResponse](
            items=[MembershipResponse.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )
    except LLMServiceError as exc:
        translate_management_error(exc)
