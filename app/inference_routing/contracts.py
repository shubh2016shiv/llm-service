"""
The storage contract — what the resolver needs from the outside world
========================================================================

What this file is for
---------------------
The resolver decides routes, but it does not read databases itself. It
asks a "reader" for three kinds of facts. This file declares those three
questions as a Protocol — a shape that any class with these three
methods satisfies automatically, no inheritance required.

The real reader is CachedInferenceRoutingConfigReader (in
app/adapters/inference_routing), which answers from PostgreSQL + Redis.
Tests supply tiny fakes instead. The resolver cannot tell the difference
— that is the point.

The three questions
-------------------
    read_tenant_config     -> the tenant's rules: active? plan? allowed
                              providers?
    find_user_entitlements -> this user's personal API-key records
                              (bring-your-own-key candidates).
    read_deployment_config -> the tenant's shared deployment setup.
"""

# This line makes every type hint below a lazy string. (Boilerplate.)
from __future__ import annotations

# TYPE_CHECKING is only True while a type checker reads the file, never at
# runtime — imports under it exist purely for type hints.
# Protocol = describes "any class with these methods" (structural typing).
from typing import TYPE_CHECKING, Protocol

# Names used only in type hints, so they are imported only for the checker.
if TYPE_CHECKING:
    from uuid import UUID

    from app.core.settings.models.tenant_config import (
        DeploymentConfig,
        TenantConfig,
        UserEntitlementConfig,
    )
    from app.inference_routing.models import ResolutionRequest


class InferenceRoutingConfigReader(Protocol):
    """The three storage questions the route resolver needs answered."""

    async def read_tenant_config(self, tenant_id: UUID) -> TenantConfig | None:
        """Return the tenant's rules, or None when the tenant does not exist."""
        ...

    async def find_user_entitlements(
        self,
        request: ResolutionRequest,
    ) -> list[UserEntitlementConfig]:
        """Return this user's personal-key records matching the request.

        An empty list simply means "no personal key here" — that is
        normal, not an error.
        """
        ...

    async def read_deployment_config(
        self,
        tenant_id: UUID,
        deployment_key: str,
    ) -> DeploymentConfig | None:
        """Return the deployment's setup, or None when it does not exist."""
        ...
