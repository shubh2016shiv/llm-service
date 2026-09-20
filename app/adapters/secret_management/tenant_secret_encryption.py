"""
Tenant secret encryption — the lockbox helpers
===============================================

What this file is for
---------------------
Provider API keys must never be stored in plain text. This file provides
the two operations that make encrypted storage possible:

    encrypt_api_key(plaintext, master_key, tenant_id, reference) -> locked box
    decrypt_api_key(locked box, master_key, tenant_id, reference) -> plaintext

Management tooling calls the first when a key is stored; the
AesGcmSecretStore calls the second when a key is needed at runtime.

The jargon, in plain words
--------------------------
    Master key   = the ONE secret key that protects everything. It lives
                   in an environment variable or a secrets manager, never
                   in source code.
    HKDF         = a key-copying machine. From the master key it mints a
                   DIFFERENT key for each tenant. Same inputs always give
                   the same key (which decryption relies on), but two
                   different tenants always get different keys.
    Nonce        = a one-time random serial number, freshly generated for
                   every encryption. AES-GCM demands a new one each time.
    AES-256-GCM  = a lockbox that does two jobs at once: it hides the
                   contents, and it seals them so any tampering is
                   detected (decryption then fails loudly).
    base64url    = a way of turning raw bytes into safe text characters,
                   so the locked box can sit in a normal text column.

Encryption flow:
    1. Mint the tenant's key from the master key.
    2. Generate a fresh nonce.
    3. Seal the plaintext into the lockbox (AES-GCM).
    4. Glue the nonce to the front of the locked box (decryption needs
       it) and turn the whole thing into base64url text for storage.

Decryption flow:
    1. Turn the base64url text back into bytes.
    2. Split off the nonce (front) from the locked box (rest).
    3. Mint the SAME tenant key from the master key.
    4. Open the lockbox and verify the seal — any tampering raises.

Author: Shubham Singh
"""

# This line makes every type hint below a lazy string. (Boilerplate.)
from __future__ import annotations

# base64 = the bytes-to-text encoding used for storage.
import base64

# os = the operating system; here only for os.urandom, which produces
# cryptographically safe random bytes for the nonce.
import os

# The cryptography library's building blocks:
# hashes = SHA-256 and friends (used by HKDF as its mixing function).
# AESGCM = the lockbox itself.
# HKDF   = the key-copying machine.
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# An AES-256 key is exactly 32 bytes long.
AES_256_KEY_BYTES: int = 32
_ENVELOPE_VERSION = "v1"


def decode_master_key(master_key_b64: str) -> bytes:
    """Decode and validate the base64-encoded master key.

    Shared by every caller that needs raw master key bytes (AesGcmSecretStore,
    PrefixDispatchingSecretStore, and the management services that encrypt a
    credential at write time), so master-key validation only lives in one
    place.

    Raises:
        ValueError: Not valid base64, or not exactly AES_256_KEY_BYTES long.
    """
    try:
        decoded = base64.b64decode(master_key_b64, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("Master key must be valid base64.") from exc
    if len(decoded) != AES_256_KEY_BYTES:
        raise ValueError(
            f"Master key must be exactly {AES_256_KEY_BYTES} bytes after base64 decoding, "
            f"got {len(decoded)} bytes."
        )
    return decoded
# AES-GCM's recommended nonce size is 12 bytes.
_NONCE_BYTES: int = 12
# A fixed label mixed into every key derivation, so keys minted for THIS
# purpose can never be confused with keys minted for another purpose by
# the same master key. (Cryptographers call this "domain separation".)
_HKDF_INFO: bytes = b"llm-provider-service:tenant-api-key"


def derive_tenant_key(master_key_bytes: bytes, tenant_id: str) -> bytes:
    """Mint the per-tenant key from the master key.

    Why not just use the master key directly for every tenant?
        If one tenant's data leaks (or one tenant's key is exposed), a
        shared master key would put every tenant's data at risk at once.
        Per-tenant keys shrink that "blast radius": each tenant has its
        own key, so one leak touches one tenant.

    Deterministic on purpose:
        The same master key + tenant id ALWAYS mints the same key. That
        is not a bug — decryption needs to reproduce the exact key the
        encryption used.

    Args:
        master_key_bytes: The master key (exactly 32 bytes).
        tenant_id: Which tenant's key to mint.

    Returns:
        A 32-byte key unique to this tenant.

    Example:
        >>> master_key = b"x" * 32
        >>> key_a = derive_tenant_key(master_key, "tenant-a")
        >>> key_b = derive_tenant_key(master_key, "tenant-b")
        >>> key_a != key_b
        True
    """
    if len(master_key_bytes) != AES_256_KEY_BYTES:
        raise ValueError(f"Master key must be exactly {AES_256_KEY_BYTES} bytes.")
    if not tenant_id:
        raise ValueError("tenant_id must not be empty.")
    # Configure the key-copying machine: SHA-256 as the mixer, 32-byte
    # output, the tenant id as the "salt" (the ingredient that makes each
    # tenant's copy different), and the fixed purpose label.
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=AES_256_KEY_BYTES,
        salt=tenant_id.encode("utf-8"),
        info=_HKDF_INFO,
    )
    # Mint the key.
    return hkdf.derive(master_key_bytes)


