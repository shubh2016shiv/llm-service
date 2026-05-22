"""
Schemas Package
===============

This package contains the typed data contracts used at API boundaries.
These models define exactly what requests we accept and what responses we return.

Enterprise Pattern: Contract-First Schema Layer
    Routers, services, and clients share one canonical schema vocabulary so
    behavior stays consistent across the system.

High-level flow:
    API request -> request schema validation -> service execution
    -> response schema serialization -> API response

Author: Shubham Singh
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
    "AuthMode",
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
    "ChatStreamChunk",
    "DeploymentCreateRequest",
    "DeploymentUpdateRequest",
    "EmbedRequest",
    "EmbedResponse",
    "EntitlementCreateRequest",
    "EntitlementUpdateRequest",
    "HealthStatus",
    "MembershipCreateRequest",
    "MembershipUpdateRequest",
    "ModelCreateRequest",
    "ModelUpdateRequest",
    "OperationType",
    "PaginatedResponse",
    "ProviderCreateRequest",
    "ProviderType",
    "ProviderUpdateRequest",
    "RerankRequest",
    "RerankResponse",
    "ResourceResponse",
    "TenantCreateRequest",
    "TenantUpdateRequest",
    "Usage",
    "UserCreateRequest",
    "UserUpdateRequest",
]
