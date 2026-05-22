"""
User Service
============

Business service for managing platform user accounts.

What this service does:
    Handles user account lifecycle operations such as create, list, retrieve,
    update, suspend, activate, and delete. Returned rows are sanitized so
    secret-bearing fields (notably password hashes) never leave the service.

Security rationale - password handling:
    User passwords are never persisted in plaintext. On create, passwords are
    transformed with PBKDF2-HMAC-SHA256 using a random salt and high iteration
    count. The persisted value is a self-describing hash string containing
    algorithm, iteration count, salt, and derived hash.

Enterprise Pattern: CRUD Service Pattern
    Persistence calls are wrapped with error translation and output cleaning
    so API layers can remain thin and security-safe.

Author: Shubham Singh
"""

from __future__ import annotations

import hashlib
import secrets
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from app.core.exceptions import ResourceNotFoundError
from app.services.management_helpers import Row, clean_row, clean_rows, raise_clean_validation_error

if TYPE_CHECKING:
    from app.database import UserPersistence
    from app.schemas.management_schema import UserCreateRequest, UserUpdateRequest

_PBKDF2_ITERATIONS = 390_000
_SALT_BYTES = 16


class UserService:
    """Manage platform-user lifecycle operations with secure credential handling."""

    def __init__(self, user_persistence: UserPersistence) -> None:
        """Initialize with persistence adapter for user storage operations."""
        self._users = user_persistence

    async def create_user(self, request: UserCreateRequest) -> Row:
        """Create a user account and store only a hashed password.

        The input password is hashed immediately and never returned by any
        service response shape.
        """
        try:
            row = await self._users.create_user(
                user_id=uuid4(),
                username=request.username,
                email=str(request.email),
                first_name=request.first_name,
                last_name=request.last_name,
                password_hash=self._hash_password(request.password),
                platform_role=request.platform_role,
                status=request.status,
            )
            return clean_row(row)
        except ValueError as exc:
            raise_clean_validation_error(exc)

    async def list_users(
        self,
        platform_role_filter: str | None,
        status_filter: str | None,
        limit: int,
        offset: int,
    ) -> list[Row]:
        """List users with optional role and status filters.

        Args:
            platform_role_filter: Optional role filter (for example, admin).
            status_filter: Optional account status filter (active/suspended).
            limit: Maximum users to return in one page.
            offset: Number of rows skipped before retrieval.
        """
        rows = await self._users.get_all_users(
            platform_role_filter=platform_role_filter,
            status_filter=status_filter,
            limit=limit,
            offset=offset,
        )
        return clean_rows(rows)

    async def count_users(
        self,
        platform_role_filter: str | None,
        status_filter: str | None,
    ) -> int:
        """Count users matching list filters for pagination metadata."""
        return await self._users.count_users_filtered(platform_role_filter, status_filter)

    async def get_user(self, user_id: UUID) -> Row:
        """Retrieve one user by UUID or raise a typed not-found error."""
        row = await self._users.get_user_by_id(user_id)
        if row is None:
            raise ResourceNotFoundError("User", str(user_id))
        return clean_row(row)

    async def get_user_by_email(self, email: str) -> Row:
        """Retrieve one user by email address.

        Email lookups are useful for admin tooling and login-adjacent flows
        that resolve identity from normalized email input.
        """
        row = await self._users.get_user_by_email(email)
        if row is None:
            raise ResourceNotFoundError("User", email)
        return clean_row(row)

    async def update_user(self, user_id: UUID, request: UserUpdateRequest) -> Row:
        """Partially update mutable user attributes.

        Password updates are intentionally not handled in this method; this
        keeps account-profile updates separate from credential-rotation logic.
        """
        try:
            row = await self._users.update_user(
                user_id=user_id,
                email_address=str(request.email) if request.email is not None else None,
                platform_role=request.platform_role,
                status=request.status,
            )
        except ValueError as exc:
            raise_clean_validation_error(exc)
        if row is None:
            raise ResourceNotFoundError("User", str(user_id))
        return clean_row(row)

    async def suspend_user(self, user_id: UUID) -> Row:
        """Set user status to suspended to block normal account activity."""
        row = await self._users.suspend_user(user_id)
        if row is None:
            raise ResourceNotFoundError("User", str(user_id))
        return clean_row(row)

    async def activate_user(self, user_id: UUID) -> Row:
        """Set user status to active so account access can resume."""
        row = await self._users.activate_user(user_id)
        if row is None:
            raise ResourceNotFoundError("User", str(user_id))
        return clean_row(row)

    async def delete_user(self, user_id: UUID) -> None:
        """Delete a user account permanently."""
        deleted = await self._users.delete_user(user_id)
        if not deleted:
            raise ResourceNotFoundError("User", str(user_id))

    def _hash_password(self, password: str) -> str:
        """Hash a plaintext password using PBKDF2-HMAC-SHA256.

        PBKDF2 is a key-derivation function designed to make brute-force
        attacks expensive by requiring many hash iterations per attempt.
        The returned value embeds algorithm parameters so verification logic
        can evolve without separate schema fields.
        """
        salt = secrets.token_bytes(_SALT_BYTES)
        password_hash = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            _PBKDF2_ITERATIONS,
        )
        return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${password_hash.hex()}"
