"""Service tests that keep database catalog entries routable at runtime."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from app.core.exceptions import ManagementValidationError
from app.schemas.enums import ProviderCatalogAuthMode, ProviderCatalogType
from app.schemas.management_schema import ProviderCreateRequest
from app.services.catalog.provider_catalog import ProviderCatalogService

if TYPE_CHECKING:
    from app.core.settings.loader import ConfigLoader
    from app.core.settings.models.provider_config import ProviderStaticConfig
    from app.database import ProviderCatalogPersistence


class RuntimeCatalog:
    """Return one startup-validated provider config to the service."""

    def load_provider_config(self, provider_name: str) -> ProviderStaticConfig:
        config = SimpleNamespace(
            auth=SimpleNamespace(mode=SimpleNamespace(value="bearer_token")),
            capabilities=(SimpleNamespace(value="chat"),),
        )
        return cast("ProviderStaticConfig", config)


class RecordingProviderPersistence:
    """Detect whether invalid catalog input reaches PostgreSQL."""

    def __init__(self) -> None:
        self.create_called = False

    async def create_provider(self, **fields: object) -> dict[str, object]:
        self.create_called = True
        return fields


@pytest.mark.asyncio
async def test_create_provider_mismatched_runtime_auth_rejects_before_database() -> None:
    """Catalog auth cannot drift from the adapter configuration used in inference."""
    persistence = RecordingProviderPersistence()
    service = ProviderCatalogService(
        cast("ProviderCatalogPersistence", persistence),
        cast("ConfigLoader", RuntimeCatalog()),
    )
    request = ProviderCreateRequest(
        provider_name="openai",
        display_name="OpenAI",
        provider_type=ProviderCatalogType.DIRECT_API,
        auth_mode=ProviderCatalogAuthMode.API_KEY_HEADER,
        supported_operations=["chat"],
    )

    with pytest.raises(ManagementValidationError, match="must match runtime"):
        await service.create_provider(request)

    assert persistence.create_called is False


@pytest.mark.asyncio
async def test_create_provider_unsupported_operation_rejects_before_database() -> None:
    """A catalog entry cannot advertise an operation its adapter cannot execute."""
    persistence = RecordingProviderPersistence()
    service = ProviderCatalogService(
        cast("ProviderCatalogPersistence", persistence),
        cast("ConfigLoader", RuntimeCatalog()),
    )
    request = ProviderCreateRequest(
        provider_name="openai",
        display_name="OpenAI",
        auth_mode=ProviderCatalogAuthMode.BEARER_TOKEN,
        supported_operations=["chat", "rerank"],
    )

    with pytest.raises(ManagementValidationError, match="rerank"):
        await service.create_provider(request)

    assert persistence.create_called is False
