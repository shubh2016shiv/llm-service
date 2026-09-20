"""Turn one authorization-approved entitlement into an execution route.

Architecture:
    Authentication -> Authorization -> InferenceRouteResolver -> Provider
                              |                 |
                              |                 -> provider/model policy
                              -> exact entitlement id

The boundary is intentionally strict. Authorization chooses the entitlement;
routing re-reads that exact record and enriches it with provider-catalog
defaults. It never searches for another grant and never falls back to a shared
credential. That rule prevents authorization and execution from disagreeing.

Reading order:
    1. ``models.py``: input and output contracts.
    2. ``contracts.py``: the two injected read boundaries.
    3. ``route_resolution.py``: policy gates and orchestration.
    4. ``route_builder.py``: final route construction and fingerprinting.
    5. ``exceptions.py``: typed failure vocabulary.
"""

from app.inference_routing.contracts import InferenceRoutingConfigReader
from app.inference_routing.models import ResolutionRequest, ResolvedRoute
from app.inference_routing.route_resolution import InferenceRouteResolver

__all__ = [
    "InferenceRouteResolver",
    "InferenceRoutingConfigReader",
    "ResolutionRequest",
    "ResolvedRoute",
]
