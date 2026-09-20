"""Typed failures raised while turning an authorized grant into a route.

Architecture:
    InferenceRouteResolver -> these errors -> API exception translation

Stable ``error_code`` values let the HTTP boundary map failures without
parsing human-readable messages.
"""

from __future__ import annotations

from app.core.exceptions import LLMServiceError


class ResolutionError(LLMServiceError):
    """Base class for route-resolution failures."""

    error_code: str = "RESOLUTION_ERROR"


class ProviderNotAllowedError(ResolutionError):
    """The tenant policy forbids the entitlement's provider."""

    error_code: str = "PROVIDER_NOT_ALLOWED"

    def __init__(self, tenant_id: str, provider_name: str) -> None:
        super().__init__(f"Provider {provider_name!r} is not allowed for tenant {tenant_id!r}.")
        self.tenant_id = tenant_id
        self.provider_name = provider_name


class OperationNotSupportedError(ResolutionError):
    """The selected model cannot perform the requested operation."""

    error_code: str = "OPERATION_NOT_SUPPORTED"

    def __init__(self, provider_name: str, model_name: str, operation: str) -> None:
        super().__init__(
            f"Operation {operation!r} is not supported by model {model_name!r} "
            f"on provider {provider_name!r}."
        )
        self.provider_name = provider_name
        self.model_name = model_name
        self.operation = operation


class AuthorizedEntitlementUnavailableError(ResolutionError):
    """The authorization-approved entitlement is no longer executable."""

    error_code: str = "AUTHORIZED_ENTITLEMENT_UNAVAILABLE"

    def __init__(self, entitlement_id: str) -> None:
        super().__init__(f"Authorized entitlement {entitlement_id!r} is no longer available.")
        self.entitlement_id = entitlement_id
