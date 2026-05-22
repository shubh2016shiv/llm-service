"""
Inference Routing Exceptions
============================

Domain exceptions raised by routing components when policy, entitlement, or
capability checks fail.

Enterprise Pattern: Canonical Exception Surface
    Routing raises stable domain errors so API translation remains consistent.

Why this matters:
    API handlers should map typed errors to stable HTTP/status payloads without
    parsing message strings. This file defines that stable error vocabulary.

Author: Shubham Singh
"""

from __future__ import annotations

from app.core.exceptions import LLMServiceError


class ResolutionError(LLMServiceError):
    """Base class for all routing-resolution failures."""

    error_code: str = "RESOLUTION_ERROR"


class ProviderNotAllowedError(ResolutionError):
    """Raised when tenant allow-list denies selected provider route."""

    error_code: str = "PROVIDER_NOT_ALLOWED"

    def __init__(self, tenant_id: str, provider_name: str) -> None:
        super().__init__(
            f"Provider {provider_name!r} is not allowed for tenant {tenant_id!r}."
        )
        self.tenant_id = tenant_id
        self.provider_name = provider_name


class OperationNotSupportedError(ResolutionError):
    """Raised when model/provider cannot serve requested operation type."""

    error_code: str = "OPERATION_NOT_SUPPORTED"

    def __init__(self, provider_name: str, model_name: str, operation: str) -> None:
        super().__init__(
            f"Operation {operation!r} is not supported by model {model_name!r} "
            f"on provider {provider_name!r}."
        )
        self.provider_name = provider_name
        self.model_name = model_name
        self.operation = operation


class AmbiguousUserEntitlementError(ResolutionError):
    """Raised when multiple active entitlement matches create precedence ambiguity."""

    error_code: str = "AMBIGUOUS_USER_ENTITLEMENT"

    def __init__(self, tenant_id: str, user_id: str, deployment_key: str) -> None:
        super().__init__(
            f"Multiple user entitlements matched deployment "
            f"{deployment_key!r} for user {user_id!r} in tenant {tenant_id!r}."
        )
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.deployment_key = deployment_key

