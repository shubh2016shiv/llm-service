"""
Password Hashing
================

PBKDF2-HMAC-SHA256 hashing and verification for stored user passwords.

What this module does:
    Turns a plaintext password into the self-describing string persisted in
    ``users.password_hash``, and checks a later attempt against it.

Why it is its own module:
    Hashing was written for user creation; verification arrived with sign-in.
    Two copies of the encoding would be two chances to disagree about the
    format, and a disagreement here means nobody can log in. One module owns
    the string layout, so the writer and the reader cannot drift apart.

Encoded format:
    ``pbkdf2_sha256$<iterations>$<salt hex>$<derived key hex>``

    The parameters travel with the hash rather than living in a config value,
    so raising the iteration count later does not invalidate existing rows:
    each row still states how it was made.

Architecture:
-------------
    ┌────────────────┐     ┌──────────────────────┐     ┌──────────────────┐
    │  UserService   │────▶│  [This Module]       │◀────│  SignInService   │
    │  (create)      │     │  hash / verify       │     │  (authenticate)  │
    └────────────────┘     └──────────────────────┘     └──────────────────┘

Dependencies:
    - hashlib, secrets, hmac — standard library primitives only; no third-party
      password library is present in this project's dependencies.

Author: Shubham Singh
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets

logger = logging.getLogger(__name__)

_ALGORITHM_LABEL = "pbkdf2_sha256"
_PBKDF2_ITERATIONS = 390_000
_SALT_BYTES = 16
_FIELD_COUNT = 4

# A verification attempt against a user who does not exist still has to spend
# the same time as a real one, or the response time itself reveals which
# usernames are registered. This hash is never matched; it exists only to be
# checked against, and is built once because deriving it costs ~100ms.
_TIMING_DECOY_HASH = None


def hash_password(password: str) -> str:
    """Hash a plaintext password using PBKDF2-HMAC-SHA256.

    PBKDF2 is a key-derivation function designed to make brute-force
    attacks expensive by requiring many hash iterations per attempt.
    The returned value embeds algorithm parameters so verification logic
    can evolve without separate schema fields.

    Args:
        password: Plaintext password to protect. Must be non-empty.

    Returns:
        Encoded hash string safe to persist.

    Raises:
        ValueError: If password is empty.

    Example:
        >>> encoded = hash_password("correct horse battery staple")
        >>> encoded.startswith("pbkdf2_sha256$")
        True
    """
    if not password:
        raise ValueError("password must be non-empty")
    salt = secrets.token_bytes(_SALT_BYTES)
    derived_key = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _PBKDF2_ITERATIONS,
    )
    return f"{_ALGORITHM_LABEL}${_PBKDF2_ITERATIONS}${salt.hex()}${derived_key.hex()}"


def verify_password(password: str, encoded_hash: str) -> bool:
    """Check a plaintext password against a stored encoded hash.

    Args:
        password: Plaintext attempt supplied by the caller.
        encoded_hash: Value previously produced by ``hash_password``.

    Returns:
        True when the password matches, False for a mismatch, an empty input,
        or a stored value this module cannot parse.

    Example:
        >>> encoded = hash_password("s3cret")
        >>> verify_password("s3cret", encoded)
        True
        >>> verify_password("wrong", encoded)
        False
    """
    parsed = _parse_encoded_hash(encoded_hash)
    if parsed is None or not password:
        return False
    iterations, salt, expected_key = parsed

    candidate_key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    # compare_digest, not ==, because a byte-by-byte comparison returns sooner
    # for a wrong first byte than a wrong last byte, and that timing difference
    # can be measured to reconstruct the expected value one byte at a time.
    return hmac.compare_digest(candidate_key, expected_key)


def spend_verification_time() -> None:
    """Burn the same work a real verification costs, and discard the result.

    Called when no user matched the supplied username. Returning immediately in
    that case would make "unknown username" measurably faster than "wrong
    password", which lets an attacker enumerate valid accounts with a stopwatch
    and without ever guessing a password correctly.
    """
    global _TIMING_DECOY_HASH
    if _TIMING_DECOY_HASH is None:
        _TIMING_DECOY_HASH = hash_password(secrets.token_urlsafe(32))
    verify_password(secrets.token_urlsafe(32), _TIMING_DECOY_HASH)


def _parse_encoded_hash(encoded_hash: str) -> tuple[int, bytes, bytes] | None:
    """Split a stored hash into its parts, or return None if it is unusable.

    A malformed stored value is a data problem, not a caller problem, so this
    logs and reports failure rather than raising: a single corrupt row should
    deny that one sign-in, not return a 500 that looks like an outage.
    """
    if not encoded_hash:
        return None
    fields = encoded_hash.split("$")
    if len(fields) != _FIELD_COUNT or fields[0] != _ALGORITHM_LABEL:
        logger.warning(
            "Stored password hash has an unrecognized format",
            extra={"field_count": len(fields)},
        )
        return None
    try:
        iterations = int(fields[1])
        salt = bytes.fromhex(fields[2])
        expected_key = bytes.fromhex(fields[3])
    except ValueError:
        logger.warning("Stored password hash has unparsable parameters")
        return None
    if iterations < 1 or not salt or not expected_key:
        logger.warning("Stored password hash has empty or non-positive parameters")
        return None
    return iterations, salt, expected_key
