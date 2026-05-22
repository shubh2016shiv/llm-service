"""
Services Package
================

This package is the business-logic layer, often called the service layer.
If the API layer is responsible for speaking HTTP and the database layer is
responsible for reading and writing rows, this layer is responsible for
decision-making: which operation is allowed, which validation rules apply,
and which side effects must happen after a successful write.

TL;DR for new developers:
    - ``app/api`` parses requests and returns responses, but does not own
      business rules.
    - ``app/database`` performs persistence operations, but does not decide
      whether an operation should be allowed.
    - ``app/services`` sits in between and coordinates policy, validation,
      persistence ordering, and consistency side effects.

What each service does:
    Every service in this package is responsible for one business domain
    (tenants, users, deployments, models, and so on). A service is expected
    to define:

    1. Which authorization checks run before the operation.
    2. Which cross-field or cross-entity validation rules apply.
    3. Which persistence calls execute and in what order.
    4. Which side effects happen after writes (for example, cache invalidation).

The API layer calls a service method. The service enforces rules, calls
persistence, and returns a cleaned, typed result. Keeping that flow strict
prevents route handlers from becoming "mini services" that duplicate logic
across endpoints.

Important distinction - what lives here vs. in ``app/auth``:
    Authorization *decisions* (for example, "can this user read this tenant?")
    live in ``app.auth.authorization``. This package *calls* those services as
    one step of a broader workflow. In other words, authorization is an input
    into service orchestration, not a replacement for it.

Enterprise Pattern: Service Layer Pattern
    Every business operation has one service entry point that coordinates
    authorization, validation, persistence, and cache invalidation into a
    single testable unit. Route handlers remain thin and predictable because
    they only parse inputs and call service methods.

Author: Shubham Singh
"""

from app.services.catalog.model_catalog import ModelCatalogService
from app.services.catalog.provider_catalog import ProviderCatalogService
from app.services.inference import InferenceService
from app.services.management_reference_validation import ManagementReferenceValidationService
from app.services.tenants.deployment import TenantDeploymentService
from app.services.tenants.membership import TenantMembershipService
from app.services.tenants.tenant import TenantService
from app.services.users.entitlement import UserEntitlementService
from app.services.users.user import UserService

__all__ = [
    "InferenceService",
    "ManagementReferenceValidationService",
    "ModelCatalogService",
    "ProviderCatalogService",
    "TenantDeploymentService",
    "TenantMembershipService",
    "TenantService",
    "UserEntitlementService",
    "UserService",
]
