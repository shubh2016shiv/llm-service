"""
Inference Routing Package
=========================

TL;DR: When an API call arrives asking to run a chat/embed/rerank request,
this package figures out which AI provider to use, which model, which
credentials, and whether the caller is even allowed to do so. It answers
one question: "given a tenant ID and a deployment key, what exact provider
call should we make?"

How it fits in the bigger picture:
    The API layer (``app/api``) authenticates the user and authorizes the
    tenant/deployment pair. Then it hands off to this package, which resolves
    the route into a concrete execution plan — provider name, model name,
    API endpoint URL, and a reference to the right credential. The result is
    an immutable context object that the inference service uses to make the
    actual provider call.

The resolution pipeline (in order):
    1. Load tenant config and verify the tenant is active.
    2. Check if the user has a personal entitlement (override) for this route.
    3. If no user override, fall back to the tenant's deployment config.
    4. Validate that the selected provider and model support the requested
       operation (chat, embed, or rerank).
    5. Select the credential reference (never the actual secret — that stays
       in the secrets manager).
    6. Build and return a frozen (immutable) execution context.

Architecture decisions and rationale:
    - Separation from inference execution:
      This package decides "what should run" but never performs provider calls.
      That split keeps policy/routing logic independent from transport logic.
    - Immutable context output:
      Resolved context is frozen to prevent accidental mutation across layers
      after routing decisions are made.
    - Protocol-based data readers:
      Resolvers depend on contracts, not concrete persistence classes. This
      keeps resolver logic testable and lets storage implementations evolve.
    - Security boundary on secrets:
      Routing resolves only ``secret_reference`` values. Plaintext secrets are
      materialized later by secret-store components in infrastructure.

Enterprise Pattern: Orchestration Pipeline + Facade
    - Facade: this ``__init__.py`` re-exports the public API so callers only
      need one import.
    - Orchestration: ``OrchestrationPipeline`` coordinates focused resolver
      components, each with one clear job, to produce a single result.

Step-by-step integration with request lifecycle:
    1. API/auth layer validates identity and tenant/deployment access.
    2. API constructs ``ResolutionRequest`` and calls ``OrchestrationPipeline``.
    3. Pipeline returns ``ResolvedExecutionContext``.
    4. Inference service executes provider call using that context.
    5. Usage/quota/reporting operate on context fields (provider/model/quota key).

Author: Shubham Singh
"""

from app.inference_routing.context_factory import ResolvedExecutionContextFactory
from app.inference_routing.contracts import (
    DeploymentConfigReader,
    TenantConfigReader,
    UserEntitlementReader,
)
from app.inference_routing.credential_resolver import CredentialResolver, CredentialSelection
from app.inference_routing.deployment_resolver import DeploymentResolver
from app.inference_routing.entitlement_resolver import UserEntitlementResolver
from app.inference_routing.models import (
    CredentialScope,
    ResolutionRequest,
    ResolutionSource,
    ResolvedExecutionContext,
)
from app.inference_routing.pipeline import OrchestrationPipeline
from app.inference_routing.provider_validator import ProviderRouteValidator
from app.inference_routing.tenant_resolver import TenantResolver

__all__ = [
    "CredentialResolver",
    "CredentialScope",
    "CredentialSelection",
    "DeploymentConfigReader",
    "DeploymentResolver",
    "OrchestrationPipeline",
    "ProviderRouteValidator",
    "ResolutionRequest",
    "ResolutionSource",
    "ResolvedExecutionContext",
    "ResolvedExecutionContextFactory",
    "TenantConfigReader",
    "TenantResolver",
    "UserEntitlementReader",
    "UserEntitlementResolver",
]
