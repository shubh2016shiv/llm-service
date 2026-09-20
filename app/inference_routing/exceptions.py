"""
Routing exceptions — the error vocabulary
==========================================

What this file is for
---------------------
When routing says "no", it must say WHY in a way the API layer can turn
into a clean HTTP response without reading error-message text. Each error
class here carries a stable machine-readable code (error_code) — for
example "PROVIDER_NOT_ALLOWED" — plus a human-readable message.

Why this matters:
    - API handlers translate typed errors by CLASS and CODE, not by
      parsing message strings (which change, get localized, etc.).
    - Adding a new "no" reason = adding one class here, and every caller
      gets it consistently.

Author: Shubham Singh
"""

# This line makes every type hint below a lazy string. (Boilerplate.)
from __future__ import annotations

# The one base error for the whole service: every routing error is also
# an LLMServiceError, so generic handlers still catch them.
from app.core.exceptions import LLMServiceError


class ResolutionError(LLMServiceError):
    """The parent of every "routing said no" error in this package.

    Catch this one class to catch all routing failures; read the
    error_code to tell them apart.
    """

    error_code: str = "RESOLUTION_ERROR"


class ProviderNotAllowedError(ResolutionError):
    """The tenant's allowed-providers list does not include this provider.

    Raised after a route was picked: the tenant may be perfectly valid,
    but its admin restricted it to other providers only.
    """

    error_code: str = "PROVIDER_NOT_ALLOWED"

    def __init__(self, tenant_id: str, provider_name: str) -> None:
        super().__init__(f"Provider {provider_name!r} is not allowed for tenant {tenant_id!r}.")
        self.tenant_id = tenant_id
        self.provider_name = provider_name


class OperationNotSupportedError(ResolutionError):
    """The chosen model cannot perform the requested operation.

    For example: the caller asked for embeddings, but this model only
    does chat.
    """

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
    """The user has MORE THAN ONE matching personal key for this route.

    With two active personal keys, the resolver cannot know which one the
    caller meant — so it refuses rather than guess.
    """

    error_code: str = "AMBIGUOUS_USER_ENTITLEMENT"

    def __init__(self, tenant_id: str, user_id: str, deployment_key: str) -> None:
        super().__init__(
            f"Multiple user entitlements matched deployment "
            f"{deployment_key!r} for user {user_id!r} in tenant {tenant_id!r}."
        )
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.deployment_key = deployment_key
