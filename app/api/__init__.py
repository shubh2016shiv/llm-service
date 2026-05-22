"""
API Package
===========

FastAPI routers for inference and schema-aligned management endpoints.

Architecture:
-------------
    app.main
        │
        ▼
    app.api routers
        │
        ▼
    app.services services
        │
        ▼
    app.database persistence

Dependencies:
    - app.auth — JWT role guards
    - app.services — business services
    - app.schemas — request/response schemas

Author: Engineering Team
Last Updated: 2026-05-18
"""

from app.api.llm_inference_router import router as llm_inference_router
from app.api.management_routers import router as management_router

__all__ = ["llm_inference_router", "management_router"]
