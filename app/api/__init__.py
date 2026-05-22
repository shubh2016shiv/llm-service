"""
API Package
===========

This package contains all HTTP route handlers (endpoints) for the LLM services
application. When a request arrives, this package receives it, validates it,
and forwards it to the correct service.

It is organized into two groups:

    Inference Endpoints (``llm_inference_router.py``)
        Routes used by product clients for chat, embeddings, and reranking.

    Management Endpoints (``management_routers/``)
        Administrative routes used to manage tenants, users, providers, models,
        deployments, and entitlements.

Why separate these two groups?
    They have different authorization, performance, and operational concerns.
    Separation keeps each group easier to understand and safer to change.

Enterprise Pattern: Package Facade Pattern
    This ``__init__.py`` collects and re-exports routers under stable names so
    other modules can import from ``app.api`` without knowing internal file
    layout.

Plain explanation for new developers:
    Think of this package as the API front desk.
    1) It accepts incoming HTTP requests.
    2) It checks who is calling and what they can access.
    3) It calls service-layer logic.
    4) It returns a clean response.

How a request flows through the system:
    app.main (FastAPI application entry point)
    -> app.api routers (this package - parses URL and checks auth)
    -> app.services (business logic - decides what to do)
    -> app.database (reads/writes data to PostgreSQL or Redis)

Dependencies (other packages this one relies on):
    - app.auth: Authenticates caller identity and role.
    - app.services: Runs business logic.
    - app.schemas: Defines request and response data shapes.

Author: Shubham Singh
"""

from app.api.llm_inference_router import router as llm_inference_router
from app.api.management_routers import router as management_router

__all__ = ["llm_inference_router", "management_router"]
