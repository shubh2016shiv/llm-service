"""Provider-owned model-catalog HTTP endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.exception_handlers import translate_management_error
from app.api.management_dependencies import get_model_catalog_service
from app.auth import AuthTokenPayload, require_admin, require_developer
from app.core.exceptions import LLMServiceError
from app.schemas.management_responses import ModelResponse, PaginatedResponse
from app.schemas.management_schema import (
    ModelCreateRequest,
    ModelUpdateRequest,
)
from app.services import ModelCatalogService

router = APIRouter(prefix="/api/v1/providers", tags=["Model Catalog"])
ModelServiceDependency = Annotated[ModelCatalogService, Depends(get_model_catalog_service)]


@router.post(
    "/{provider_id}/models",
    response_model=ModelResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_model(
    provider_id: UUID,
    body: ModelCreateRequest,
    service: ModelServiceDependency,
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ModelResponse:
    """Register one model under an existing provider."""
    try:
        result = await service.create_model(provider_id, body)
        return ModelResponse.model_validate(result)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.get("/{provider_id}/models", response_model=PaginatedResponse[ModelResponse])
async def list_models(
    provider_id: UUID,
    service: ModelServiceDependency,
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
    active_only: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[ModelResponse]:
    """Return one bounded page of a provider's models."""
    rows = await service.list_models(provider_id, active_only, limit, offset)
    total = await service.count_models(provider_id, active_only)
    return PaginatedResponse[ModelResponse](
        items=[ModelResponse.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{provider_id}/models/{model_id}", response_model=ModelResponse)
async def get_model(
    provider_id: UUID,
    model_id: UUID,
    service: ModelServiceDependency,
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
) -> ModelResponse:
    """Return one provider-owned model."""
    try:
        result = await service.get_model(provider_id, model_id)
        return ModelResponse.model_validate(result)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.patch("/{provider_id}/models/{model_id}", response_model=ModelResponse)
async def update_model(
    provider_id: UUID,
    model_id: UUID,
    body: ModelUpdateRequest,
    service: ModelServiceDependency,
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ModelResponse:
    """Apply supplied fields to one model record."""
    try:
        result = await service.update_model(provider_id, model_id, body)
        return ModelResponse.model_validate(result)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.patch("/{provider_id}/models/{model_id}/activate", response_model=ModelResponse)
async def activate_model(
    provider_id: UUID,
    model_id: UUID,
    service: ModelServiceDependency,
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ModelResponse:
    """Make a model available for deployment selection."""
    try:
        result = await service.activate_model(provider_id, model_id)
        return ModelResponse.model_validate(result)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.patch("/{provider_id}/models/{model_id}/deactivate", response_model=ModelResponse)
async def deactivate_model(
    provider_id: UUID,
    model_id: UUID,
    service: ModelServiceDependency,
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ModelResponse:
    """Prevent new deployment use while retaining historical metadata."""
    try:
        result = await service.deactivate_model(provider_id, model_id)
        return ModelResponse.model_validate(result)
    except LLMServiceError as exc:
        translate_management_error(exc)
