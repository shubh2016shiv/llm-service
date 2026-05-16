"""
app/schemas — Pydantic request/response schemas for the LLM gateway.

These models are the canonical data shapes flowing through the system:
API router → request_dispatcher → provider → request_dispatcher → API router.

Package Structure
-----------------
    schemas/
    ├── requests.py       ← ChatRequest, EmbedRequest, RerankRequest, ChatMessage
    ├── responses.py      ← ChatResponse, ChatStreamChunk, EmbedResponse,
    │                        RerankResponse, RerankResult, HealthStatus, Usage
    └── enums.py          ← ProviderType, OperationType, AuthMode

Usage
-----
    from app.schemas import ChatRequest, ChatResponse, HealthStatus
    from app.schemas.enums import ProviderType, OperationType
"""

from app.schemas.enums import AuthMode, OperationType, ProviderType
from app.schemas.requests import ChatMessage, ChatRequest, EmbedRequest, RerankRequest
from app.schemas.responses import (
    ChatResponse,
    ChatStreamChunk,
    EmbedResponse,
    HealthStatus,
    RerankResponse,
    Usage,
)

__all__ = [
    # ── Requests ──
    "ChatMessage",
    "ChatRequest",
    "EmbedRequest",
    "RerankRequest",
    # ── Responses ──
    "ChatResponse",
    "ChatStreamChunk",
    "EmbedResponse",
    "HealthStatus",
    "RerankResponse",
    "Usage",
    # ── Enums ──
    "AuthMode",
    "OperationType",
    "ProviderType",
]
