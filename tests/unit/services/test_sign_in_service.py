"""Sign-in service tests: who gets a token, who is refused, and what leaks."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from uuid import UUID

import pytest

from app.adapters.cache.sign_in_attempt_limiter import AttemptBudget
from app.core.exceptions import (
    GuestSessionDisabledError,
    InvalidCredentialsError,
    TooManySignInAttemptsError,
)
from app.services.authentication import SignInService
from app.services.users.password_hashing import hash_password

if TYPE_CHECKING:
    from app.adapters.cache.sign_in_attempt_limiter import SignInAttemptLimiter
    from app.database import UserPersistence

KNOWN_USER_ID = UUID("70994844-a74b-4642-92f6-a956bbc57498")
GUEST_USER_ID = UUID("7cbb6261-c5fb-4b3d-bda4-13139bfd04cf")
CORRECT_PASSWORD = "correct-horse-battery-staple"


class FakeUserPersistence:
    """Return one canned credential row. Does not touch a database."""

    def __init__(self, row: dict[str, object] | None) -> None:
        self.row = row
        self.looked_up_usernames: list[str] = []

    async def get_sign_in_credentials_by_username(self, username: str) -> dict[str, object] | None:
        self.looked_up_usernames.append(username)
        return self.row


class FakeAttemptLimiter:
    """Record limiter traffic and answer with a preset budget."""

    def __init__(self, *, allowed: bool = True, retry_after_seconds: int = 0) -> None:
        self.budget = AttemptBudget(allowed=allowed, retry_after_seconds=retry_after_seconds)
        self.recorded_failures: list[str] = []
        self.cleared: list[str] = []

    async def check(self, username: str) -> AttemptBudget:
        return self.budget

    async def record_failure(self, username: str) -> None:
        self.recorded_failures.append(username)

    async def clear(self, username: str) -> None:
        self.cleared.append(username)


def build_service(
    *,
    row: dict[str, object] | None,
    limiter: FakeAttemptLimiter | None = None,
    guest_enabled: bool = False,
    guest_user_id: UUID | None = GUEST_USER_ID,
) -> tuple[SignInService, FakeAttemptLimiter, FakeUserPersistence]:
    """Assemble the service over fakes, returning the fakes for assertions."""
    users = FakeUserPersistence(row)
    attempt_limiter = limiter or FakeAttemptLimiter()
    service = SignInService(
        user_persistence=cast("UserPersistence", users),
        attempt_limiter=cast("SignInAttemptLimiter", attempt_limiter),
        guest_enabled=guest_enabled,
        guest_user_id=guest_user_id,
        guest_role="owner",
    )
    return service, attempt_limiter, users


def active_user_row(**overrides: object) -> dict[str, object]:
    """Build a credential row for an active admin with the known password."""
    row: dict[str, object] = {
        "user_id": str(KNOWN_USER_ID),
        "username": "shubham.singh",
        "password_hash": hash_password(CORRECT_PASSWORD),
        "platform_role": "admin",
        "status": "active",
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_sign_in_with_password_when_credentials_correct_returns_token() -> None:
    service, limiter, _ = build_service(row=active_user_row())

    session = await service.sign_in_with_password("shubham.singh", CORRECT_PASSWORD)

    assert session.user_id == KNOWN_USER_ID
    assert session.role == "admin"
    assert session.is_guest is False
    assert session.access_token
    assert session.expires_at is not None
    assert limiter.cleared == ["shubham.singh"], "a success must reset the failure budget"


@pytest.mark.asyncio
async def test_sign_in_with_password_when_password_wrong_raises_invalid_credentials() -> None:
    service, limiter, _ = build_service(row=active_user_row())

    with pytest.raises(InvalidCredentialsError):
        await service.sign_in_with_password("shubham.singh", "not-the-password")

    assert limiter.recorded_failures == ["shubham.singh"]


@pytest.mark.asyncio
async def test_sign_in_with_password_when_username_unknown_raises_identical_error() -> None:
    # Identical error type and message is the whole point: a different one
    # would let a caller discover which usernames exist.
    service, limiter, _ = build_service(row=None)

    with pytest.raises(InvalidCredentialsError) as unknown_user:
        await service.sign_in_with_password("nobody", CORRECT_PASSWORD)

    wrong_password_service, _, _ = build_service(row=active_user_row())
    with pytest.raises(InvalidCredentialsError) as wrong_password:
        await wrong_password_service.sign_in_with_password("shubham.singh", "wrong")

    assert str(unknown_user.value) == str(wrong_password.value)
    assert limiter.recorded_failures == ["nobody"]


@pytest.mark.parametrize("inactive_status", ["suspended", "inactive", "deleted"])
@pytest.mark.asyncio
async def test_sign_in_with_password_when_account_not_active_raises_invalid_credentials(
    inactive_status: str,
) -> None:
    service, _, _ = build_service(row=active_user_row(status=inactive_status))

    with pytest.raises(InvalidCredentialsError):
        await service.sign_in_with_password("shubham.singh", CORRECT_PASSWORD)


@pytest.mark.asyncio
async def test_sign_in_with_password_when_stored_role_unknown_raises_invalid_credentials() -> None:
    # A role outside the vocabulary means the database and this service have
    # diverged; a token carrying it could not be ranked by authorization.
    service, _, _ = build_service(row=active_user_row(platform_role="superuser"))

    with pytest.raises(InvalidCredentialsError):
        await service.sign_in_with_password("shubham.singh", CORRECT_PASSWORD)


@pytest.mark.asyncio
async def test_sign_in_with_password_when_attempt_budget_spent_raises_too_many_attempts() -> None:
    limiter = FakeAttemptLimiter(allowed=False, retry_after_seconds=240)
    service, _, users = build_service(row=active_user_row(), limiter=limiter)

    with pytest.raises(TooManySignInAttemptsError) as locked_out:
        await service.sign_in_with_password("shubham.singh", CORRECT_PASSWORD)

    assert locked_out.value.retry_after_seconds == 240
    assert users.looked_up_usernames == [], "a locked-out attempt must not query credentials"


@pytest.mark.asyncio
async def test_sign_in_as_guest_when_disabled_raises_guest_session_disabled() -> None:
    service, _, _ = build_service(row=None, guest_enabled=False)

    assert service.guest_enabled is False
    with pytest.raises(GuestSessionDisabledError):
        await service.sign_in_as_guest()


@pytest.mark.asyncio
async def test_sign_in_as_guest_when_enabled_returns_superuser_token() -> None:
    service, _, _ = build_service(row=None, guest_enabled=True)

    session = await service.sign_in_as_guest()

    assert session.user_id == GUEST_USER_ID
    assert session.role == "owner"
    assert session.is_guest is True
    assert session.access_token


@pytest.mark.asyncio
async def test_sign_in_as_guest_when_no_identity_configured_raises_guest_session_disabled() -> None:
    # Defence in depth: settings validation already rejects this pairing, but
    # the service must not mint a token for user_id None if it ever slips past.
    service, _, _ = build_service(row=None, guest_enabled=True, guest_user_id=None)

    assert service.guest_enabled is False
    with pytest.raises(GuestSessionDisabledError):
        await service.sign_in_as_guest()


@pytest.mark.asyncio
async def test_sign_in_with_password_issued_token_passes_own_validator() -> None:
    # Issuance and verification are two halves of one contract; this is the
    # test that fails first if either drifts.
    from app.auth.jwt_token_validator import validate_access_token

    service, _, _ = build_service(row=active_user_row())
    session = await service.sign_in_with_password("shubham.singh", CORRECT_PASSWORD)

    payload = validate_access_token(session.access_token)

    assert payload.user_id == KNOWN_USER_ID
    assert payload.role == "admin"
    assert payload.expires_at > payload.issued_at


@pytest.mark.asyncio
async def test_sign_in_as_guest_issued_token_passes_own_validator() -> None:
    from app.auth.jwt_token_validator import validate_access_token

    service, _, _ = build_service(row=None, guest_enabled=True)
    session = await service.sign_in_as_guest()

    assert validate_access_token(session.access_token).user_id == GUEST_USER_ID


@pytest.mark.asyncio
async def test_sign_in_with_password_when_rejected_omits_secrets_from_message() -> None:
    service, _, _ = build_service(row=active_user_row())
    secret_attempt = "hunter2-should-never-be-echoed"

    with pytest.raises(InvalidCredentialsError) as refused:
        await service.sign_in_with_password("shubham.singh", secret_attempt)

    assert secret_attempt not in str(refused.value)
    assert "shubham.singh" not in str(refused.value)

