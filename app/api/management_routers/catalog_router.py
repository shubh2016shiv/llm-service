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
from app.services import ModelCatalogService, ProviderCatalogService
from app.schemas.management_schema import (
    ModelCreateRequest,
    ModelUpdateRequest,
    PaginatedResponse,
    ProviderCreateRequest,
    ProviderUpdateRequest,
    ResourceResponse,
)

router = APIRouter(prefix="/api/v1/providers", tags=["Provider Catalog"])


@router.post("", response_model=ResourceResponse, status_code=status.HTTP_201_CREATED)
async def create_provider(
    body: ProviderCreateRequest,
    service: Annotated[ProviderCatalogService, Depends(get_provider_catalog_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Register a provider catalog entry."""
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
    """List provider catalog entries."""
    rows = await service.list_providers(include_inactive, limit, offset)
    total = await service.count_providers(include_inactive)
    return PaginatedResponse(items=rows, total=total, limit=limit, offset=offset)


@router.get("/{provider_id}", response_model=ResourceResponse)
async def get_provider(
    provider_id: UUID,
    service: Annotated[ProviderCatalogService, Depends(get_provider_catalog_service)],
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
) -> ResourceResponse:
    """Retrieve one provider catalog entry."""
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
    """Partially update a provider catalog entry."""
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
    """Delete a provider catalog entry."""
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
    """Register a model under a provider."""
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
    """List models registered under a provider."""
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
    """Retrieve one provider-owned model."""
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
    """Partially update a provider-owned model."""
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
    """Activate a provider-owned model."""
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
    """Deactivate a provider-owned model by marking it deprecated."""
    try:
        return ResourceResponse.model_validate(await service.deactivate_model(provider_id, model_id))
    except LLMServiceError as exc:
        translate_management_error(exc)
