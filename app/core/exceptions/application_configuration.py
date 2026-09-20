"""Configuration-integrity exceptions for provider/model catalog data.

Architecture: inference route resolution -> ConfigurationError -> API handlers.

Despite the name, this is raised at request time, not process startup: the
only current raise site is ``route_resolution.py``, when a tenant deployment
references a provider whose catalog YAML is missing on disk. That is an
operator/config-deployment mistake discovered while serving a request, not a
process-boot failure — a missing settings file at actual startup fails
`ConfigLoader` directly and never reaches this class.
"""

from app.core.exceptions.base import LLMServiceError


class ConfigurationError(LLMServiceError):
    """A referenced provider/model configuration file is missing or invalid.

    Raised when route resolution cannot find the on-disk catalog config for
    a provider a deployment already references — a server-side data
    integrity problem, not a caller mistake.
    """

    error_code = "CONFIGURATION_ERROR"
