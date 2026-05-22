"""
Credential Crypto Helpers
=========================

HKDF key derivation plus AES-256-GCM encryption/decryption helpers.

Goal:
    Store provider API keys encrypted at rest while allowing safe decryption
    on demand for authorized runtime use.

Jargon explained:
    - HKDF: key-derivation function that turns one master key into many
      purpose/scoped keys (here: one derived key per tenant).
    - Nonce: one-time random value used with AES-GCM; must be unique per
      encryption operation.
    - Authenticated encryption: cipher mode (AES-GCM) that provides both
      confidentiality and tamper detection.

Step-by-step encryption flow:
    1. Derive tenant-scoped key from master key + tenant_id via HKDF.
    2. Generate random nonce.
    3. Encrypt plaintext using AES-GCM.
    4. Store base64url(nonce || ciphertext) in persistence.

Step-by-step decryption flow:
    1. Decode base64url payload.
    2. Split nonce and ciphertext.
    3. Derive same tenant-scoped key via HKDF.
    4. Decrypt and verify integrity with AES-GCM.

Author: Shubham Singh
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_KEY_BYTES: int = 32
_NONCE_BYTES: int = 12
_HKDF_INFO: bytes = b"llm-provider-service:tenant-api-key"


def derive_tenant_key(master_key_bytes: bytes, tenant_id: str) -> bytes:
    """Derive tenant-specific AES-256 key using HKDF-SHA256.

    Why this is safer than direct key reuse:
        Using one master key directly for all tenants creates a large blast
        radius. Per-tenant derivation limits exposure if one derived key leaks.

    Example:
        >>> master_key = b"x" * 32
        >>> key_a = derive_tenant_key(master_key, "tenant-a")
        >>> key_b = derive_tenant_key(master_key, "tenant-b")
        >>> key_a != key_b
        True

    Additional note:
        The same ``master_key_bytes`` + ``tenant_id`` pair always produces the
        same derived key, which is required so decryption can reproduce it.
    """
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=_KEY_BYTES,
        salt=tenant_id.encode("utf-8"),
        info=_HKDF_INFO,
    )
    return hkdf.derive(master_key_bytes)


def encrypt_api_key(plaintext: str, master_key_bytes: bytes, tenant_id: str) -> str:
    """Encrypt plaintext API key and return base64url payload.

    Returned format:
        ``base64url(nonce || ciphertext)``

    Example:
        >>> master = b"x" * 32
        >>> blob = encrypt_api_key("sk-live-example", master, "tenant-a")
        >>> isinstance(blob, str)
        True

    Rationale:
        Returning a base64url string makes the encrypted payload easy to store
        in text columns or JSON fields without binary encoding issues.
    """
    derived_key = derive_tenant_key(master_key_bytes, tenant_id)
    aesgcm = AESGCM(derived_key)
    nonce = os.urandom(_NONCE_BYTES)
    encrypted = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")


def decrypt_api_key(ciphertext_b64: str, master_key_bytes: bytes, tenant_id: str) -> str:
    """Decrypt base64url AES-GCM payload back into plaintext API key.

    Raises:
        ValueError: When payload is malformed or too short.
        cryptography.exceptions.InvalidTag: When authentication check fails.

    Example:
        >>> master = b"x" * 32
        >>> blob = encrypt_api_key("sk-live-example", master, "tenant-a")
        >>> decrypt_api_key(blob, master, "tenant-a")
        'sk-live-example'

    Best practice:
        Treat returned plaintext as sensitive and avoid logging, persisting, or
        storing it in long-lived objects.
    """
    raw = base64.urlsafe_b64decode(ciphertext_b64.encode("ascii"))
    if len(raw) <= _NONCE_BYTES:
        raise ValueError(
            f"Ciphertext too short: expected >{_NONCE_BYTES} bytes, got {len(raw)}."
        )
    nonce = raw[:_NONCE_BYTES]
    ciphertext = raw[_NONCE_BYTES:]
    derived_key = derive_tenant_key(master_key_bytes, tenant_id)
    aesgcm = AESGCM(derived_key)
    return aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")
