"""
Provider and Model Catalog Router.

Architecture:
-------------
    +----------------------------------+
    ¦ admin/developer caller           ¦
    +----------------------------------+
                   ?
    +----------------------------------+
    ¦ catalog router (`/api/v1/providers`) ¦
    +----------------------------------+
                   ?
    +----------------------------------+
    ¦ ProviderCatalogService /         ¦
    ¦ ModelCatalogService              ¦
    +----------------------------------+
                   ?
    +----------------------------------+
    ¦ catalog persistence              ¦
    +----------------------------------+

Purpose:
    Manage provider definitions and model definitions that tenants can later
    reference in deployments.

Flow rationale:
    Provider/model records are platform-level metadata. Keeping this in a
    dedicated router avoids mixing global catalog management with tenant-scoped
    deployment operations.

Author: Shubham Singh
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.dependencies import (
    get_model_catalog_service,
    get_provider_catalog_service,
)
from app.api.exception_handlers import translate_management_error
from app.auth import AuthTokenPayload, require_admin, require_developer, require_owner
from app.core.exceptions import LLMServiceError
from app.schemas.management_schema import (
    ModelCreateRequest,
    ModelUpdateRequest,
    PaginatedResponse,
    ProviderCreateRequest,
    ProviderUpdateRequest,
    ResourceResponse,
)
from app.services import ModelCatalogService, ProviderCatalogService

router = APIRouter(prefix="/api/v1/providers", tags=["Provider Catalog"])


@router.post("", response_model=ResourceResponse, status_code=status.HTTP_201_CREATED)
async def create_provider(
    body: ProviderCreateRequest,
    service: Annotated[ProviderCatalogService, Depends(get_provider_catalog_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Create a new provider catalog entry.

    Args:
        body: Provider metadata and configuration defaults.
        service: Provider catalog business service.
        current_user: Authenticated admin caller.

    Returns:
        ResourceResponse: Created provider resource envelope.

    Raises:
        HTTPException: Raised indirectly when domain validation fails.
    """
    try:
        return ResourceResponse.model_validate(await service.create_provider(body))
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.get("", response_model=PaginatedResponse)
async def list_providers(
    service: Annotated[ProviderCatalogService, Depends(get_provider_catalog_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
    include_inactive: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse:
    """List provider catalog entries with pagination.

    Args:
        service: Provider catalog business service.
        current_user: Authenticated developer-or-higher caller.
        include_inactive: When true, include deprecated/inactive providers.
        limit: Maximum records to return.
        offset: Pagination offset.

    Returns:
        PaginatedResponse: Provider rows plus total/limit/offset metadata.
    """
    rows = await service.list_providers(include_inactive, limit, offset)
    total = await service.count_providers(include_inactive)
    return PaginatedResponse(items=rows, total=total, limit=limit, offset=offset)


@router.get("/{provider_id}", response_model=ResourceResponse)
async def get_provider(
    provider_id: UUID,
    service: Annotated[ProviderCatalogService, Depends(get_provider_catalog_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
) -> ResourceResponse:
    """Fetch one provider catalog entry by id.

    Args:
        provider_id: Provider identifier.
        service: Provider catalog business service.
        current_user: Authenticated developer-or-higher caller.

    Returns:
        ResourceResponse: Provider resource envelope.

    Raises:
        HTTPException: Raised indirectly if provider does not exist.
    """
    try:
        return ResourceResponse.model_validate(await service.get_provider(provider_id))
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.patch("/{provider_id}", response_model=ResourceResponse)
async def update_provider(
    provider_id: UUID,
    body: ProviderUpdateRequest,
    service: Annotated[ProviderCatalogService, Depends(get_provider_catalog_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Apply partial updates to a provider catalog entry.

    Args:
        provider_id: Provider identifier to update.
        body: Partial provider fields to change.
        service: Provider catalog business service.
        current_user: Authenticated admin caller.

    Returns:
        ResourceResponse: Updated provider envelope.
    """
    try:
        return ResourceResponse.model_validate(await service.update_provider(provider_id, body))
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    provider_id: UUID,
    service: Annotated[ProviderCatalogService, Depends(get_provider_catalog_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_owner)],
) -> Response:
    """Delete a provider catalog entry.

    Owner role is required because deleting provider metadata can impact
    deployment creation and governance behavior platform-wide.

    Args:
        provider_id: Provider identifier to delete.
        service: Provider catalog business service.
        current_user: Authenticated owner caller.

    Returns:
        Response: Empty HTTP 204 response on success.
    """
    try:
        await service.delete_provider(provider_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.post("/{provider_id}/models", response_model=ResourceResponse, status_code=201)
async def create_model(
    provider_id: UUID,
    body: ModelCreateRequest,
    service: Annotated[ModelCatalogService, Depends(get_model_catalog_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Register a model under one provider.

    Args:
        provider_id: Provider owning the model definition.
        body: Model metadata and capability flags.
        service: Model catalog business service.
        current_user: Authenticated admin caller.

    Returns:
        ResourceResponse: Created model resource envelope.
    """
    try:
        return ResourceResponse.model_validate(await service.create_model(provider_id, body))
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.get("/{provider_id}/models", response_model=PaginatedResponse)
async def list_models(
    provider_id: UUID,
    service: Annotated[ModelCatalogService, Depends(get_model_catalog_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
    active_only: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse:
    """List models for one provider with pagination.

    Args:
        provider_id: Provider identifier.
        service: Model catalog business service.
        current_user: Authenticated developer-or-higher caller.
        active_only: When true, hide inactive/deprecated models.
        limit: Maximum records to return.
        offset: Pagination offset.

    Returns:
        PaginatedResponse: Model rows plus pagination metadata.
    """
    rows = await service.list_models(provider_id, active_only, limit, offset)
    total = await service.count_models(provider_id, active_only)
    return PaginatedResponse(items=rows, total=total, limit=limit, offset=offset)


@router.get("/{provider_id}/models/{model_id}", response_model=ResourceResponse)
async def get_model(
    provider_id: UUID,
    model_id: UUID,
    service: Annotated[ModelCatalogService, Depends(get_model_catalog_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
) -> ResourceResponse:
    """Fetch one model owned by a provider.

    Args:
        provider_id: Provider identifier.
        model_id: Model identifier.
        service: Model catalog business service.
        current_user: Authenticated developer-or-higher caller.

    Returns:
        ResourceResponse: Requested model envelope.
    """
    try:
        return ResourceResponse.model_validate(await service.get_model(provider_id, model_id))
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.patch("/{provider_id}/models/{model_id}", response_model=ResourceResponse)
async def update_model(
    provider_id: UUID,
    model_id: UUID,
    body: ModelUpdateRequest,
    service: Annotated[ModelCatalogService, Depends(get_model_catalog_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Apply partial updates to one provider-owned model.

    Args:
        provider_id: Provider identifier.
        model_id: Model identifier.
        body: Partial fields to update.
        service: Model catalog business service.
        current_user: Authenticated admin caller.

    Returns:
        ResourceResponse: Updated model envelope.
    """
    try:
        return ResourceResponse.model_validate(await service.update_model(provider_id, model_id, body))
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.patch("/{provider_id}/models/{model_id}/activate", response_model=ResourceResponse)
async def activate_model(
    provider_id: UUID,
    model_id: UUID,
    service: Annotated[ModelCatalogService, Depends(get_model_catalog_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Mark one provider-owned model as active.

    Activation allows downstream deployment workflows to select this model.

    Args:
        provider_id: Provider identifier.
        model_id: Model identifier.
        service: Model catalog business service.
        current_user: Authenticated admin caller.

    Returns:
        ResourceResponse: Activated model envelope.
    """
    try:
        return ResourceResponse.model_validate(await service.activate_model(provider_id, model_id))
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.patch("/{provider_id}/models/{model_id}/deactivate", response_model=ResourceResponse)
async def deactivate_model(
    provider_id: UUID,
    model_id: UUID,
    service: Annotated[ModelCatalogService, Depends(get_model_catalog_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Mark one provider-owned model as inactive/deprecated.

    Deactivation prevents new deployment use while keeping historical metadata.

    Args:
        provider_id: Provider identifier.
        model_id: Model identifier.
        service: Model catalog business service.
        current_user: Authenticated admin caller.

    Returns:
        ResourceResponse: Deactivated model envelope.
    """
    try:
        return ResourceResponse.model_validate(await service.deactivate_model(provider_id, model_id))
    except LLMServiceError as exc:
        translate_management_error(exc)

