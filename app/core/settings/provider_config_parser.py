"""Translate one provider YAML mapping into its frozen runtime contract.

Architecture:
    yaml_source.ConfigMapping -> parse_provider_config -> ProviderStaticConfig

Provider YAML groups request defaults under ``defaults`` for readability,
while the runtime model exposes named fields. This module owns that one shape
translation; file access stays in ``ConfigLoader``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from app.core.settings.models.model_config import LLMModelSpec
from app.core.settings.models.provider_config import ProviderStaticConfig

if TYPE_CHECKING:
    from app.core.settings.yaml_source import ConfigMapping, ConfigValue


def parse_provider_config(raw: ConfigMapping) -> ProviderStaticConfig:
    """Validate and translate a provider document without silently defaulting blocks."""
    defaults = _mapping(raw.get("defaults", {}), "defaults")
    models = _mapping_list(raw.get("models", []), "models")
    payload: dict[str, object] = {
        key: value for key, value in raw.items() if key not in {"defaults", "models"}
    }
    payload.update(
        default_timeout_seconds=defaults.get("timeout_seconds", 60.0),
        default_max_retries=defaults.get("max_retries", 3),
        default_temperature=defaults.get("temperature", 0.7),
        models=tuple(LLMModelSpec.model_validate(model) for model in models),
    )
    return ProviderStaticConfig.model_validate(payload)


def _mapping(value: ConfigValue, field_name: str) -> ConfigMapping:
    """Require a named provider block to be a mapping."""
    if not isinstance(value, dict):
        raise ValueError(f"Provider field {field_name!r} must be a mapping")
    return value


def _mapping_list(value: ConfigValue, field_name: str) -> list[ConfigMapping]:
    """Require a named provider block to be a list of mappings."""
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"Provider field {field_name!r} must be a list of mappings")
    return cast("list[ConfigMapping]", value)
