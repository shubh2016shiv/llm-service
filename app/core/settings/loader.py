"""
ConfigLoader — Assembles frozen Pydantic settings models from YAML source files.

Responsibilities:
    - Load and parse YAML files from the settings directory tree
    - Merge base settings with environment-specific overlays (deep merge)
    - Validate merged dicts against frozen Pydantic models
    - Provide synchronous accessors (settings is read at startup, not per-request)

Step-by-step loading flow:
    1. Read ``base.yaml`` as the baseline configuration.
    2. Read optional ``environments/<env>.yaml`` overlay.
    3. Deep-merge overlay values on top of baseline values.
    4. Validate merged dictionaries into frozen Pydantic models.
    5. Expose typed config objects to provider registry and infrastructure code.

Architecture:
-------------
    config/
    ├── base.yaml                    ──┐
    ├── environments/{env}.yaml      ──┼──►  GlobalConfig
    ├── providers/{name}.yaml        ──────►  ProviderStaticConfig
    └── cloud_providers/{name}.yaml  ──────►  AnyCloudConfig

    ConfigLoader (this module)
         │
         ├── load_global_config()          → GlobalConfig
         ├── load_provider_config(name)    → ProviderStaticConfig
         ├── load_all_provider_configs()   → Dict[str, ProviderStaticConfig]
         └── load_cloud_config(vendor)     → AnyCloudConfig

Design notes:
    - Uses pathlib.Path (never os.path) per Agents.md
    - All YAML loading uses yaml.safe_load() (never yaml.load())
    - Dict deep-merge: environment values override base values recursively

Failure modes (what callers actually catch):
    This module raises standard exceptions, not a domain-specific one:
      - ``FileNotFoundError``  — a required YAML file is absent.
      - ``yaml.YAMLError``     — the file exists but is not valid YAML.
      - ``ValidationError``    — YAML parsed, but violates the Pydantic model.
      - ``KeyError``           — a mandatory YAML key is missing (see builders).
      - ``ValueError``         — unknown cloud vendor passed to load_cloud_config.
    Missing-file handling deliberately differs per loader: ``base.yaml`` and a
    named provider file are required, while the environment overlay, the
    providers directory, and cloud files fall back to warn-and-default.

Dependencies:
    - pyyaml          — YAML parsing
    - pydantic >= 2.0 — model validation

Author: Shubham Singh
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import yaml

from app.core.settings.models.cloud_config import (
    AnyCloudConfig,
    AWSCloudConfig,
    AzureCloudConfig,
    CloudVendor,
    GCPCloudConfig,
)
from app.core.settings.models.global_config import GlobalConfig
from app.core.settings.models.model_config import LLMModelSpec, ModelCapability
from app.core.settings.models.provider_config import (
    AuthMode,
    ProviderAuthConfig,
    ProviderEndpointConfig,
    ProviderStaticConfig,
    ProviderType,
)

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

# Mapping from cloud vendor string to its settings model class.
# Typed as the concrete union rather than bare ``type``: a bare ``type`` erases
# the model identity, so the type checker cannot see ``.model_validate`` on the
# looked-up class and treats every load_cloud_config() return as ``Any``.
_CLOUD_CONFIG_MAP: dict[
    str, type[AWSCloudConfig] | type[AzureCloudConfig] | type[GCPCloudConfig]
] = {
    CloudVendor.AWS: AWSCloudConfig,
    CloudVendor.AZURE: AzureCloudConfig,
    CloudVendor.GCP: GCPCloudConfig,
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base, returning a new dict.

    Nested dicts are merged rather than replaced. Scalar values in override
    always win over base values.

    Args:
        base: Base configuration dictionary (e.g., from base.yaml).
        override: Override dictionary (e.g., from environments/production.yaml).

    Returns:
        A new merged dictionary with override values taking precedence.

    Example:
        >>> _deep_merge({"a": {"x": 1, "y": 2}}, {"a": {"y": 99}})
        {'a': {'x': 1, 'y': 99}}
    """
    result: dict[str, Any] = dict(base)
    for key, override_value in override.items():
        base_value = result.get(key)
        if isinstance(base_value, dict) and isinstance(override_value, dict):
            result[key] = _deep_merge(base_value, override_value)
        else:
            result[key] = override_value
    return result


