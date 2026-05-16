"""
Provider Route Validation Service
=================================

Validates the resolved provider and model against the static YAML catalog.

Architecture:
-------------
    request_resolution_service.py
        │
        └── provider_route_validation_service.py
                │
                ├── app.core.settings.loader.ConfigLoader
                └── config/providers/*.yaml

Dependencies:
    - app.core.settings.loader — ConfigLoader
    - app.core.settings.models.model_config — model specs and capabilities
    - app.core.settings.models.provider_config — provider static config
    - app.routing.exceptions — operation validation errors

Author: Engineering Team
Last Updated: 2026-05-16
"""

from __future__ import annotations

from app.core.exceptions import ConfigurationError, ModelNotSupportedError
from app.core.settings.loader import ConfigLoader
from app.core.settings.models.model_config import LLMModelSpec, ModelCapability
from app.core.settings.models.provider_config import ProviderStaticConfig
from app.routing.exceptions import OperationNotSupportedError
from app.schemas.enums import OperationType


class ProviderRouteValidationService:
    """Validates provider metadata and model capability using YAML config."""

    def __init__(self, config_loader: ConfigLoader) -> None:
        self._config_loader = config_loader

    def resolve_provider_and_model(
        self,
        provider_name: str,
        model_name: str,
        operation: OperationType,
    ) -> tuple[ProviderStaticConfig, LLMModelSpec]:
        """Load provider static config and validate the selected model."""
        try:
            provider_config = self._config_loader.load_provider_config(provider_name)
        except FileNotFoundError as exc:
            raise ConfigurationError(
                f"Provider config not found for provider {provider_name!r}."
            ) from exc

        model_spec = provider_config.get_model_spec(model_name)
        if model_spec is None:
            raise ModelNotSupportedError(provider_name=provider_name, model_name=model_name)

        capability = self._to_model_capability(operation)
        if not model_spec.supports(capability):
            raise OperationNotSupportedError(
                provider_name=provider_name,
                model_name=model_name,
                operation=operation.value,
            )

        return provider_config, model_spec

    @staticmethod
    def _to_model_capability(operation: OperationType) -> ModelCapability:
        """Map runtime operation types to model capabilities."""
        mapping = {
            OperationType.CHAT: ModelCapability.CHAT,
            OperationType.EMBED: ModelCapability.EMBED,
            OperationType.RERANK: ModelCapability.RERANK,
        }
        capability = mapping.get(operation)
        if capability is None:
            raise OperationNotSupportedError(
                provider_name="unknown",
                model_name="unknown",
                operation=operation.value,
            )
        return capability
