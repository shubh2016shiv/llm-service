"""
External Client Adapters
========================

Client wrappers for outbound service-to-service communication.

Architecture:
-------------
    execution/
        │
        ▼
    app.adapters.clients
        └── token_manager_client.py → TokenManagerClient

Author: Engineering Team
Last Updated: 2026-05-16
"""

from app.clients.token_manager_client import (
    QuotaExceededError,
    TokenManagerClient,
)

__all__ = ["QuotaExceededError", "TokenManagerClient"]
