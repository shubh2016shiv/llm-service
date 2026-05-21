"""
UserPersistence
---------------
PostgreSQL CRUD for the `users` table.

Schema column of note:
  platform_role — stores the platform-wide role ('owner' | 'admin' | 'operator' | 'developer').
  This is distinct from tenant_role which lives in tenant_memberships.

password_hash is never returned by any read method. It is accepted only by
create_user() because the caller already holds the bcrypt hash and would otherwise
need a redundant read-back to confirm the write succeeded.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, ClassVar
from uuid import UUID

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import text

from app.database.base import BasePersistence
from app.database.queries.user_queries import (
    CHECK_USER_EMAIL_EXISTS_SQL,
    CHECK_USER_EXISTS_BY_ID_SQL,
    CHECK_USERNAME_EXISTS_SQL,
    COUNT_USERS_BY_ROLE_SQL,
    COUNT_USERS_BY_STATUS_SQL,
    CREATE_USER_SQL,
    DELETE_USER_BY_EMAIL_SQL,
    DELETE_USER_BY_ID_SQL,
    GET_USER_BY_EMAIL_SQL,
    GET_USER_BY_ID_SQL,
    GET_USER_BY_USERNAME_SQL,
)
from app.database.session import DatabaseSessionManager

logger = logging.getLogger(__name__)


class UserPersistence(BasePersistence):
    """Production-ready persistence for the `users` table.

    All write operations validate inputs before opening a session so callers
    receive ValueError with a clear message rather than a database-level
    constraint violation.

    password_hash is accepted for create but never returned from reads.
    """

    # Valid values match the CHECK constraint in create_users.sql
    VALID_PLATFORM_ROLES: ClassVar[list[str]] = ["owner", "admin", "operator", "developer"]
    VALID_USER_STATUSES: ClassVar[list[str]] = ["active", "suspended", "inactive", "deleted"]

    def __init__(self, database_manager: DatabaseSessionManager | None = None) -> None:
        super().__init__(database_manager)

    # =========================================================================
    # VALIDATION HELPERS
    # =========================================================================

    @staticmethod
    @lru_cache(maxsize=1024)
    def _normalize_email(email_address: str) -> str:
        """Validate and normalise an email address (cached for hot paths).

        Args:
            email_address: Raw email string from the caller.

        Returns:
            Normalised email string (lowercase, RFC-compliant).

        Raises:
            ValueError: If the email is empty or syntactically invalid.
        """
        if not email_address:
            raise ValueError("email_address cannot be empty")
        try:
            validated = validate_email(email_address, check_deliverability=False)
            return validated.email
        except EmailNotValidError as exc:
            raise ValueError(f"Invalid email address: {exc}") from exc

    def validate_platform_role(self, platform_role: str) -> None:
        """Raise ValueError if platform_role is not in VALID_PLATFORM_ROLES."""
        self.validate_enum_value(platform_role, self.VALID_PLATFORM_ROLES, "platform_role")

    def validate_user_status(self, user_status: str) -> None:
        """Raise ValueError if user_status is not in VALID_USER_STATUSES."""
        self.validate_enum_value(user_status, self.VALID_USER_STATUSES, "status")

    async def check_email_exists(self, email: str) -> bool:
        """Return True if the email address is already registered."""
        try:
            async with self.get_session() as session:
                result = await session.execute(text(CHECK_USER_EMAIL_EXISTS_SQL), {"email": email})
                return result.first() is not None
        except Exception:
            logger.error(
                "UserPersistence: check_email_exists failed — email=%s", email, exc_info=True
            )
            raise

    async def check_username_exists(self, username: str) -> bool:
        """Return True if the username is already registered."""
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(CHECK_USERNAME_EXISTS_SQL), {"username": username}
                )
                return result.first() is not None
        except Exception:
            logger.error(
                "UserPersistence: check_username_exists failed — username=%s",
                username,
                exc_info=True,
            )
            raise

    async def check_user_exists(self, user_id: UUID | str) -> bool:
        """Return True if a user with this UUID exists."""
        self.validate_uuid(user_id, "user_id")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(CHECK_USER_EXISTS_BY_ID_SQL), {"user_id": str(user_id)}
                )
                return result.first() is not None
        except Exception:
            logger.error(
                "UserPersistence: check_user_exists failed — user_id=%s", user_id, exc_info=True
            )
            raise

    # =========================================================================
    # CREATE
    # =========================================================================

    async def create_user(
        self,
        user_id: UUID,
        username: str,
        email: str,
        first_name: str,
        last_name: str,
        password_hash: str,
        platform_role: str = "developer",
        status: str = "active",
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Insert a new user row and return the created record.

        Args:
            user_id: Pre-generated UUID for the new user.
            username: Unique username string.
            email: Email address (will be normalised).
            first_name: Given name.
            last_name: Family name.
            password_hash: bcrypt hash of the plaintext password (never stored raw).
            platform_role: Platform-wide role. Defaults to 'developer'.
            status: Account lifecycle status. Defaults to 'active'.
            created_at: Override creation timestamp (defaults to UTC now).
            updated_at: Override update timestamp (defaults to UTC now).

        Returns:
            Row dict without password_hash.

        Raises:
            ValueError: On invalid inputs or duplicate email/username.
            sqlalchemy.exc.SQLAlchemyError: On unexpected database errors.
        """
        self.validate_uuid(user_id, "user_id")
        self.validate_string_not_empty(username, "username")
        normalized_email = self._normalize_email(email)
        self.validate_string_not_empty(first_name, "first_name")
        self.validate_string_not_empty(last_name, "last_name")
        self.validate_string_not_empty(password_hash, "password_hash")
        self.validate_platform_role(platform_role)
        self.validate_user_status(status)

        if await self.check_email_exists(normalized_email):
            raise ValueError(f"Email '{normalized_email}' is already registered")
        if await self.check_username_exists(username):
            raise ValueError(f"Username '{username}' is already taken")

        now = datetime.now(UTC)
        params = {
            "user_id": str(user_id),
            "username": username,
            "email": normalized_email,
            "first_name": first_name,
            "last_name": last_name,
            "password_hash": password_hash,
            "platform_role": platform_role,
            "status": status,
            "created_at": created_at or now,
            "updated_at": updated_at or now,
        }

        try:
            async with self.get_session() as session:
                result = await session.execute(text(CREATE_USER_SQL), params)
                created_user = result.mappings().one_or_none()
                if not created_user:
                    raise RuntimeError(
                        "INSERT succeeded but returned no row — this should not happen"
                    )
                logger.info("UserPersistence: created user — user_id=%s", user_id)
                return dict(created_user)
        except (ValueError, RuntimeError):
            raise
        except Exception:
            logger.error("UserPersistence: create_user failed — user_id=%s", user_id, exc_info=True)
            raise

    # =========================================================================
    # READ
    # =========================================================================

    async def get_user_by_id(self, user_id: UUID | str) -> dict[str, Any] | None:
        """Return a user record by UUID, or None if not found.

        Args:
            user_id: UUID or UUID string.

        Returns:
            Row dict (without password_hash) or None.
        """
        if isinstance(user_id, str):
            try:
                user_id = UUID(user_id)
            except ValueError as exc:
                raise ValueError("user_id must be a valid UUID string") from exc
        self.validate_uuid(user_id, "user_id")

        try:
            async with self.get_session() as session:
                result = await session.execute(text(GET_USER_BY_ID_SQL), {"user_id": str(user_id)})
                row = result.mappings().one_or_none()
                return dict(row) if row else None
        except Exception:
            logger.error(
                "UserPersistence: get_user_by_id failed — user_id=%s", user_id, exc_info=True
            )
            raise

    async def get_user_by_email(self, email_address: str) -> dict[str, Any] | None:
        """Return a user record by email address, or None if not found."""
        normalized = self._normalize_email(email_address)
        try:
            async with self.get_session() as session:
                result = await session.execute(text(GET_USER_BY_EMAIL_SQL), {"email": normalized})
                row = result.mappings().one_or_none()
                return dict(row) if row else None
        except Exception:
            logger.error("UserPersistence: get_user_by_email failed", exc_info=True)
            raise

    async def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        """Return a user record by username, or None if not found."""
        self.validate_string_not_empty(username, "username")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(GET_USER_BY_USERNAME_SQL), {"username": username}
                )
                row = result.mappings().one_or_none()
                return dict(row) if row else None
        except Exception:
            logger.error(
                "UserPersistence: get_user_by_username failed — username=%s",
                username,
                exc_info=True,
            )
            raise

    async def get_all_users(
        self,
        platform_role_filter: str | None = None,
        status_filter: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return a paginated list of users with optional filters.

        Args:
            platform_role_filter: Optional platform_role to filter by.
            status_filter: Optional status to filter by.
            limit: Max rows to return (1-1000).
            offset: Rows to skip.

        Returns:
            List of user row dicts ordered by created_at DESC.
        """
        self.validate_pagination_parameters(limit, offset)
        if platform_role_filter:
            self.validate_platform_role(platform_role_filter)
        if status_filter:
            self.validate_user_status(status_filter)

        sql = (
            "SELECT user_id, username, email, first_name, last_name, "
            "platform_role, status, created_at, updated_at "
            "FROM users WHERE 1=1"
        )
        params: dict[str, Any] = {}

        if platform_role_filter:
            sql += " AND platform_role = :platform_role"
            params["platform_role"] = platform_role_filter
        if status_filter:
            sql += " AND status = :status"
            params["status"] = status_filter

        sql += " ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
        params["limit"] = limit
        params["offset"] = offset

        try:
            async with self.get_session() as session:
                result = await session.execute(text(sql), params)
                rows = result.mappings().all()
                logger.debug("UserPersistence: get_all_users returned %d rows", len(rows))
                return [dict(row) for row in rows]
        except Exception:
            logger.error("UserPersistence: get_all_users failed", exc_info=True)
            raise

    async def count_users_by_status(self, user_status: str) -> int:
        """Return the number of users with the given status."""
        self.validate_user_status(user_status)
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(COUNT_USERS_BY_STATUS_SQL), {"status": user_status}
                )
                return result.scalar_one_or_none() or 0
        except Exception:
            logger.error(
                "UserPersistence: count_users_by_status failed — status=%s",
                user_status,
                exc_info=True,
            )
            raise

    async def count_users(self) -> int:
        """Return the total number of users."""
        try:
            async with self.get_session() as session:
                result = await session.execute(text("SELECT COUNT(*) FROM users"))
                return result.scalar_one_or_none() or 0
        except Exception:
            logger.error("UserPersistence: count_users failed", exc_info=True)
            raise

    async def count_users_by_role(self, platform_role: str) -> int:
        """Return the number of users with the given platform_role."""
        self.validate_platform_role(platform_role)
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(COUNT_USERS_BY_ROLE_SQL), {"platform_role": platform_role}
                )
                return result.scalar_one_or_none() or 0
        except Exception:
            logger.error(
                "UserPersistence: count_users_by_role failed — role=%s",
                platform_role,
                exc_info=True,
            )
            raise

    async def count_users_filtered(
        self,
        platform_role_filter: str | None = None,
        status_filter: str | None = None,
    ) -> int:
        """Return the count of users matching any combination of filters.

        Mirrors the WHERE clause of get_all_users so that list and count
        always agree on the same predicate.
        """
        if platform_role_filter:
            self.validate_platform_role(platform_role_filter)
        if status_filter:
            self.validate_user_status(status_filter)

        sql = "SELECT COUNT(*) FROM users WHERE 1=1"
        params: dict[str, Any] = {}

        if platform_role_filter:
            sql += " AND platform_role = :platform_role"
            params["platform_role"] = platform_role_filter
        if status_filter:
            sql += " AND status = :status"
            params["status"] = status_filter

        try:
            async with self.get_session() as session:
                result = await session.execute(text(sql), params)
                return result.scalar_one_or_none() or 0
        except Exception:
            logger.error(
                "UserPersistence: count_users_filtered failed — role=%s status=%s",
                platform_role_filter,
                status_filter,
                exc_info=True,
            )
            raise

    # =========================================================================
    # UPDATE
    # =========================================================================

    async def update_user(
        self,
        user_id: UUID,
        email_address: str | None = None,
        platform_role: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any] | None:
        """Partially update a user record.

        Only supplied (non-None) fields are written. Returns the updated row
        or None if no user with that UUID exists.

        Args:
            user_id: UUID of the user to update.
            email_address: New email address (will be normalised).
            platform_role: New platform role.
            status: New account status.

        Returns:
            Updated row dict or None.

        Raises:
            ValueError: On invalid inputs.
            sqlalchemy.exc.IntegrityError: On duplicate email.
        """
        self.validate_uuid(user_id, "user_id")
        if email_address is not None:
            email_address = self._normalize_email(email_address)
        if platform_role is not None:
            self.validate_platform_role(platform_role)
        if status is not None:
            self.validate_user_status(status)

        update_fields: dict[str, Any] = {}
        if email_address is not None:
            update_fields["email"] = email_address
        if platform_role is not None:
            update_fields["platform_role"] = platform_role
        if status is not None:
            update_fields["status"] = status

        if not update_fields:
            logger.warning(
                "UserPersistence: update_user called with no fields — user_id=%s", user_id
            )
            return await self.get_user_by_id(user_id)

        sql, params = self.build_dynamic_update_query(
            table_name="users",
            update_fields=update_fields,
            where_clause="user_id = :user_id",
            where_parameters={"user_id": str(user_id)},
        )

        try:
            async with self.get_session() as session:
                result = await session.execute(text(sql), params)
                updated_row = result.mappings().one_or_none()
                if updated_row:
                    self.log_operation("UPDATE", user_id)
                    return dict(updated_row)
                logger.warning(
                    "UserPersistence: update_user — user not found — user_id=%s", user_id
                )
                return None
        except Exception:
            logger.error("UserPersistence: update_user failed — user_id=%s", user_id, exc_info=True)
            raise

    async def update_platform_role(
        self, user_id: UUID, platform_role: str
    ) -> dict[str, Any] | None:
        """Set a user's platform_role."""
        return await self.update_user(user_id=user_id, platform_role=platform_role)

    async def update_status(self, user_id: UUID, status: str) -> dict[str, Any] | None:
        """Set a user's account status."""
        return await self.update_user(user_id=user_id, status=status)

    async def suspend_user(self, user_id: UUID) -> dict[str, Any] | None:
        """Set user status to 'suspended'."""
        return await self.update_status(user_id, "suspended")

    async def activate_user(self, user_id: UUID) -> dict[str, Any] | None:
        """Set user status to 'active'."""
        return await self.update_status(user_id, "active")

    # =========================================================================
    # DELETE
    # =========================================================================

    async def delete_user(self, user_id: UUID) -> bool:
        """Permanently delete a user by UUID.

        Returns:
            True if the row existed and was deleted; False if not found.
        """
        self.validate_uuid(user_id, "user_id")
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(DELETE_USER_BY_ID_SQL), {"user_id": str(user_id)}
                )
                deleted = getattr(result, "rowcount", 0) > 0
                if deleted:
                    self.log_operation("DELETE", user_id)
                return bool(deleted)
        except Exception:
            logger.error("UserPersistence: delete_user failed — user_id=%s", user_id, exc_info=True)
            raise

    async def delete_user_by_email(self, email_address: str) -> bool:
        """Permanently delete a user by email address.

        Returns:
            True if deleted; False if not found.
        """
        normalized = self._normalize_email(email_address)
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text(DELETE_USER_BY_EMAIL_SQL), {"email": normalized}
                )
                deleted = getattr(result, "rowcount", 0) > 0
                if deleted:
                    self.log_operation("DELETE", normalized)
                return bool(deleted)
        except Exception:
            logger.error("UserPersistence: delete_user_by_email failed", exc_info=True)
            raise
