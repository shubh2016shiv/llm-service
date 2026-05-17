"""
app/schemas/auth_schema.py — JWT token payload and auth response schemas.

These models represent the decoded identity inside every authenticated request.
They are Pydantic models so FastAPI can serialise/validate them at route boundaries.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Canonical set of roles in ascending privilege order.
# Higher roles inherit all permissions of lower roles.
UserRole = Literal["developer", "operator", "admin", "owner"]


class AuthTokenPayload(BaseModel):
    """Decoded JWT access or refresh token payload.

    Produced by ``decode_token`` and injected into route handlers
    via ``get_current_user``. Never construct this manually.
    """

    model_config = ConfigDict(frozen=True)

    user_id: UUID = Field(description="Unique identifier of the authenticated user.")
    role: UserRole = Field(description="Role that determines endpoint access level.")
    token_type: Literal["access", "refresh"] = Field(
        description="Token kind — prevents refresh tokens from authorising API calls."
    )
    expires_at: datetime = Field(description="UTC datetime at which the token expires.")
    issued_at: datetime = Field(description="UTC datetime at which the token was issued.")


class AuthTokenResponse(BaseModel):
    """Response body returned after successful login.

    Follows the OAuth 2.0 bearer token response convention.
    """

    model_config = ConfigDict(frozen=True)

    access_token: str = Field(description="Signed JWT access token.")
    token_type: str = Field(default="bearer", description="Always 'bearer'.")
    expires_in_seconds: int = Field(description="Number of seconds until the access token expires.")
