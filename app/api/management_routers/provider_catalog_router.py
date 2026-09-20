"""Provider-catalog HTTP endpoints.

Provider records describe platform-level integrations. Model records are a
separate child resource and live in ``model_catalog_router``. Keeping those
resources in different modules gives a new developer one predictable place to
look when changing provider behavior versus model behavior.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.exception_handlers import translate_management_error
from app.api.management_dependencies import get_provider_catalog_service
from app.api.shared_dependencies import get_config_loader
from app.auth import AuthTokenPayload, require_admin, require_developer, require_owner
from app.core.exceptions import LLMServiceError
from app.core.settings.loader import ConfigLoader
from app.schemas.management_schema import (
    PaginatedResponse,
    ProviderCreateRequest,
    ProviderTemplateListResponse,
    ProviderUpdateRequest,
    ResourceResponse,
)
from app.services import ProviderCatalogService
from app.services.catalog.provider_templates import build_provider_templates

router = APIRouter(prefix="/api/v1/providers", tags=["Provider Catalog"])
ProviderServiceDependency = Annotated[
    ProviderCatalogService,
    Depends(get_provider_catalog_service),
]


# Static routes must be declared before /{provider_id}. Otherwise FastAPI may
# try to parse "runtime-templates" as a UUID for the dynamic route.
@router.get("/runtime-templates", response_model=ProviderTemplateListResponse)
async def list_provider_runtime_templates(
    config_loader: Annotated[ConfigLoader, Depends(get_config_loader)],
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
) -> ProviderTemplateListResponse:
    """Expose non-secret runtime templates used by provider creation forms."""
    return ProviderTemplateListResponse(items=build_provider_templates(config_loader))


@router.post("", response_model=ResourceResponse, status_code=status.HTTP_201_CREATED)
async def create_provider(
    body: ProviderCreateRequest,
    service: ProviderServiceDependency,
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Create one provider catalog record."""
    try:
        return ResourceResponse.model_validate(await service.create_provider(body))
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.get("", response_model=PaginatedResponse)
async def list_providers(
    service: ProviderServiceDependency,
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
    include_inactive: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse:
    """Return one bounded provider page."""
    rows = await service.list_providers(include_inactive, limit, offset)
    total = await service.count_providers(include_inactive)
    return PaginatedResponse(items=rows, total=total, limit=limit, offset=offset)


@router.get("/{provider_id}", response_model=ResourceResponse)
async def get_provider(
    provider_id: UUID,
    service: ProviderServiceDependency,
    current_user: Annotated[AuthTokenPayload, Depends(require_developer)],
) -> ResourceResponse:
    """Return one provider catalog record."""
    try:
        return ResourceResponse.model_validate(await service.get_provider(provider_id))
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.patch("/{provider_id}", response_model=ResourceResponse)
async def update_provider(
    provider_id: UUID,
    body: ProviderUpdateRequest,
    service: ProviderServiceDependency,
    current_user: Annotated[AuthTokenPayload, Depends(require_admin)],
) -> ResourceResponse:
    """Apply supplied fields to one provider record."""
    try:
        result = await service.update_provider(provider_id, body)
        return ResourceResponse.model_validate(result)
    except LLMServiceError as exc:
        translate_management_error(exc)


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    provider_id: UUID,
    service: ProviderServiceDependency,
    current_user: Annotated[AuthTokenPayload, Depends(require_owner)],
) -> Response:
    """Delete an unused provider; platform-owner authority is required."""
    try:
        await service.delete_provider(provider_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except LLMServiceError as exc:
        translate_management_error(exc)
