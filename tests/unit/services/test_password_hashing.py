"""Password hashing tests: the stored format must stay readable by sign-in."""

from __future__ import annotations

import pytest

from app.services.users.password_hashing import (
    hash_password,
    spend_verification_time,
    verify_password,
)


def test_verify_password_when_password_matches_returns_true() -> None:
    encoded = hash_password("correct horse battery staple")

    assert verify_password("correct horse battery staple", encoded) is True


def test_verify_password_when_password_differs_returns_false() -> None:
    encoded = hash_password("correct horse battery staple")

    assert verify_password("correct horse battery stapler", encoded) is False


def test_hash_password_returns_the_format_already_stored_in_the_database() -> None:
    # Rows created before hashing moved into this module use exactly this
    # layout. A change here silently locks every existing user out.
    algorithm, iterations, salt_hex, key_hex = hash_password("s3cret").split("$")

    assert algorithm == "pbkdf2_sha256"
    assert int(iterations) >= 100_000
    assert len(bytes.fromhex(salt_hex)) == 16
    assert len(bytes.fromhex(key_hex)) == 32


def test_hash_password_when_called_twice_returns_different_values() -> None:
    # Two users with the same password must not share a stored value, or one
    # cracked hash reveals every account that reused that password.
    assert hash_password("same-password") != hash_password("same-password")


def test_verify_password_when_stored_hash_uses_legacy_layout_parses_without_raising() -> None:
    # Shaped exactly like the values UserService._hash_password wrote before
    # hashing moved here, so this pins the parser against the stored layout.
    legacy = (
        "pbkdf2_sha256$390000$c2c8fbbd5e4b4b0a9f7d4e1c8a3b6d90$"
        "9d1bd90aa4b45ba8ef4a1e54a99ed4e34be4b5d9dd3e2f4a67d5b1e8c0f3a2d7"
    )

    # The digest above is not a real derivation of that password, so this
    # asserts only that a well-formed legacy string parses and compares
    # without raising — the format contract, not the secret.
    assert verify_password("legacy-password", legacy) is False


@pytest.mark.parametrize(
    "malformed",
    [
        "",
        "not-a-hash",
        "pbkdf2_sha256$390000$deadbeef",
        "bcrypt$12$abc$def",
        "pbkdf2_sha256$notanumber$aa$bb",
        "pbkdf2_sha256$390000$zz$bb",
        "pbkdf2_sha256$0$aa$bb",
    ],
)
def test_verify_password_when_stored_value_is_malformed_returns_false(malformed: str) -> None:
    # One corrupt row should deny that sign-in, not raise a 500 that reads
    # like an outage.
    assert verify_password("any-password", malformed) is False


def test_verify_password_when_attempt_is_empty_returns_false() -> None:
    encoded = hash_password("s3cret")

    assert verify_password("", encoded) is False


def test_hash_password_when_password_is_empty_raises_value_error() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        hash_password("")


def test_spend_verification_time_when_called_repeatedly_never_raises() -> None:
    # Used on the unknown-username path purely for its cost; it must never
    # raise, because doing so would turn a normal failed login into a 500.
    spend_verification_time()
    spend_verification_time()
