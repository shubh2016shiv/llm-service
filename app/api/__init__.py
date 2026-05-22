"""
API Package - HTTP entry points for inference and platform management.

Architecture:
-------------
    ┌──────────────────────────────┐
    │ app.main (FastAPI bootstrap) │
    └───────────────┬──────────────┘
                    │ includes routers
        ┌───────────┴─────────────────────┐
        ▼                                 ▼
    ┌─────────────────────┐        ┌──────────────────────┐
    │ llm_inference_router│        │ management_routers/* │
    │ product inference   │        │ admin/ops workflows  │
    └──────────┬──────────┘        └──────────┬───────────┘
               │                              │
               └──────────────┬───────────────┘
                              ▼
                     ┌──────────────────┐
                     │ app.services     │
                     │ business rules   │
                     └────────┬─────────┘
                              ▼
                     ┌──────────────────┐
                     │ app.database     │
                     │ persistence      │
                     └──────────────────┘

Why the split matters:
    Inference traffic and management traffic have different operational needs.
    Inference favors low latency and predictable runtime behavior, while
    management routes prioritize auditability and strict administrative role
    checks. Keeping them in separate router groups prevents accidental coupling
    and makes ownership easier for new contributors.

Step-by-step request flow:
    1. FastAPI matches the URL to one router in this package.
    2. Dependencies run first (authentication, tenant checks, service factories).
    3. The route handler calls the service layer for business decisions.
    4. Service results are returned as response schemas.
    5. Domain exceptions are translated to HTTP errors in `exception_handlers`.

Dependencies:
    - app.auth: Resolves caller identity and role claims.
    - app.services: Contains business workflows used by route handlers.
    - app.schemas: Defines validated request and response models.
    - app.api.exception_handlers: Centralizes domain-error to HTTP translation.

Author: Shubham Singh
"""

from app.api.llm_inference_router import router as llm_inference_router
from app.api.management_routers import router as management_router

__all__ = ["llm_inference_router", "management_router"]
