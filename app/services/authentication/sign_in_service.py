"""
Sign-In Service
===============

Turns a credential (or an enabled guest door) into a signed session token.

What this service does:
    Verifies a username and password against stored hashes, enforces the
    failed-attempt budget, and issues an access token. It also serves the
    guest superuser path, which skips verification entirely and exists so a
    developer can administer a fresh local stack before any password is known.

What it does not do:
    Decide HTTP status codes (``api/exception_handlers``), verify tokens
    (``auth/jwt_token_validator``), or authorize a request once identity is
    established (``auth/authorization``).

Security posture:
    - Unknown username and wrong password raise the same error, after spending
      the same CPU time, so neither the message nor the response time reveals
      which accounts exist.
    - Only active accounts may sign in. A suspended account is refused with the
      same message as a wrong password.
    - The guest path is refused unless configuration enables it, and the
      settings composition root refuses to start in production with it on.

Architecture:
-------------
    ┌────────────────┐     ┌──────────────────────┐     ┌────────────────────┐
    │  auth router   │────▶│  [This Module]       │────▶│  UserPersistence   │
    │  (api/)        │     │  SignInService       │     │  (database/)       │
    └────────────────┘     └──────────┬───────────┘     └────────────────────┘
                                      │
                    ┌─────────────────┼──────────────────┐
                    ▼                 ▼                  ▼
          ┌──────────────────┐ ┌──────────────┐ ┌──────────────────┐
          │ password_hashing │ │ token_issuer │ │ SignInAttempt    │
          │ (services/users) │ │ (auth/)      │ │ Limiter (cache)  │
          └──────────────────┘ └──────────────┘ └──────────────────┘

Dependencies:
    - app.database.users — the one credential-returning read.
    - app.services.users.password_hashing — verification and the timing decoy.
    - app.auth.token_issuer — signs the resulting token.
    - app.adapters.cache.sign_in_attempt_limiter — failed-attempt budget.

Author: Shubham Singh
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, NamedTuple, cast
from uuid import UUID

from app.auth.token_issuer import issue_access_token
from app.core.exceptions import (
    GuestSessionDisabledError,
    InvalidCredentialsError,
    TooManySignInAttemptsError,
)
from app.schemas.enums import UserAccountStatus
from app.services.users.password_hashing import spend_verification_time, verify_password

if TYPE_CHECKING:
    from datetime import datetime

    from app.adapters.cache.sign_in_attempt_limiter import SignInAttemptLimiter
    from app.database import UserPersistence
    from app.schemas.auth_schema import UserRole

logger = logging.getLogger(__name__)

_VALID_ROLES = frozenset(("developer", "operator", "admin", "owner"))


class AuthenticatedSession(NamedTuple):
    """A signed token plus the identity the caller may display."""

    access_token: str
    expires_at: datetime
    user_id: UUID
    username: str
    role: UserRole
    is_guest: bool


class SignInService:
    """Establish a caller's identity and issue a session token.

    Example:
        >>> service = SignInService(users=FakeUserPersistence(), limiter=FakeLimiter(), ...)
        >>> session = await service.sign_in_with_password("alice", "s3cret")
        >>> session.is_guest
        False
    """

    def __init__(
        self,
        *,
        user_persistence: UserPersistence,
        attempt_limiter: SignInAttemptLimiter,
        guest_enabled: bool,
        guest_user_id: UUID | None,
        guest_role: UserRole,
    ) -> None:
        """Initialize with injected dependencies and the guest-door configuration.

        Args:
            user_persistence: Supplies stored credentials for verification.
            attempt_limiter: Enforces the failed-attempt budget per username.
            guest_enabled: Whether the unauthenticated guest door is open.
            guest_user_id: Existing user the guest session acts as.
            guest_role: Platform role granted to a guest session.
        """
        self._users = user_persistence
        self._limiter = attempt_limiter
        self._guest_enabled = guest_enabled
        self._guest_user_id = guest_user_id
        self._guest_role = guest_role

    @property
    def guest_enabled(self) -> bool:
        """Report whether this deployment offers a guest session."""
        return self._guest_enabled and self._guest_user_id is not None

    async def sign_in_with_password(self, username: str, password: str) -> AuthenticatedSession:
        """Verify a credential and issue a session token.

        Args:
            username: Username supplied by the caller.
            password: Plaintext password supplied by the caller.

        Returns:
            AuthenticatedSession: Token and the identity behind it.

        Raises:
            TooManySignInAttemptsError: The failure budget for this username is spent.
            InvalidCredentialsError: Unknown user, wrong password, or inactive account.
        """
        budget = await self._limiter.check(username)
        if not budget.allowed:
            raise TooManySignInAttemptsError(budget.retry_after_seconds)

        record = await self._users.get_sign_in_credentials_by_username(username)
        verified_user = await self._verify_record(record, password)
        if verified_user is None:
            await self._limiter.record_failure(username)
            raise InvalidCredentialsError

        await self._limiter.clear(username)
        user_id, resolved_role = verified_user
        issued = issue_access_token(user_id=user_id, role=resolved_role)
        logger.info(
            "Password sign-in succeeded",
            extra={"user_id": str(user_id), "role": resolved_role},
        )
        return AuthenticatedSession(
            access_token=issued.token,
            expires_at=issued.expires_at,
            user_id=user_id,
            username=username,
            role=resolved_role,
            is_guest=False,
        )

    async def sign_in_as_guest(self) -> AuthenticatedSession:
        """Issue a superuser session without any credential check.

        Returns:
            AuthenticatedSession: Token for the configured guest identity.

        Raises:
            GuestSessionDisabledError: Guest access is not enabled here.
        """
        if not self.guest_enabled or self._guest_user_id is None:
            raise GuestSessionDisabledError

        issued = issue_access_token(user_id=self._guest_user_id, role=self._guest_role)
        # WARNING level on purpose: an unauthenticated superuser session is
        # normal in development and alarming anywhere else, so it should stand
        # out in a log that is being skimmed rather than blend into INFO.
        logger.warning(
            "Guest superuser session issued without authentication",
            extra={"user_id": str(self._guest_user_id), "role": self._guest_role},
        )
        return AuthenticatedSession(
            access_token=issued.token,
            expires_at=issued.expires_at,
            user_id=self._guest_user_id,
            username="guest",
            role=self._guest_role,
            is_guest=True,
        )

    async def _verify_record(
        self,
        record: dict[str, object] | None,
        password: str,
    ) -> tuple[UUID, UserRole] | None:
        """Check one credential row, or burn equivalent time when there is none.

        Returns the verified identity, or None for every kind of failure. The
        caller converts that into the single non-revealing error, so the reason
        stays in the log and never reaches the response.
        """
        if record is None:
            # No row to compare against, but returning now would make an
            # unknown username measurably faster than a wrong password.
            await asyncio.to_thread(spend_verification_time)
            logger.info("Sign-in rejected", extra={"reason": "unknown_username"})
            return None

        stored_hash = str(record.get("password_hash") or "")
        # PBKDF2 is deliberately CPU-expensive. A worker thread keeps one
        # sign-in from pausing every coroutine on the event loop.
        matched = await asyncio.to_thread(verify_password, password, stored_hash)
        if not matched:
            logger.info("Sign-in rejected", extra={"reason": "password_mismatch"})
            return None

        if str(record.get("status")) != UserAccountStatus.ACTIVE.value:
            logger.info(
                "Sign-in rejected",
                extra={"reason": "account_not_active", "status": str(record.get("status"))},
            )
            return None

        raw_role = str(record.get("platform_role") or "")
        if raw_role not in _VALID_ROLES:
            # The role column is CHECK-constrained, so this means the database
            # and this service's vocabulary have diverged. Refusing the sign-in
            # is safer than issuing a token carrying a role nothing can rank.
            logger.error(
                "Sign-in rejected: stored platform_role is outside the known vocabulary",
                extra={"platform_role": raw_role},
            )
            return None

        return UUID(str(record["user_id"])), cast("UserRole", raw_role)
