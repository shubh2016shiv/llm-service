# Authentication services

## The short version

This package signs a person in.

It accepts either a username and password or the explicitly enabled local
guest route. If the check succeeds, it returns a signed access token describing
who the caller is and which platform role they have.

This package answers:

> Who is this caller?

It does not answer:

> Is this caller allowed to perform a particular action?

That later question belongs to [`app/auth`](../../auth), which validates tokens
and enforces roles after sign-in.

## Vocabulary

| Term | Plain-language meaning |
| --- | --- |
| Authentication | Proving who a caller is. Here that normally means checking a username and password. |
| Authorization | Deciding what an already authenticated caller may do. This is handled elsewhere. |
| Access token | A signed, short-lived string the client sends with later requests. |
| Password hash | A one-way representation used to check a password without storing the plaintext password. |
| Attempt budget | The number of failed sign-ins allowed during a time window. |
| Guest session | A development-only session issued without a password when configuration explicitly enables it. |

## Normal password sign-in

```text
Client sends username + password
    |
    v
SignInAttemptLimiter checks whether this username is temporarily blocked
    |
    v
UserPersistence reads the one credential-bearing user row
    |
    v
SignInService verifies the password in a worker thread
    |
    +-- failure -> record failed attempt -> return one generic credential error
    |
    +-- success -> clear failed attempts -> issue signed access token
```

Password hashing is deliberately expensive. It runs in a worker thread so one
sign-in does not pause unrelated asynchronous requests.

## Why every bad credential looks the same

An unknown username, incorrect password, suspended account, or unusable stored
role all produce the same public `InvalidCredentialsError`.

Returning different messages would let an attacker discover which usernames
exist. Returning quickly for an unknown username would leak the same fact
through response timing, so the service performs a decoy password verification
when no user row exists.

The internal log may record the reason, but the caller receives no account
existence hint. Never add the submitted password or stored hash to a log.

## Failed-attempt limiting

The limiter is checked before the user database is queried. When the failure
budget has been spent, the service raises `TooManySignInAttemptsError` with a
retry delay.

After a failed verification, `record_failure()` increments the budget. After a
successful verification, `clear()` removes it. The limiter owns Redis failure
handling; `SignInService` should not duplicate Redis logic.

## Guest sign-in

Guest sign-in skips credential verification and therefore deserves special
care:

- it works only when guest access and a guest user ID are configured;
- it emits a warning-level log because the session is unauthenticated;
- application settings refuse to start production with guest access enabled;
- it raises `GuestSessionDisabledError` when the door is closed.

Do not weaken these checks to make local setup more convenient.

## Main output

`AuthenticatedSession` contains:

- the signed access token and its expiry;
- user ID and username;
- platform role;
- whether this is a guest session.

The API converts this internal value into its response schema. This service
does not choose HTTP status codes.

## File map

1. [`sign_in_service.py`](sign_in_service.py) — password/guest flows and token
   issuance orchestration.
2. [`__init__.py`](__init__.py) — the package's public imports.
3. [`../users/password_hashing.py`](../users/password_hashing.py) — the shared
   hash format used by user creation and sign-in verification.
4. [`../../auth/token_issuer.py`](../../auth/token_issuer.py) — creates the
   signed token after identity is proven.
5. [`../../adapters/cache/sign_in_attempt_limiter.py`](../../adapters/cache/sign_in_attempt_limiter.py)
   — owns failed-attempt state in Redis.
6. [`../../api/auth_router.py`](../../api/auth_router.py) — HTTP endpoints that
   call this package.

Focused tests live in
[`../../../tests/unit/services/test_sign_in_service.py`](../../../tests/unit/services/test_sign_in_service.py)
and
[`../../../tests/unit/services/test_password_hashing.py`](../../../tests/unit/services/test_password_hashing.py).

## Rules to preserve

1. Never reveal whether a username exists through messages or obvious timing.
2. Never log or return plaintext passwords or password hashes.
3. Check the attempt budget before reading credentials.
4. Clear the budget only after successful authentication.
5. Keep guest access impossible in production.
6. Keep authentication separate from later authorization decisions.

