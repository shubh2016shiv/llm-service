"""
Provider Route Validator
========================

Validates provider and model against static configuration and ensures the
requested operation is supported by the resolved model.

Enterprise Pattern: Contract Validation Pattern
    Resolution is accepted only when catalog contracts are satisfied.

Architecture rationale:
    Static provider/model configuration defines hard capabilities. This module
    is the gate that prevents runtime routes from selecting combinations that
    cannot actually serve the requested operation.

Author: Shubham Singh
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.exceptions import ConfigurationError, ModelNotSupportedError
from app.core.settings.models.model_config import LLMModelSpec, ModelCapability
from app.inference_routing.exceptions import OperationNotSupportedError
from app.schemas.enums import OperationType

if TYPE_CHECKING:
    from app.core.settings.loader import ConfigLoader
    from app.core.settings.models.provider_config import ProviderStaticConfig


class ProviderRouteValidator:
    """Validate provider/model route against static catalog contracts.

    This resolver does not inspect tenant policy or deployment status. It only
    answers: "Is this provider/model combination capable of this operation?"
    """

    def __init__(self, config_loader: ConfigLoader) -> None:
        self._config_loader = config_loader

    def resolve_provider_and_model(
        self,
        provider_name: str,
        model_name: str,
        operation: OperationType,
    ) -> tuple[ProviderStaticConfig, LLMModelSpec]:
        """Resolve provider config and validate model + operation capability.

        Step-by-step:
            1. Load provider static YAML config by provider name.
            2. Resolve model spec from provider catalog.
            3. Map runtime operation to required model capability.
            4. Verify model supports capability.
            5. Return provider config and model spec.
        """
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
        """Translate runtime operation enum into model capability enum.

        Rationale:
            Routing receives API-level operation types, while model specs use
            capability enums. This method is the explicit mapping boundary.
        """
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

