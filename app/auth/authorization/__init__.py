"""
Authorization — the door policies (management + inference)
============================================================

What this package is for
------------------------
Authentication answers "WHO are you?" (the JWT check, which happens
earlier, in ``app.auth``). Authorization answers the follow-up question:
"Are you ALLOWED to do this?" This package is the authorization half.

It contains three players, each guarding a different kind of door:

    1. TenantAccessService           (tenant_access.py)
       The bouncer for MANAGEMENT APIs — "can this caller read or change
       this tenant's settings?" (users, tenants, deployments,
       entitlements).

    2. InferenceAuthorizationService (tenant_inference_auth.py)
       The gatekeeper for INFERENCE requests — the bigger question "can
       this caller run this exact model, for this tenant, under this
       deployment, right now?"

    3. AuthorizationGrantCache       (authorization_grant_cache.py)
       The rememberer. Not a decision-maker itself: it remembers the
       gatekeeper's last "yes" so a repeat request can skip the slow
       database checks — while making sure a remembered "yes" goes stale
       the moment anything it depended on changes.

How these connect to the Auth Stage numbering in ``app.auth``
-------------------------------------------------------------
    Auth Stage 1-2 -- authentication (identity, JWT) — earlier package.
    Auth Stage 3   -- tenant_access.py — the management doors.
    Auth Stage 4   -- tenant_inference_auth.py — the inference door.
    Auth Stage 5   -- authorization_grant_cache.py — remembering Stage 4.

Routes receive these services through the focused dependency modules in
``app/api``. Those modules wire the services together as FastAPI dependencies
and hand the results to the route handlers.

Suggested reading order (for learning this package from scratch)
----------------------------------------------------------------
    1. tenant_access.py             (about 3 minutes)
       The simplest door: platform badges vs. tenant membership.
    2. tenant_inference_auth.py     (about 5 minutes)
       The layered inference check, one gate at a time.
    3. authorization_grant_cache.py (about 10 minutes)
       The clever part: how a saved "yes" is kept honest with four
       change stamps.

If any line in these files still reads like jargon, it is a bug in the
comments — not in you. Fix it right there.

Author: Shubham Singh
"""

# Re-export the three players so calling modules can import from
# ``app.auth.authorization`` without knowing which file each class lives in.
from app.auth.authorization.authorization_grant_cache import AuthorizationGrantCache
from app.auth.authorization.tenant_access import TenantAccessService
from app.auth.authorization.tenant_inference_auth import InferenceAuthorizationService

__all__ = [
    "AuthorizationGrantCache",
    "InferenceAuthorizationService",
    "TenantAccessService",
]
