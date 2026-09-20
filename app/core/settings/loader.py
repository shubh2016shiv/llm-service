"""Load and validate immutable YAML configuration at process startup.

Architecture:
    config/*.yaml -> yaml_source -> ConfigLoader -> frozen Pydantic models
                                      |
                                      +-> provider_config_parser

The loader fails fast for required global/provider configuration. A process
with a partially loaded provider catalog is not ready to serve traffic.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from app.core.settings.models.cloud_config import (
    AnyCloudConfig,
    AWSCloudConfig,
    AzureCloudConfig,
    CloudVendor,
    GCPCloudConfig,
)
from app.core.settings.models.environment_config import DeploymentEnvironment
from app.core.settings.models.global_config import GlobalConfig
from app.core.settings.provider_config_parser import parse_provider_config
from app.core.settings.yaml_source import load_yaml_mapping, merge_mappings

if TYPE_CHECKING:
    from pathlib import Path

    from app.core.settings.models.provider_config import ProviderStaticConfig

logger = logging.getLogger(__name__)

_SAFE_CONFIG_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_CLOUD_CONFIG_MODELS: dict[
    CloudVendor,
    type[AWSCloudConfig] | type[AzureCloudConfig] | type[GCPCloudConfig],
] = {
    CloudVendor.AWS: AWSCloudConfig,
    CloudVendor.AZURE: AzureCloudConfig,
    CloudVendor.GCP: GCPCloudConfig,
}


class ConfigLoader:
    """Read static configuration once and retain validated immutable models.

    Construct this at startup and call ``load_all_provider_configs`` before
    accepting traffic. Later per-request lookups are in-memory dictionary reads,
    never synchronous filesystem operations.
    """

    def __init__(
        self,
        config_dir: Path,
        environment: DeploymentEnvironment,
    ) -> None:
        """Validate the configuration root and selected overlay name."""
        if not config_dir.is_dir():
            raise FileNotFoundError(
                f"Configuration directory not found: {config_dir}. Check CONFIG_DIR."
            )
        self._config_dir = config_dir
        self._environment = environment
        self._provider_configs: dict[str, ProviderStaticConfig] = {}

    def load_global_config(self) -> GlobalConfig:
        """Merge base configuration with the selected environment overlay."""
        base = load_yaml_mapping(self._config_dir / "base.yaml")
        overlay_path = self._config_dir / "environments" / f"{self._environment.value}.yaml"
        if not overlay_path.is_file():
            if self._environment is not DeploymentEnvironment.TEST:
                raise FileNotFoundError(
                    f"Environment overlay not found: {overlay_path}. "
                    "Every deployable environment requires an explicit overlay."
                )
            logger.info("Test environment has no YAML overlay; using base configuration")
            return GlobalConfig.model_validate(base)
        return GlobalConfig.model_validate(merge_mappings(base, load_yaml_mapping(overlay_path)))

    def load_all_provider_configs(self) -> dict[str, ProviderStaticConfig]:
        """Load every provider file, failing startup if any file is invalid."""
        providers_dir = self._config_dir / "providers"
        if not providers_dir.is_dir():
            raise FileNotFoundError(f"Provider configuration directory not found: {providers_dir}")
        provider_files = sorted(providers_dir.glob("*.yaml"))
        if not provider_files:
            raise ValueError(f"No provider configuration files found in {providers_dir}")
        loaded = {
            provider_file.stem: self._load_provider_file(provider_file.stem)
            for provider_file in provider_files
        }
        self._provider_configs = loaded
        logger.info("Provider catalog loaded", extra={"provider_count": len(loaded)})
        return dict(loaded)

    def load_provider_config(self, provider_name: str) -> ProviderStaticConfig:
        """Return one preloaded provider; refuse request-path disk access."""
        _validate_config_name(provider_name, "provider_name")
        try:
            return self._provider_configs[provider_name]
        except KeyError as exc:
            raise KeyError(
                f"Provider {provider_name!r} is not in the startup-validated catalog"
            ) from exc

    def load_cloud_config(self, vendor: CloudVendor) -> AnyCloudConfig:
        """Load one optional cloud config, using its validated model defaults if absent."""
        config_model = _CLOUD_CONFIG_MODELS.get(vendor)
        if config_model is None:
            raise ValueError(f"Cloud vendor {vendor.value!r} has no platform configuration model")
        path = self._config_dir / "cloud_providers" / f"{vendor.value}.yaml"
        if not path.is_file():
            logger.info(
                "Cloud configuration absent; using model defaults", extra={"vendor": vendor}
            )
            return config_model()
        return config_model.model_validate(load_yaml_mapping(path))

    def load_all_cloud_configs(self) -> dict[CloudVendor, AnyCloudConfig]:
        """Validate every supported cloud platform configuration at startup."""
        return {vendor: self.load_cloud_config(vendor) for vendor in _CLOUD_CONFIG_MODELS}

    def _load_provider_file(self, provider_name: str) -> ProviderStaticConfig:
        """Load one safe filename and verify its declared identity matches."""
        _validate_config_name(provider_name, "provider filename")
        path = self._config_dir / "providers" / f"{provider_name}.yaml"
        config = parse_provider_config(load_yaml_mapping(path))
        if config.provider_name != provider_name:
            raise ValueError(
                f"Provider file {path.name!r} declares provider_name "
                f"{config.provider_name!r}; the names must match"
            )
        return config


def _validate_config_name(value: str, field_name: str) -> None:
    """Reject path separators, uppercase aliases, and ambiguous config names."""
    if not _SAFE_CONFIG_NAME.fullmatch(value):
        raise ValueError(f"{field_name} must match {_SAFE_CONFIG_NAME.pattern!r}, got {value!r}")
