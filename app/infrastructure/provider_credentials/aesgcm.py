"""
AES-GCM Secret Store
====================

Secret-store backend for encrypted credentials loaded from persistence.

What this store expects:
    - A base64-encoded 32-byte master key from environment/settings.
    - Encrypted credential payloads registered by ``secret_reference``.
    - Tenant ID at read time for tenant-scoped key derivation.

Step-by-step retrieval flow:
    1. Caller requests secret by reference and tenant ID.
    2. Store looks up encrypted payload in in-memory registry.
    3. Store decrypts payload using HKDF-derived tenant key + AES-GCM.
    4. Plaintext is returned to caller for immediate runtime use.

Best-practice notes:
    - Persist ciphertext only, never plaintext.
    - Keep master key out of source code and commit history.
    - Keep plaintext in memory for the shortest practical duration.

Author: Shubham Singh
"""

from __future__ import annotations

import base64
import logging

from cryptography.exceptions import InvalidTag

from app.infrastructure.provider_credentials.contracts import SecretStore
from app.infrastructure.provider_credentials.crypto import _KEY_BYTES, decrypt_api_key

logger = logging.getLogger(__name__)


class AESGCMSecretStore(SecretStore):
    """Resolve provider credentials from encrypted-at-rest payloads.

    In plain language:
        This class is a runtime "decryption gateway". It stores encrypted
        credential blobs in memory, and when asked for one, it derives the
        tenant-specific key and decrypts the blob into a usable API key.

    Why tenant-scoped derivation matters:
        Two tenants with the same reference name still decrypt with different
        derived keys because ``tenant_id`` participates in key derivation.
        That separation reduces cross-tenant blast radius.

    Example:
        >>> # master_key_b64 = os.environ["ENCRYPTION_MASTER_KEY"]
        >>> # store = AESGCMSecretStore(master_key_b64)
        >>> # store.register_secret("providers/openai/default", "<ciphertext>")
        >>> # key = await store.get_secret("providers/openai/default", tenant_id="tenant-a")
    """

    def __init__(
        self,
        master_key_b64: str,
        encrypted_secrets: dict[str, str] | None = None,
    ) -> None:
        """Decode/validate master key and initialize encrypted secret registry.

        Args:
            master_key_b64: Base64-encoded 32-byte master key.
            encrypted_secrets: Optional preloaded mapping of reference to
                base64url ciphertext payload.

        Raises:
            ValueError: If decoded master key length is not 32 bytes.
        """
        self._master_key_bytes: bytes = base64.b64decode(master_key_b64)
        if len(self._master_key_bytes) != _KEY_BYTES:
            raise ValueError(
                f"Master key must be exactly {_KEY_BYTES} bytes after base64 decoding, "
                f"got {len(self._master_key_bytes)} bytes."
            )
        self._encrypted_secrets: dict[str, str] = dict(encrypted_secrets or {})

    def register_secret(self, reference: str, ciphertext_b64: str) -> None:
        """Register encrypted secret payload for a reference key.

        Typically called during startup/bootstrap after loading encrypted rows
        from database persistence.

        Rationale:
            Keeping registration explicit makes secret population observable and
            testable instead of hidden behind implicit global state.
        """
        self._encrypted_secrets[reference] = ciphertext_b64

    async def get_secret(self, secret_reference: str, *, tenant_id: str) -> str:
        """Resolve and decrypt one secret for a tenant scope.

        Raises:
            KeyError: If reference is not registered.
            ValueError: If decryption fails or ciphertext is invalid.

        Step-by-step:
            1. Look up ciphertext by reference.
            2. Derive tenant key and decrypt via AES-GCM helper.
            3. Return plaintext for immediate caller use.
        """
        ciphertext_b64: str | None = self._encrypted_secrets.get(secret_reference)
        if ciphertext_b64 is None:
            raise KeyError(
                f"Secret reference {secret_reference!r} not found in store. "
                "Ensure it was registered at startup."
            )
        try:
            plaintext = decrypt_api_key(
                ciphertext_b64,
                master_key_bytes=self._master_key_bytes,
                tenant_id=tenant_id,
            )
        except (InvalidTag, ValueError) as exc:
            raise ValueError(
                f"Decryption failed for secret_reference={secret_reference!r}. "
                "Possible causes: wrong master key, wrong tenant_id, or tampered ciphertext."
            ) from exc

        logger.debug(
            "Secret decrypted successfully",
            extra={"secret_reference": secret_reference, "tenant_id": tenant_id},
        )
        return plaintext
