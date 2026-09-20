"""
AES-GCM secret store — the decryption gateway
==============================================

What this file is for
---------------------
A backend that keeps encrypted API keys in memory and decrypts them on
demand. Management tooling encrypts keys (using tenant_secret_encryption)
and registers the locked boxes here at startup; at runtime this backend
opens the right box and hands back the plaintext key.

What it expects at startup:
    - a base64-encoded 32-byte master key (from settings/environment),
    - the encrypted payloads, one per secret reference.

Flow for one get_secret call:
    1. Look up the locked box by reference.
    2. Mint the tenant's key (HKDF) and open the box (AES-GCM).
    3. Hand the plaintext to the caller for immediate use.

Best-practice notes:
    - Store ciphertext only, never plaintext.
    - Keep the master key out of source code and commit history.
    - Keep plaintext in memory for the shortest practical time.

Author: Shubham Singh
"""

# This line makes every type hint below a lazy string. (Boilerplate.)
from __future__ import annotations

# logging  = writing to the application log.
import logging

# InvalidTag = the "seal check failed" error from the cryptography
# library (wrong key, wrong tenant, or tampered data).
from cryptography.exceptions import InvalidTag

# The contract this backend fulfils, and the lockbox helpers with the
# expected master-key size. Relative imports: the package works under any
# parent package name.
from .secret_store import SecretStore
from .tenant_secret_encryption import decode_master_key, decrypt_api_key

logger = logging.getLogger(__name__)


class AesGcmSecretStore(SecretStore):
    """Resolve provider credentials from encrypted-at-rest payloads.

    In plain words:
        A runtime "decryption gateway". It holds encrypted key blobs in
        memory; when asked for one, it mints the tenant-specific key and
        opens the blob into a usable API key.

    Why tenant-scoped keys matter:
        Two tenants can register the same reference name, and each still
        decrypts with their own derived key, because the tenant id takes
        part in the key recipe. One tenant's leaked key cannot open
        another tenant's box.

    Example:
        >>> # master_key_b64 = os.environ["ENCRYPTION_MASTER_KEY"]
        >>> # store = AesGcmSecretStore(master_key_b64)
        >>> # store.register_secret(
        >>> #     "providers/openai/default", "<ciphertext>", tenant_id="tenant-a"
        >>> # )
        >>> # key = await store.get_secret("providers/openai/default", tenant_id="tenant-a")
    """

    def __init__(
        self,
        master_key_b64: str,
        encrypted_secrets: dict[tuple[str, str], str] | None = None,
    ) -> None:
        """Decode and validate the master key, then load the registry.

        Everything is validated HERE, at startup, so a bad master key
        fails the app launch loudly instead of failing a provider call
        at 3 AM.

        Args:
            master_key_b64: The base64-encoded 32-byte master key.
            encrypted_secrets: Optional preloaded mapping of reference ->
                base64url ciphertext (normally loaded from persistence).

        Raises:
            ValueError: The master key is not valid base64, or is not
                exactly 32 bytes after decoding.
        """
        self._master_key_bytes = decode_master_key(master_key_b64)
        # Copy the preloaded registry into our own dict (a copy, so the
        # caller's dict can change later without affecting us).
        self._encrypted_secrets: dict[tuple[str, str], str] = {}
        for (tenant_id, reference), ciphertext in (encrypted_secrets or {}).items():
            self.register_secret(reference, ciphertext, tenant_id=tenant_id)

    def register_secret(
        self,
        reference: str,
        ciphertext_b64: str,
        *,
        tenant_id: str,
    ) -> None:
        """Add one encrypted payload to the registry.

        Normally called during startup/bootstrap after loading encrypted
        rows from the database. Keeping registration explicit (instead of
        hidden behind global state) makes secret population observable
        and testable.

        Args:
            reference: The secret reference the payload belongs to.
            ciphertext_b64: The base64url ciphertext from encryption.
            tenant_id: Tenant that owns both the reference and ciphertext.
        """
        if not reference or not tenant_id:
            raise ValueError("reference and tenant_id must not be empty.")
        # Authenticate the complete envelope now. Bad persisted data should
        # fail startup/registration, not the first live inference request.
        try:
            decrypt_api_key(
                ciphertext_b64,
                master_key_bytes=self._master_key_bytes,
                tenant_id=tenant_id,
                secret_reference=reference,
            )
        except (InvalidTag, ValueError) as exc:
            raise ValueError(
                f"Encrypted secret is invalid for tenant/reference {tenant_id!r}/{reference!r}."
            ) from exc
        self._encrypted_secrets[(tenant_id, reference)] = ciphertext_b64

    async def get_secret(self, secret_reference: str, *, tenant_id: str) -> str:
        """Open one lockbox and return the plaintext key inside.

        Step-by-step:
            1. Look up the locked box by reference.
            2. Mint the tenant key and open the box (AES-GCM).
            3. Return the plaintext for immediate caller use.

        Args:
            secret_reference: Which registered payload to open.
            tenant_id: Whose key to mint for opening it.

        Returns:
            The plaintext API key.

        Raises:
            KeyError: The reference was never registered.
            ValueError: The box could not be opened — wrong master key,
                wrong tenant id, or tampered ciphertext.
        """
        if not secret_reference or not tenant_id:
            raise ValueError("secret_reference and tenant_id must not be empty.")
        # Look up the locked box; None means "never registered".
        ciphertext_b64: str | None = self._encrypted_secrets.get((tenant_id, secret_reference))
        if ciphertext_b64 is None:
            raise KeyError(
                f"Secret reference {secret_reference!r} not found in store. "
                "Ensure it was registered at startup."
            )
        try:
            # Open the box with the tenant's minted key.
            plaintext = decrypt_api_key(
                ciphertext_b64,
                master_key_bytes=self._master_key_bytes,
                tenant_id=tenant_id,
                secret_reference=secret_reference,
            )
        except (InvalidTag, ValueError) as exc:
            # Seal check failed or the payload was malformed. Translate
            # into one clear error naming the likely causes, so the
            # on-call engineer is not left guessing.
            raise ValueError(
                f"Decryption failed for secret_reference={secret_reference!r}. "
                "Possible causes: wrong master key, wrong tenant_id, or tampered ciphertext."
            ) from exc

        # Log ONLY the reference and tenant — never the secret value.
        logger.debug(
            "Secret decrypted successfully",
            extra={"secret_reference": secret_reference, "tenant_id": tenant_id},
        )
        return plaintext
