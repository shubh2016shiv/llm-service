"""
Authentication Services — establishing who a caller is.

Architecture:
-------------
    ┌────────────────┐     ┌────────────────────────┐     ┌──────────────────┐
    │  auth router   │────▶│  SignInService         │────▶│  UserPersistence │
    │  (api/)        │     │  (this package)        │     │  (database/)     │
    └────────────────┘     └────────────────────────┘     └──────────────────┘

Why this is separate from ``app/auth``:
    ``app/auth`` answers "may this caller do X" — it verifies tokens and ranks
    roles for requests that already carry an identity. This package answers the
    earlier question, "who is this caller at all", by checking a password or
    opening the configured guest door. Keeping them apart means the
    authorization path never imports password handling.

Dependencies:
    - app.database.users — supplies stored credentials.
    - app.auth.token_issuer — signs the session token.

Author: Shubham Singh
"""

from app.services.authentication.sign_in_service import AuthenticatedSession, SignInService

__all__ = ["AuthenticatedSession", "SignInService"]
