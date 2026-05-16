"""
Execution Package
=================

Application services responsible for the LLM request execution lifecycle.

Architecture:
-------------
    api/
        │
        ▼
    app.execution
        └── inference_service.py → InferenceService

Author: Engineering Team
Last Updated: 2026-05-16
"""

from app.execution.inference_service import InferenceService

__all__ = ["InferenceService"]
