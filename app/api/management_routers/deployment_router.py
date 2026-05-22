"""
Tenant Deployment Router.

Architecture:
-------------
    +------------------------------+
    ¦ tenant admin/developer caller¦
    +------------------------------+
                   ?
    +------------------------------+
    ¦ deployment router            ¦
    ¦ (`/api/v1/tenants/*`)        ¦
    +------------------------------+
                   ?
    +------------------------------+
    ¦ TenantDeploymentService      ¦
    ¦ access + validation + state  ¦
    +------------------------------+
                   ?
    +------------------------------+
    ¦ deployment persistence       ¦
    +------------------------------+

Purpose:
    Manage deployment definitions for each tenant, including lifecycle
    transitions like activate and maintenance.

Rationale:
    Deployment lifecycle is security and traffic critical. Keeping explicit
    endpoints for state transitions makes audit and policy enforcement clearer
    than generic status mutation fields.

Author: Shubham Singh
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.dependencies import get_tenant_deployment_service
from app.api.exception_handlers import translate_management_error
from app.auth import AuthTokenPayload, require_admin, require_developer
from app.core.exceptions import LLMServiceError
from app.schemas.management_filters import TenantDeploymentListFilters
from app.schemas.management_schema import (
    DeploymentCreateRequest,
    DeploymentUpdateRequest,
    PaginatedResponse,
    ResourceResponse,
)
from app.services import TenantDeploymentService

router = APIRouter(prefix="/api/v1/tenants", tags=["Tenant Deployments"])
ProviderIdQuery = Annotated[UUID | None, Query()]


@router.post("/{tenant_id}/deployments", response_model=ResourceResponse, status_code=201)
async def create_deployment(
    tenant_id: UUID,
    body: DeploymentCreateRequest,
    service: Annotated[TenantDeploymentService, Depends(get_tenant_deployment_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Create a deployment under one tenant.

    Args:
        tenant_id: Tenant that will own the deployment.
        body: Deployment config including provider/model bindings.
        service: Deployment business service.
        current_user: Authenticated admin caller.

    Returns:
        ResourceResponse: Created deployment envelope.
    """
    try:
        row = await service.create_deployment(tenant_id, body, current_user)
        return ResourceResponse.model_validate(row)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.get("/{tenant_id}/deployments", response_model=PaginatedResponse)
async def list_deployments(
    tenant_id: UUID,
    service: Annotated[TenantDeploymentService, Depends(get_tenant_deployment_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
    provider_id: ProviderIdQuery = None,
    active_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse:
    """List deployments for one tenant with filtering and pagination.

    Args:
        tenant_id: Tenant identifier.
        service: Deployment business service.
        current_user: Authenticated developer-or-higher caller.
        provider_id: Optional provider filter.
        active_only: Include only active deployments when true.
        limit: Maximum rows to return.
        offset: Pagination offset.

    Returns:
        PaginatedResponse: Deployment rows and pagination metadata.
    """
    try:
        filters = TenantDeploymentListFilters(provider_id=provider_id, active_only=active_only)
        rows = await service.list_deployments(tenant_id, current_user, filters, limit, offset)
        total = await service.count_deployments(tenant_id, filters)
        return PaginatedResponse(items=rows, total=total, limit=limit, offset=offset)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.get("/{tenant_id}/deployments/{deployment_id}", response_model=ResourceResponse)
async def get_deployment(
    tenant_id: UUID,
    deployment_id: UUID,
    service: Annotated[TenantDeploymentService, Depends(get_tenant_deployment_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
) -> ResourceResponse:
    """Fetch one deployment by id within tenant scope.

    Args:
        tenant_id: Tenant identifier.
        deployment_id: Deployment identifier.
        service: Deployment business service.
        current_user: Authenticated developer-or-higher caller.

    Returns:
        ResourceResponse: Requested deployment envelope.
    """
    try:
        row = await service.get_deployment(tenant_id, deployment_id, current_user)
        return ResourceResponse.model_validate(row)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.patch("/{tenant_id}/deployments/{deployment_id}", response_model=ResourceResponse)
async def update_deployment(
    tenant_id: UUID,
    deployment_id: UUID,
    body: DeploymentUpdateRequest,
    service: Annotated[TenantDeploymentService, Depends(get_tenant_deployment_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Apply partial updates to one deployment.

    Args:
        tenant_id: Tenant identifier.
        deployment_id: Deployment identifier.
        body: Partial deployment updates.
        service: Deployment business service.
        current_user: Authenticated admin caller.

    Returns:
        ResourceResponse: Updated deployment envelope.
    """
    try:
        row = await service.update_deployment(tenant_id, deployment_id, body, current_user)
        return ResourceResponse.model_validate(row)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.patch("/{tenant_id}/deployments/{deployment_id}/activate", response_model=ResourceResponse)
async def activate_deployment(
    tenant_id: UUID,
    deployment_id: UUID,
    service: Annotated[TenantDeploymentService, Depends(get_tenant_deployment_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Move one deployment into active serving state.

    Args:
        tenant_id: Tenant identifier.
        deployment_id: Deployment identifier.
        service: Deployment business service.
        current_user: Authenticated admin caller.

    Returns:
        ResourceResponse: Activated deployment envelope.
    """
    try:
        row = await service.activate_deployment(tenant_id, deployment_id, current_user)
        return ResourceResponse.model_validate(row)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.patch(
    "/{tenant_id}/deployments/{deployment_id}/maintenance", response_model=ResourceResponse
)
async def maintain_deployment(
    tenant_id: UUID,
    deployment_id: UUID,
    service: Annotated[TenantDeploymentService, Depends(get_tenant_deployment_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Move one deployment to maintenance state.

    Maintenance mode is useful when provider credentials, model routing, or
    backend quotas are being adjusted and traffic should be temporarily paused.

    Args:
        tenant_id: Tenant identifier.
        deployment_id: Deployment identifier.
        service: Deployment business service.
        current_user: Authenticated admin caller.

    Returns:
        ResourceResponse: Deployment envelope in maintenance state.
    """
    try:
        row = await service.set_maintenance(tenant_id, deployment_id, current_user)
        return ResourceResponse.model_validate(row)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.delete("/{tenant_id}/deployments/{deployment_id}", status_code=204)
async def delete_deployment(
    tenant_id: UUID,
    deployment_id: UUID,
    service: Annotated[TenantDeploymentService, Depends(get_tenant_deployment_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> Response:
    """Delete one deployment owned by a tenant.

    Args:
        tenant_id: Tenant identifier.
        deployment_id: Deployment identifier.
        service: Deployment business service.
        current_user: Authenticated admin caller.

    Returns:
        Response: Empty HTTP 204 response on success.
    """
    try:
        await service.delete_deployment(tenant_id, deployment_id, current_user)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except LLMServiceError as exc:
        translate_management_error(exc)

