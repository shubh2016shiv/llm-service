from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.dependencies import get_tenant_deployment_service
from app.api.exception_handlers import translate_management_error
from app.auth import AuthTokenPayload, require_admin, require_developer
from app.core.exceptions import LLMServiceError
from app.services import TenantDeploymentService
from app.schemas.management_filters import TenantDeploymentListFilters
from app.schemas.management_schema import (
    DeploymentCreateRequest,
    DeploymentUpdateRequest,
    PaginatedResponse,
    ResourceResponse,
)

router = APIRouter(prefix="/api/v1/tenants", tags=["Tenant Deployments"])
ProviderIdQuery = Annotated[UUID | None, Query()]


@router.post("/{tenant_id}/deployments", response_model=ResourceResponse, status_code=201)
async def create_deployment(
    tenant_id: UUID,
    body: DeploymentCreateRequest,
    service: Annotated[TenantDeploymentService, Depends(get_tenant_deployment_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Create a tenant deployment."""
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
    """List tenant deployments."""
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
    """Retrieve one tenant deployment."""
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
    """Partially update one tenant deployment."""
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
    """Activate one tenant deployment."""
    try:
        row = await service.activate_deployment(tenant_id, deployment_id, current_user)
        return ResourceResponse.model_validate(row)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.patch("/{tenant_id}/deployments/{deployment_id}/maintenance", response_model=ResourceResponse)
async def maintain_deployment(
    tenant_id: UUID,
    deployment_id: UUID,
    service: Annotated[TenantDeploymentService, Depends(get_tenant_deployment_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Move one tenant deployment to maintenance."""
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
    """Delete one tenant deployment."""
    try:
        await service.delete_deployment(tenant_id, deployment_id, current_user)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except LLMServiceError as exc:
        translate_management_error(exc)
