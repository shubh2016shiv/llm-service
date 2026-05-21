"""
app/schemas — Pydantic request/response schemas for the LLM gateway.

These models are the canonical data shapes flowing through the system:
API router → request_dispatcher → provider → request_dispatcher → API router.

Package Structure
-----------------
    schemas/
    ├── requests_schema.py       ← ChatRequest, EmbedRequest, RerankRequest, ChatMessage
    ├── responses_schema.py      ← ChatResponse, ChatStreamChunk, EmbedResponse,
    │                        RerankResponse, RerankResult, HealthStatus, Usage
    ├── management_schema.py     ← CRUD request/response contracts
    └── enums.py          ← ProviderType, OperationType, AuthMode

Usage
-----
    from app.schemas import ChatRequest, ChatResponse, HealthStatus
    from app.schemas.enums import ProviderType, OperationType
"""

from app.schemas.enums import AuthMode, OperationType, ProviderType
from app.schemas.management_schema import (
    DeploymentCreateRequest,
    DeploymentUpdateRequest,
    EntitlementCreateRequest,
    EntitlementUpdateRequest,
    MembershipCreateRequest,
    MembershipUpdateRequest,
    ModelCreateRequest,
    ModelUpdateRequest,
    PaginatedResponse,
    ProviderCreateRequest,
    ProviderUpdateRequest,
    ResourceResponse,
    TenantCreateRequest,
    TenantUpdateRequest,
    UserCreateRequest,
    UserUpdateRequest,
)
from app.schemas.requests_schema import ChatMessage, ChatRequest, EmbedRequest, RerankRequest
from app.schemas.responses_schema import (
    ChatResponse,
    ChatStreamChunk,
    EmbedResponse,
    HealthStatus,
    RerankResponse,
    Usage,
)

__all__ = [
    # ── Enums ──
    "AuthMode",
    "DeploymentCreateRequest",
    "DeploymentUpdateRequest",
    "EntitlementCreateRequest",
    "EntitlementUpdateRequest",
    "MembershipCreateRequest",
    "MembershipUpdateRequest",
    "ModelCreateRequest",
    "ModelUpdateRequest",
    "PaginatedResponse",
    "ProviderCreateRequest",
    "ProviderUpdateRequest",
    "ResourceResponse",
    "TenantCreateRequest",
    "TenantUpdateRequest",
    "UserCreateRequest",
    "UserUpdateRequest",
    # ── Requests ──
    "ChatMessage",
    "ChatRequest",
    # ── Responses ──
    "ChatResponse",
    "ChatStreamChunk",
    "EmbedRequest",
    "EmbedResponse",
    "HealthStatus",
    "OperationType",
    "ProviderType",
    "RerankRequest",
    "RerankResponse",
    "Usage",
]
