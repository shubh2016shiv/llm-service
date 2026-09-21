"""
Provider Templates — surface config/providers/*.yaml to the create forms

Architecture:
-------------
    +------------------------+     +--------------------------+     +--------------------+
    ¦ catalog router         ¦────▶¦ build_provider_templates ¦────▶¦ ConfigLoader       ¦
    ¦ (interfaces/)          ¦     ¦ (this module)            ¦     ¦ config/providers/  ¦
    ¦ /providers/runtime-    ¦     ¦                          ¦     ¦ *.yaml             ¦
    ¦  templates             ¦     +--------------------------+     +--------------------+
    +------------------------+

Purpose:
    Read-only translation from ProviderStaticConfig — the YAML-backed runtime
    routing config that route_resolution.py validates every inference call
    against — into a client-safe shape the dashboard's Add Provider and Add
    Model forms can prefill from.

Rationale:
    The dashboard's provider_catalog table and this YAML registry are two
    independent systems. Creating a provider or model through the UI has
    never required, or verified, a matching YAML file, so a mismatch is only
    discovered at the first real inference call. Making the known-good set
    visible in the UI closes that feedback gap at the point of entry.

Dependencies:
    - app/core/settings/loader.py — reads and validates the provider YAML files
    - app/schemas/management_schema.py — the client-facing response contracts

Author: Shubham Singh
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.settings.models.provider_config import AuthMode
from app.schemas.enums import ProviderCatalogType
from app.schemas.management_schema import ProviderTemplate, ProviderTemplateModel

if TYPE_CHECKING:
    from app.core.settings.loader import ConfigLoader
    from app.core.settings.models.provider_config import ProviderStaticConfig

# ProviderStaticConfig.provider_type is a transport-strategy label ("rest_api")
# shared by every provider, so it cannot distinguish a managed cloud API from
# something a team runs itself — the distinction ProviderCatalogType needs.
# Matching on the endpoint host is a heuristic, offered as an editable
# suggestion in the form rather than presented as a fact from the YAML.
_LOCAL_HOST_MARKERS = ("localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal")


def build_provider_templates(config_loader: ConfigLoader) -> list[ProviderTemplate]:
    """Translate every config/providers/*.yaml file into a ProviderTemplate.

    Args:
        config_loader: Reads the same YAML files route_resolution.py's Gate 4
            (ensure_model_supported) validates an inference call against.

    Returns:
        One ProviderTemplate per loadable YAML file, sorted by provider_name
        so the dashboard's dropdown order stays stable between requests.
    """
    static_configs = config_loader.load_all_provider_configs()
    templates = [_build_template(config) for config in static_configs.values()]
    return sorted(templates, key=lambda template: template.provider_name)


def _build_template(config: ProviderStaticConfig) -> ProviderTemplate:
    """Translate one ProviderStaticConfig into its client-safe template."""
    return ProviderTemplate(
        provider_name=config.provider_name,
        suggested_provider_type=_suggest_provider_type(config.endpoints.base_url),
        auth_mode=AuthMode(config.auth.mode.value),
        default_api_endpoint_url=config.endpoints.base_url,
        supported_operations=sorted(capability.value for capability in config.capabilities),
        models=[
            ProviderTemplateModel(
                model_name=model.name,
                context_window_tokens=model.context_window,
                max_output_tokens=model.max_output_tokens,
                supported_operations=sorted(
                    capability.value for capability in model.capabilities
                ),
            )
            for model in config.models
        ],
    )


def _suggest_provider_type(base_url: str) -> ProviderCatalogType:
    """Suggest a provider_type from the endpoint host.

    Args:
        base_url: The provider's default endpoint from YAML.

    Returns:
        SELF_HOSTED when the host looks local or private, DIRECT_API
        otherwise. Only a starting point — the create form keeps the field
        editable because the YAML cannot express this distinction.
    """
    if any(marker in base_url for marker in _LOCAL_HOST_MARKERS):
        return ProviderCatalogType.SELF_HOSTED
    return ProviderCatalogType.DIRECT_API
