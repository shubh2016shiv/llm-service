"""Safe YAML reading and deterministic environment-overlay merging.

Architecture:
    YAML files -> load_yaml_mapping -> merge_mappings -> ConfigLoader

This module knows about files and generic mapping structure only. Pydantic
model selection and provider-specific meaning belong to neighboring modules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import yaml

if TYPE_CHECKING:
    from pathlib import Path

type ConfigScalar = str | int | float | bool | None
type ConfigValue = ConfigScalar | list["ConfigValue"] | dict[str, "ConfigValue"]
type ConfigMapping = dict[str, ConfigValue]


def load_yaml_mapping(path: Path) -> ConfigMapping:
    """Read one YAML document and require a string-keyed top-level mapping.

    Empty files and scalar/list documents are rejected. Treating them as an
    empty configuration would replace a useful startup error with confusing
    missing-field errors later.
    """
    if not path.is_file():
        raise FileNotFoundError(
            f"Configuration file not found: {path}. "
            "Check CONFIG_DIR and the deployment's mounted files."
        )
    raw_document: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    _validate_value(raw_document, location=str(path), require_mapping=True)
    return cast("ConfigMapping", raw_document)


def merge_mappings(base: ConfigMapping, override: ConfigMapping) -> ConfigMapping:
    """Deep-merge an environment overlay without modifying either input."""
    merged: ConfigMapping = dict(base)
    for key, override_value in override.items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(override_value, dict):
            merged[key] = merge_mappings(base_value, override_value)
        else:
            merged[key] = override_value
    return merged


def _validate_value(value: object, *, location: str, require_mapping: bool = False) -> None:
    """Reject YAML shapes that cannot belong to the typed configuration tree."""
    if require_mapping and not isinstance(value, dict):
        raise ValueError(f"Configuration document {location} must contain a mapping")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError(f"Configuration key at {location} must be text, got {key!r}")
            _validate_value(child, location=f"{location}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate_value(child, location=f"{location}[{index}]")
        return
    if value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError(f"Unsupported configuration value at {location}: {type(value).__name__}")
