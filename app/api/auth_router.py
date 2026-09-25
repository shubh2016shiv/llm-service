"""
Authentication Router.

Architecture:
-------------
    ┌───────────────────────────────┐
    │ admin dashboard (browser)     │
    └───────────────┬───────────────┘
                     ▼
    ┌───────────────────────────────┐
    │ auth router (`/api/v1/auth`)  │
    └───────────────┬───────────────┘
                     ▼
    ┌───────────────────────────────┐
    │ SignInService                 │
    └───────────────┬───────────────┘
            ┌────────┴────────┐
            ▼                 ▼
    ┌───────────────┐  ┌──────────────────┐
    │ user storage  │  │ token issuer     │
    └───────────────┘  └──────────────────┘

Purpose:
    Exchange a credential — or the configured guest door — for a bearer token,
    and let a client confirm the identity behind a token it already holds.

Two of these routes are unauthenticated by necessity: ``/options`` is how a
client discovers which doors exist, and ``/sign-in`` is the door itself. Both
are therefore written to reveal nothing beyond what a caller must know to
authenticate. ``/session`` requires a token, because its whole purpose is to
describe one.

Rationale for living outside `management_routers/`:
    Everything in that package requires an authenticated caller. Placing an
    unauthenticated route beside them would make "this package is guarded" stop
    being true at a glance, which is exactly the kind of assumption a reviewer
    makes without re-checking.

Author: Shubham Singh
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.exception_handlers import translate_management_error, translate_sign_in_error
from app.api.management_dependencies import get_user_service
from app.api.sign_in_dependencies import get_sign_in_service
from app.auth import AuthTokenPayload, get_current_user
from app.core.exceptions import LLMServiceError
from app.schemas.sign_in_schema import (
    SessionResponse,
    SignedInUser,
    SignInOptionsResponse,
    SignInRequest,
)
from app.services import UserService
from app.services.authentication import AuthenticatedSession, SignInService

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])


# Stage A:1 - Tell an unauthenticated client which sign-in doors exist here.
@router.get("/options", response_model=SignInOptionsResponse)
async def read_sign_in_options(
    service: Annotated[SignInService, Depends(get_sign_in_service)],
) -> SignInOptionsResponse:
    """Report which sign-in methods this deployment offers.

    Args:
        service: Sign-in business service.

    Returns:
        SignInOptionsResponse: Availability flags for each sign-in method.
    """
    return SignInOptionsResponse(
        password_sign_in_enabled=True,
        guest_sign_in_enabled=service.guest_enabled,
    )


# Stage A:2 - Verify a username and password, then return a session token.
@router.post("/sign-in", response_model=SessionResponse)
async def sign_in(
    body: SignInRequest,
    service: Annotated[SignInService, Depends(get_sign_in_service)],
) -> SessionResponse:
    """Exchange a username and password for a bearer token.

    Args:
        body: Credential payload.
        service: Sign-in business service.

    Returns:
        SessionResponse: Issued token and the identity behind it.

    Raises:
        DomainHTTPException: 401 for a rejected credential, 429 once the
            failed-attempt budget for that username is spent.
    """
    try:
        session = await service.sign_in_with_password(
            body.username,
            body.password.get_secret_value(),
        )
    except LLMServiceError as exc:
        translate_sign_in_error(exc)
    return _to_response(session)


# Stage A:3 - Open a superuser session with no credential, where configuration allows it.
@router.post("/guest-session", response_model=SessionResponse)
async def start_guest_session(
    service: Annotated[SignInService, Depends(get_sign_in_service)],
) -> SessionResponse:
    """Issue an unauthenticated guest superuser session.

    Available only where configuration enables it; the settings composition
    root refuses to start a production process with it enabled.

    Args:
        service: Sign-in business service.

    Returns:
        SessionResponse: Issued token for the configured guest identity.

    Raises:
        DomainHTTPException: 403 when this deployment offers no guest door.
    """
    try:
        session = await service.sign_in_as_guest()
    except LLMServiceError as exc:
        translate_sign_in_error(exc)
    return _to_response(session)


# Stage A:4 - Confirm a held token is still valid and say who it belongs to.
@router.get("/session", response_model=SignedInUser)
async def read_current_session(
    current_user: Annotated[AuthTokenPayload, Depends(get_current_user)],
    service: Annotated[UserService, Depends(get_user_service)],
) -> SignedInUser:
    """Describe the identity behind the presented bearer token.

    Lets a client restore a session after a page reload without storing user
    details alongside the token, and gives it one call that distinguishes "my
    token expired" from "the API is down".

    The role comes from the token rather than the freshly read row, because the
    token is what authorization will actually enforce for this session. Showing
    a role the caller does not yet hold would be a lie the UI acts on.

    Args:
        current_user: Identity decoded from the caller's token.
        service: User business service, used to resolve the display name.

    Returns:
        SignedInUser: Identity of the authenticated caller.

    Raises:
        DomainHTTPException: 404 when the token names a user who no longer exists.
    """
    try:
        row = await service.get_user(current_user.user_id)
    except LLMServiceError as exc:
        translate_management_error(exc)
    return SignedInUser(
        user_id=current_user.user_id,
        username=str(row.get("username", "")),
        role=current_user.role,
        # A token carries no "was this a guest session" claim, and inventing one
        # would change the claim set the validator enforces. The dashboard keeps
        # the flag from the sign-in response for display purposes.
        is_guest=False,
    )


def _to_response(session: AuthenticatedSession) -> SessionResponse:
    """Project a service result onto the wire contract."""
    return SessionResponse(
        access_token=session.access_token,
        expires_at=session.expires_at,
        user=SignedInUser(
            user_id=session.user_id,
            username=session.username,
            role=session.role,
            is_guest=session.is_guest,
        ),
    )


__all__ = ["router"]
