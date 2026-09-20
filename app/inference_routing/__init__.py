"""
Inference routing — turning "please run this" into "call THIS endpoint"
=========================================================================

What this package is for
------------------------
After authentication (who are you?) and authorization (are you allowed?),
one question remains before a prompt can run: WHERE exactly should it go?
Which provider, which model, which endpoint URL, which API key reference,
with what timeout and temperature?

This package answers that question. It takes one small request shape
(ResolutionRequest) and returns one complete answer (ResolvedRoute) that
downstream execution code can use without making further decisions.

The files, in reading order
---------------------------
    1. contracts.py
       The shopping list: the three pieces of configuration the resolver
       needs from storage (tenant rules, the user's personal keys, the
       deployment setup). Any reader that provides these three methods
       works here.

    2. models.py
       The request shape (what goes in) and the route shape (what comes
       out). Both frozen: safe to share across requests.

    3. cache_keys.py
       One tiny helper: the shared Redis label for a deployment's cached
       configuration.

    4. exceptions.py
       The error vocabulary: every "no" this package says, with a stable
       machine-readable code the API layer can translate.

    5. route_resolution.py
       The resolver itself: the step-by-step decision that combines the
       pieces above.

The decision, in one paragraph
------------------------------
The user's personal key wins first (bring-your-own-key); the tenant's
shared deployment is the fallback. Then: is the tenant allowed to use
that provider? Does the provider actually support that model, and can
the model perform this operation? Only when every check passes is the
route built.

Author: Shubham Singh
"""

# Re-export the public shapes so callers import from the package, not
# from the individual files.
from app.inference_routing.contracts import InferenceRoutingConfigReader
from app.inference_routing.models import ResolutionRequest, ResolvedRoute
from app.inference_routing.route_resolution import InferenceRouteResolver

__all__ = [
    "InferenceRouteResolver",
    "InferenceRoutingConfigReader",
    "ResolutionRequest",
    "ResolvedRoute",
]
