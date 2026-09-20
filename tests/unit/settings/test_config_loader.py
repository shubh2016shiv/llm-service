"""Startup and trust-boundary tests for static configuration loading."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.core.settings.loader import ConfigLoader
from app.core.settings.models.environment_config import DeploymentEnvironment

if TYPE_CHECKING:
    from pathlib import Path

_VALID_PROVIDER = """
provider_name: openai
provider_type: rest_api
implementation_class: app.providers.direct.openai_provider.OpenAIProvider
auth:
  mode: none
endpoints:
  base_url: https://api.openai.com/v1
capabilities: [chat]
models: []
"""


def _settings_tree(tmp_path: Path, provider_yaml: str = _VALID_PROVIDER) -> Path:
    """Create the smallest complete static settings tree for one loader test."""
    (tmp_path / "providers").mkdir()
    (tmp_path / "environments").mkdir()
    (tmp_path / "base.yaml").write_text("{}\n", encoding="utf-8")
    (tmp_path / "providers" / "openai.yaml").write_text(provider_yaml, encoding="utf-8")
    return tmp_path


def test_provider_lookup_requires_startup_preload(tmp_path: Path) -> None:
    """Request-path lookup cannot fall back to synchronous filesystem I/O."""
    loader = ConfigLoader(_settings_tree(tmp_path), DeploymentEnvironment.TEST)

    with pytest.raises(KeyError, match="startup-validated catalog"):
        loader.load_provider_config("openai")


def test_load_all_provider_configs_populates_memory_catalog(tmp_path: Path) -> None:
    """Successful startup validation makes later lookup an in-memory operation."""
    loader = ConfigLoader(_settings_tree(tmp_path), DeploymentEnvironment.TEST)

    loaded = loader.load_all_provider_configs()

    assert loaded["openai"] is loader.load_provider_config("openai")


def test_load_all_provider_configs_rejects_filename_identity_mismatch(tmp_path: Path) -> None:
    """A file cannot populate a cache slot belonging to another provider."""
    mismatched = _VALID_PROVIDER.replace("provider_name: openai", "provider_name: anthropic")
    loader = ConfigLoader(_settings_tree(tmp_path, mismatched), DeploymentEnvironment.TEST)

    with pytest.raises(ValueError, match="names must match"):
        loader.load_all_provider_configs()


def test_load_all_provider_configs_fails_on_invalid_provider(tmp_path: Path) -> None:
    """One malformed provider prevents a partially configured process from starting."""
    loader = ConfigLoader(
        _settings_tree(tmp_path, "provider_name: openai\n"), DeploymentEnvironment.TEST
    )

    with pytest.raises(ValueError):
        loader.load_all_provider_configs()


def test_load_all_provider_configs_rejects_unknown_key(tmp_path: Path) -> None:
    """A misspelled YAML key cannot be silently ignored."""
    provider_yaml = _VALID_PROVIDER + "defualt_timeout_seconds: 12\n"
    loader = ConfigLoader(_settings_tree(tmp_path, provider_yaml), DeploymentEnvironment.TEST)

    with pytest.raises(ValueError, match="defualt_timeout_seconds"):
        loader.load_all_provider_configs()


def test_provider_name_rejects_path_traversal(tmp_path: Path) -> None:
    """A provider lookup key can never become an arbitrary filesystem path."""
    loader = ConfigLoader(_settings_tree(tmp_path), DeploymentEnvironment.TEST)
    loader.load_all_provider_configs()

    with pytest.raises(ValueError, match="must match"):
        loader.load_provider_config("../openai")


def test_non_test_environment_requires_explicit_overlay(tmp_path: Path) -> None:
    """Deployable environments cannot silently inherit base-only settings."""
    loader = ConfigLoader(_settings_tree(tmp_path), DeploymentEnvironment.PRODUCTION)

    with pytest.raises(FileNotFoundError, match="explicit overlay"):
        loader.load_global_config()