def encrypt_api_key(
    plaintext: str,
    master_key_bytes: bytes,
    tenant_id: str,
    *,
    secret_reference: str,
) -> str:
    """Seal an API key into a text-shaped lockbox for storage.

    What comes back:
        base64url(nonce glued to the sealed box) — a plain text string
        that is safe to store in a text column or JSON field.

    Args:
        plaintext: The API key to protect.
        master_key_bytes: The master key (exactly 32 bytes).
        tenant_id: Which tenant the key belongs to.

    Returns:
        The encrypted payload as base64url text.

    Example:
        >>> master = b"x" * 32
        >>> blob = encrypt_api_key(
        ...     "sk-live-example", master, "tenant-a", secret_reference="openai/default"
        ... )
        >>> isinstance(blob, str)
        True
    """
    if not plaintext:
        raise ValueError("plaintext secret must not be empty.")
    associated_data = _associated_data(tenant_id, secret_reference)
    # Mint the tenant's key.
    derived_key = derive_tenant_key(master_key_bytes, tenant_id)
    # Build the lockbox with that key.
    aesgcm = AESGCM(derived_key)
    # A fresh random serial number for THIS sealing. Reusing a nonce with
    # the same key would break the encryption's guarantees, so this must
    # be random every time (os.urandom is cryptographically safe).
    nonce = os.urandom(_NONCE_BYTES)
    # Seal the key. The trailing None is "extra data we want protected
    # but not hidden" — we have none.
    encrypted = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), associated_data)
    # Glue the serial number to the front of the sealed box (decryption
    # needs it) and turn the raw bytes into safe text characters.
    encoded_payload = base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")
    return f"{_ENVELOPE_VERSION}.{encoded_payload}"


def decrypt_api_key(
    ciphertext_b64: str,
    master_key_bytes: bytes,
    tenant_id: str,
    *,
    secret_reference: str,
) -> str:
    """Open a lockbox and hand back the plaintext key inside.

    Args:
        ciphertext_b64: The base64url payload produced by encrypt_api_key.
        master_key_bytes: The master key (exactly 32 bytes).
        tenant_id: The tenant the key was sealed for.

    Returns:
        The plaintext API key.

    Raises:
        ValueError: The payload is malformed or too short.
        cryptography.exceptions.InvalidTag: The seal check failed — wrong
            key, wrong tenant, or tampered data.

    Best practice:
        Treat the returned plaintext as sensitive: never log it, never
        persist it, and keep it in memory as briefly as possible.

    Example:
        >>> master = b"x" * 32
        >>> blob = encrypt_api_key(
        ...     "sk-live-example", master, "tenant-a", secret_reference="openai/default"
        ... )
        >>> decrypt_api_key(
        ...     blob, master, "tenant-a", secret_reference="openai/default"
        ... )
        'sk-live-example'
    """
    version, separator, encoded_payload = ciphertext_b64.partition(".")
    if separator != "." or version != _ENVELOPE_VERSION:
        raise ValueError(f"Ciphertext must use the {_ENVELOPE_VERSION!r} envelope format.")
    try:
        # Turn the text back into bytes. validate=True rejects anything
        # that is not proper base64, so garbage input fails here with a
        # clean error instead of producing junk bytes.
        raw = base64.b64decode(encoded_payload.encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise ValueError("Ciphertext must be valid base64url data.") from exc
    # The payload must at least contain a nonce; anything shorter cannot
    # possibly be a real lockbox.
    if len(raw) <= _NONCE_BYTES:
        raise ValueError(f"Ciphertext too short: expected >{_NONCE_BYTES} bytes, got {len(raw)}.")
    # Split: the first 12 bytes are the serial number, the rest is the box.
    nonce = raw[:_NONCE_BYTES]
    ciphertext = raw[_NONCE_BYTES:]
    # Mint the SAME tenant key the encryption used (same recipe, same
    # result) and rebuild the lockbox.
    derived_key = derive_tenant_key(master_key_bytes, tenant_id)
    aesgcm = AESGCM(derived_key)
    # Open the box. If the seal does not check out, this raises
    # InvalidTag — which is exactly what we want on tampering.
    associated_data = _associated_data(tenant_id, secret_reference)
    try:
        return aesgcm.decrypt(nonce, ciphertext, associated_data).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Decrypted secret is not valid UTF-8 text.") from exc


def _associated_data(tenant_id: str, secret_reference: str) -> bytes:
    """Bind a ciphertext to its tenant, reference, purpose, and format version.

    Associated data is authenticated but not encrypted. Moving a valid blob to
    another database row changes this value, so AES-GCM rejects the swap.
    """
    if not tenant_id:
        raise ValueError("tenant_id must not be empty.")
    if not secret_reference:
        raise ValueError("secret_reference must not be empty.")
    return (
        f"llm-provider-service:tenant-api-key:{_ENVELOPE_VERSION}\0"
        f"{tenant_id}\0{secret_reference}"
    ).encode()
