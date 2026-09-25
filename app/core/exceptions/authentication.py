"""Sign-in failures raised while issuing a session token.

Architecture:
    sign-in router -> SignInService -> these errors -> exception_handlers -> HTTP

These are deliberately separate from ``app/auth``'s authorization errors. Those
answer "may this already-identified caller do X"; these answer "is this caller
who they claim to be at all", which happens before any identity exists.

Security note on wording: every message here is written to be safe to show a
caller verbatim. None of them reveal whether a username exists, because an
error that distinguishes "no such user" from "wrong password" turns the sign-in
endpoint into a tool for discovering account names.
"""

from __future__ import annotations

from app.core.exceptions.base import LLMServiceError


class SignInError(LLMServiceError):
    """Base class for every failure to establish a caller's identity.

    Named for the act rather than the concept because ``AuthenticationError``
    is already taken by the provider layer, where it means "the upstream LLM
    provider rejected our API key". Two unrelated failures sharing one name
    would map to the same HTTP status by accident.
    """

    error_code = "SIGN_IN_ERROR"


class InvalidCredentialsError(SignInError):
    """Raised when a username is unknown, the password is wrong, or the account is not active.

    All three causes share one message and one error code on purpose. The
    service layer logs which one actually happened; the caller is told only
    that the attempt failed.
    """

    error_code = "INVALID_CREDENTIALS"

    def __init__(self) -> None:
        """Initialize with the single non-revealing message used for every cause."""
        super().__init__("Incorrect username or password, or the account is not active.")


class GuestSessionDisabledError(SignInError):
    """Raised when a guest session is requested but the deployment does not offer one.

    This is the normal state: guest access is opt-in through configuration and
    is refused outright in production.
    """

    error_code = "GUEST_SESSION_DISABLED"

    def __init__(self) -> None:
        """Initialize with a message that states the fact without naming the setting."""
        super().__init__("Guest access is not enabled on this deployment.")


class TooManySignInAttemptsError(SignInError):
    """Raised when one account has failed sign-in too often inside the window.

    Carries ``retry_after_seconds`` so the HTTP layer can emit a Retry-After
    header, which is what the shared ``_retry_after_headers`` helper looks for.
    """

    error_code = "TOO_MANY_SIGN_IN_ATTEMPTS"

    def __init__(self, retry_after_seconds: int) -> None:
        """Initialize with the number of seconds the caller must wait.

        Args:
            retry_after_seconds: Seconds remaining in the current lockout window.
        """
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"Too many failed sign-in attempts. Try again in {retry_after_seconds} seconds.",
        )
