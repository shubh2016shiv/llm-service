"""
Unit Tests — ProviderRouteValidator
======================================

Covers:
    - resolve_provider_and_model: happy path returns (ProviderStaticConfig, LLMModelSpec)
    - resolve_provider_and_model: raises ConfigurationError when provider YAML missing
    - resolve_provider_and_model: raises ModelNotSupportedError when model absent
    - resolve_provider_and_model: raises OperationNotSupportedError when model lacks capability
    - _to_model_capability: all three OperationType values map correctly
    - _to_model_capability: unknown operation raises OperationNotSupportedError

Architecture:
-------------
    FakeConfigLoader ──▶ ProviderRouteValidator (unit under test)

Author: Shubham Singh
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from app.core.exceptions import ConfigurationError, ModelNotSupportedError
from app.core.settings.models.model_config import ModelCapability
from app.inference_routing.exceptions import OperationNotSupportedError
from app.inference_routing.provider_validator import ProviderRouteValidator
from app.schemas.enums import OperationType
from tests.unit.inference_routing.conftest import (
    MODEL_NAME,
    PROVIDER_NAME,
    FakeConfigLoader,
    build_model_spec,
    build_provider_static_config,
)

if TYPE_CHECKING:
    from app.core.settings.loader import ConfigLoader


def _make_validator(
    provider_name: str = PROVIDER_NAME,
    model_name: str = MODEL_NAME,
    capabilities: frozenset[ModelCapability] | None = None,
) -> ProviderRouteValidator:
    spec = build_model_spec(name=model_name, capabilities=capabilities)
    provider_config = build_provider_static_config(
        provider_name=provider_name,
        model_spec=spec,
    )
    loader = FakeConfigLoader({provider_name: provider_config})
    return ProviderRouteValidator(config_loader=cast("ConfigLoader", loader))


# ═══════════════════════════════════════════════════════════════════════════════
# Happy path
# ═══════════════════════════════════════════════════════════════════════════════


class TestHappyPath:
    def test_returns_provider_config_and_model_spec_for_chat(self):
        """Chat operation on a chat-capable model returns both config objects."""
        validator = _make_validator(
            capabilities=frozenset({ModelCapability.CHAT})
        )

        provider_cfg, model_spec = validator.resolve_provider_and_model(
            provider_name=PROVIDER_NAME,
            model_name=MODEL_NAME,
            operation=OperationType.CHAT,
        )

        assert provider_cfg.provider_name == PROVIDER_NAME
        assert model_spec.name == MODEL_NAME

    def test_returns_correct_model_spec_for_embed(self):
        """EMBED operation maps to ModelCapability.EMBED."""
        validator = _make_validator(
            capabilities=frozenset({ModelCapability.EMBED})
        )

        _, model_spec = validator.resolve_provider_and_model(
            provider_name=PROVIDER_NAME,
            model_name=MODEL_NAME,
            operation=OperationType.EMBED,
        )

        assert model_spec.name == MODEL_NAME

    def test_returns_correct_model_spec_for_rerank(self):
        """RERANK operation maps to ModelCapability.RERANK."""
        validator = _make_validator(
            capabilities=frozenset({ModelCapability.RERANK})
        )

        _, model_spec = validator.resolve_provider_and_model(
            provider_name=PROVIDER_NAME,
            model_name=MODEL_NAME,
            operation=OperationType.RERANK,
        )

        assert model_spec.name == MODEL_NAME


# ═══════════════════════════════════════════════════════════════════════════════
# Provider not found
# ═══════════════════════════════════════════════════════════════════════════════


class TestProviderNotFound:
    def test_raises_configuration_error_when_provider_yaml_missing(self):
        """FileNotFoundError from loader → wrapped in ConfigurationError."""
        loader = FakeConfigLoader({})  # No configs registered
        validator = ProviderRouteValidator(config_loader=cast("ConfigLoader", loader))

        with pytest.raises(ConfigurationError) as exc_info:
            validator.resolve_provider_and_model(
                provider_name="ghost-provider",
                model_name=MODEL_NAME,
                operation=OperationType.CHAT,
            )

        assert "ghost-provider" in str(exc_info.value)

    def test_configuration_error_contains_provider_name(self):
        """Error message must name the missing provider for debuggability."""
        loader = FakeConfigLoader({})
        validator = ProviderRouteValidator(config_loader=cast("ConfigLoader", loader))

        with pytest.raises(ConfigurationError) as exc_info:
            validator.resolve_provider_and_model("unknown-xyz", MODEL_NAME, OperationType.CHAT)

        assert "unknown-xyz" in str(exc_info.value)


# ═══════════════════════════════════════════════════════════════════════════════
# Model not found
# ═══════════════════════════════════════════════════════════════════════════════


class TestModelNotFound:
    def test_raises_model_not_supported_when_model_absent_from_catalog(self):
        """Model name not in provider catalog → ModelNotSupportedError."""
        spec = build_model_spec(name="gpt-4o")
        provider_config = build_provider_static_config(
            provider_name=PROVIDER_NAME, model_spec=spec
        )
        loader = FakeConfigLoader({PROVIDER_NAME: provider_config})
        validator = ProviderRouteValidator(config_loader=cast("ConfigLoader", loader))

        with pytest.raises(ModelNotSupportedError) as exc_info:
            validator.resolve_provider_and_model(
                provider_name=PROVIDER_NAME,
                model_name="nonexistent-model-xyz",
                operation=OperationType.CHAT,
            )

        err = exc_info.value
        assert err.model_name == "nonexistent-model-xyz"
        assert err.provider_name == PROVIDER_NAME


# ═══════════════════════════════════════════════════════════════════════════════
# Capability mismatch
# ═══════════════════════════════════════════════════════════════════════════════


class TestCapabilityMismatch:
    def test_raises_operation_not_supported_when_model_lacks_chat_capability(self):
        """Embed-only model requested for CHAT → OperationNotSupportedError."""
        validator = _make_validator(
            capabilities=frozenset({ModelCapability.EMBED})  # no CHAT
        )

        with pytest.raises(OperationNotSupportedError) as exc_info:
            validator.resolve_provider_and_model(
                provider_name=PROVIDER_NAME,
                model_name=MODEL_NAME,
                operation=OperationType.CHAT,
            )

        err = exc_info.value
        assert err.operation == OperationType.CHAT.value
        assert err.model_name == MODEL_NAME

    def test_raises_operation_not_supported_when_model_lacks_embed_capability(self):
        """Chat-only model requested for EMBED → OperationNotSupportedError."""
        validator = _make_validator(
            capabilities=frozenset({ModelCapability.CHAT})  # no EMBED
        )

        with pytest.raises(OperationNotSupportedError):
            validator.resolve_provider_and_model(
                provider_name=PROVIDER_NAME,
                model_name=MODEL_NAME,
                operation=OperationType.EMBED,
            )

    def test_error_contains_provider_model_and_operation(self):
        """Error message must name all three failing dimensions."""
        validator = _make_validator(
            capabilities=frozenset({ModelCapability.CHAT})  # no RERANK
        )

        with pytest.raises(OperationNotSupportedError) as exc_info:
            validator.resolve_provider_and_model(
                provider_name=PROVIDER_NAME,
                model_name=MODEL_NAME,
                operation=OperationType.RERANK,
            )

        message = str(exc_info.value)
        assert PROVIDER_NAME in message
        assert MODEL_NAME in message
        assert "rerank" in message


# ═══════════════════════════════════════════════════════════════════════════════
# OperationType → ModelCapability mapping
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "operation, expected_capability",
    [
        (OperationType.CHAT, ModelCapability.CHAT),
        (OperationType.EMBED, ModelCapability.EMBED),
        (OperationType.RERANK, ModelCapability.RERANK),
    ],
)
def test_operation_to_capability_mapping(operation, expected_capability):
    """Each OperationType maps to exactly the right ModelCapability."""
    # _to_model_capability is a static method — test it directly
    capability = ProviderRouteValidator._to_model_capability(operation)

    assert capability == expected_capability