def _load_yaml_file(path: Path) -> dict[str, Any]:
    """Read and parse a single YAML file using safe_load.

    Args:
        path: Absolute or relative path to the YAML file.

    Returns:
        Parsed YAML contents as a plain dict. Returns {} if file is empty.

    Raises:
        FileNotFoundError: If the file does not exist.
        yaml.YAMLError: If the file contains invalid YAML syntax.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}. "
            "Ensure the settings directory is mounted and the file exists."
        )
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


# ── Raw-dict → frozen-model builders ─────────────────────────────────────────
#
# Everything below turns a parsed YAML dict into a validated, frozen settings
# model. They are grouped here, as module-level functions, because that is the
# one job they share: none of them touch the filesystem, hold state, or need a
# ConfigLoader instance. Keeping them together (rather than scattering one on
# the class and one at module scope) is what makes the shared default handling
# below — _DEFAULT_CAPABILITIES, _parse_capabilities — obviously shared rather
# than accidentally duplicated.

_DEFAULT_CAPABILITIES: list[str] = ["chat"]


def _parse_capabilities(raw: dict[str, Any]) -> frozenset[ModelCapability]:
    """Read the optional ``capabilities`` list and coerce it to typed members.

    Shared by the provider-level and model-level builders, which declare
    capabilities with identical YAML shape and identical defaulting.
    """
    capabilities_raw: list[str] = raw.get("capabilities", _DEFAULT_CAPABILITIES)
    return frozenset(ModelCapability(capability) for capability in capabilities_raw)


def _build_model_spec(raw: dict[str, Any]) -> LLMModelSpec:
    """Construct an LLMModelSpec from a raw YAML model entry dict.

    Args:
        raw: Dict from a single entry in the provider YAML `models` list.

    Returns:
        A validated, frozen LLMModelSpec instance.

    Raises:
        KeyError: If a mandatory key (``name``, ``max_output_tokens``,
            ``context_window``) is absent from the entry.
    """
    return LLMModelSpec(
        name=raw["name"],
        display_name=raw.get("display_name"),
        version=raw.get("version"),
        max_output_tokens=raw["max_output_tokens"],
        context_window=raw["context_window"],
        capabilities=_parse_capabilities(raw),
        price_per_1k_prompt_tokens=raw.get("price_per_1k_prompt_tokens"),
        price_per_1k_completion_tokens=raw.get("price_per_1k_completion_tokens"),
        is_active=raw.get("is_active", True),
        is_deprecated=raw.get("is_deprecated", False),
    )


def _build_auth_config(raw: dict[str, Any]) -> ProviderAuthConfig:
    """Construct the auth block of a provider config from its YAML sub-dict."""
    auth_raw: dict[str, Any] = raw.get("auth", {})
    return ProviderAuthConfig(
        mode=AuthMode(auth_raw["mode"]),
        header_name=auth_raw.get("header_name"),
        header_prefix=auth_raw.get("header_prefix"),
        aws_service_name=auth_raw.get("aws_service_name"),
    )


def _build_endpoint_config(raw: dict[str, Any]) -> ProviderEndpointConfig:
    """Construct the endpoint block of a provider config from its YAML sub-dict."""
    endpoints_raw: dict[str, Any] = raw.get("endpoints", {})
    return ProviderEndpointConfig(
        base_url=endpoints_raw.get("base_url", ""),
        base_url_template=endpoints_raw.get("base_url_template"),
        chat=endpoints_raw.get("chat"),
        embed=endpoints_raw.get("embed"),
        rerank=endpoints_raw.get("rerank"),
        health=endpoints_raw.get("health"),
    )


def _build_provider_static_config(raw: dict[str, Any]) -> ProviderStaticConfig:
    """Construct a ProviderStaticConfig from a raw provider YAML dict.

    Args:
        raw: Parsed YAML dict from one file in ``config/providers/``.

    Returns:
        Validated, frozen ProviderStaticConfig.

    Raises:
        KeyError: If a mandatory key (``provider_name``, ``provider_type``,
            ``implementation_class``, or ``auth.mode``) is absent.
    """
    # Read once: the same `defaults` sub-dict backs all three values below.
    defaults_raw: dict[str, Any] = raw.get("defaults", {})
    models_raw: list[dict[str, Any]] = raw.get("models", [])

    return ProviderStaticConfig(
        provider_name=raw["provider_name"],
        provider_type=ProviderType(raw["provider_type"]),
        implementation_class=raw["implementation_class"],
        auth=_build_auth_config(raw),
        endpoints=_build_endpoint_config(raw),
        capabilities=_parse_capabilities(raw),
        default_timeout_seconds=defaults_raw.get("timeout_seconds", 60.0),
        default_max_retries=defaults_raw.get("max_retries", 3),
        default_temperature=defaults_raw.get("temperature", 0.7),
        models=tuple(_build_model_spec(model_raw) for model_raw in models_raw),
        extra_default_headers=raw.get("extra_default_headers", {}),
    )


class ConfigLoader:
    """Loads and validates all YAML configuration into frozen Pydantic models.

    This class is instantiated once at startup. It is synchronous by design
    because configuration loading happens before request handling begins.

    Example:
        >>> loader = ConfigLoader(config_dir=Path("config"), environment="production")
        >>> global_cfg = loader.load_global_config()
        >>> openai_cfg = loader.load_provider_config("openai")
    """

    def __init__(self, config_dir: Path, environment: str = "development") -> None:
        """Initialise the loader with the settings root directory.

        Args:
            config_dir: Path to the root config/ directory.
            environment: Active deployment environment name
                         (selects the environment overlay YAML).
        """
        self._config_dir = config_dir
        self._environment = environment
        # Provider YAML is static for the process lifetime, and this loader
        # is itself a startup singleton (see class docstring). Without this
        # cache, load_provider_config would re-read and re-parse the same
        # file from disk on every inference request that resolves through
        # it — synchronous, unbounded blocking I/O inside the async request
        # path. Caching here makes the class actually match its own
        # documented contract: "settings is read at startup, not per-request".
        self._provider_config_cache: dict[str, ProviderStaticConfig] = {}
        logger.info(
            "ConfigLoader initialised",
            extra={"config_dir": str(config_dir), "environment": environment},
        )

    def load_global_config(self) -> GlobalConfig:
        """Assemble GlobalConfig by merging base.yaml with the env overlay.

        Returns:
            A frozen GlobalConfig instance with all system-wide defaults.

        Raises:
            FileNotFoundError: If base.yaml is missing.
            pydantic.ValidationError: If the merged settings fails schema validation.

        Example:
            >>> cfg = loader.load_global_config()
            >>> cfg.http_pool.max_connections
            100
        """
        base_path = self._config_dir / "base.yaml"
        env_path = self._config_dir / "environments" / f"{self._environment}.yaml"

        base_data: dict[str, Any] = _load_yaml_file(base_path)
        env_data: dict[str, Any] = {}
        if env_path.exists():
            env_data = _load_yaml_file(env_path)
        else:
            logger.warning(
                "Environment overlay not found, using base.yaml only",
                extra={"path": str(env_path)},
            )

        merged: dict[str, Any] = _deep_merge(base_data, env_data)
        config = GlobalConfig.model_validate(merged)
        logger.info(
            "Global settings loaded",
            extra={"environment": self._environment},
        )
        return config

    def load_provider_config(self, provider_name: str) -> ProviderStaticConfig:
        """Load and validate a single provider's static settings from YAML.

        Cached per provider name after the first successful load: this is
        called from inference route resolution on every request, and the
        underlying YAML does not change for the life of the process, so a
        cache hit avoids re-reading and re-parsing the file on the request
        path. A failed load (missing file, bad YAML) is never cached, so a
        fix on disk takes effect on the very next call without a restart.

        Args:
            provider_name: Lowercase provider identifier (e.g., 'openai').

        Returns:
            A frozen ProviderStaticConfig for the named provider.

        Raises:
            FileNotFoundError: If the provider YAML file does not exist.
            pydantic.ValidationError: If the YAML fails schema validation.

        Example:
            >>> cfg = loader.load_provider_config("openai")
            >>> cfg.provider_name
            'openai'
        """
        cached_config = self._provider_config_cache.get(provider_name)
        if cached_config is not None:
            return cached_config

        path = self._config_dir / "providers" / f"{provider_name}.yaml"
        raw: dict[str, Any] = _load_yaml_file(path)
        # _build_provider_static_config is a MODULE-level helper (not a
        # method): calling it via self would fail at runtime.
        config = _build_provider_static_config(raw)
        self._provider_config_cache[provider_name] = config
        logger.info(
            "Provider settings loaded",
            extra={"provider_name": provider_name},
        )
        return config

    def load_all_provider_configs(self) -> dict[str, ProviderStaticConfig]:
        """Load all provider YAML files from config/providers/.

        Returns:
            Mapping of provider name to validated static provider config for
            every ``.yaml`` file discovered in ``config/providers``.

        Example:
            >>> all_configs = loader.load_all_provider_configs()
            >>> list(all_configs.keys())
            ['anthropic', 'bedrock', 'openai', 'vllm']
        """
        providers_dir: Path = self._config_dir / "providers"
        configs: dict[str, ProviderStaticConfig] = {}

        if not providers_dir.exists():
            logger.warning(
                "Providers settings directory not found",
                extra={"path": str(providers_dir)},
            )
            return configs

        for yaml_file in sorted(providers_dir.glob("*.yaml")):
            provider_name = yaml_file.stem
            try:
                configs[provider_name] = self.load_provider_config(provider_name)
            except Exception as exc:
                # WHY: Log and skip rather than crash — lets the service start
                # even if one provider YAML has a syntax error.
                logger.error(
                    "Failed to load provider settings, skipping",
                    extra={"provider_name": provider_name, "error": str(exc)},
                )

        return configs

    def load_cloud_config(self, vendor: str) -> AnyCloudConfig | None:
        """Load cloud-provider infrastructure settings from YAML.

        Args:
            vendor: Cloud vendor name: 'aws' | 'azure' | 'gcp'.

        Returns:
            A frozen cloud settings model, or None if the file doesn't exist.

        Raises:
            ValueError: If vendor is not a recognised cloud vendor.

        Example:
            >>> aws_cfg = loader.load_cloud_config("aws")
            >>> aws_cfg.default_region
            'us-east-1'
        """
        config_class = _CLOUD_CONFIG_MAP.get(vendor)
        if config_class is None:
            raise ValueError(
                f"Unknown cloud vendor {vendor!r}. Must be one of: {list(_CLOUD_CONFIG_MAP)}"
            )

        path = self._config_dir / "cloud_providers" / f"{vendor}.yaml"
        if not path.exists():
            logger.debug(
                "Cloud settings file not found, using defaults",
                extra={"vendor": vendor, "path": str(path)},
            )
            return config_class()

        raw: dict[str, Any] = _load_yaml_file(path)
        return config_class.model_validate(raw)
