"""
Provider Credential Contracts
=============================

Abstract interface for secret retrieval backends.

Why an interface:
    Callers should depend on behavior ("get me secret value X for tenant Y")
    rather than storage details (Vault, encrypted DB row, environment variable).
    This follows dependency inversion and keeps provider code testable.

Step-by-step relation:
    1. Caller receives ``secret_reference`` from deployment/entitlement config.
    2. Caller invokes ``SecretStore.get_secret(...)``.
    3. Backend-specific implementation resolves/decrypts/fetches secret.
    4. Caller uses returned plaintext for request authentication.

Author: Shubham Singh
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class SecretStore(ABC):
    """Async contract for retrieving plaintext provider credentials.

    In plain terms:
        Any class implementing this interface answers one question:
        "Given a reference and tenant context, what is the usable secret value?"

    Why this helps new developers:
        You can understand secret retrieval call sites without knowing backend
        details first, because every backend exposes the same method.

    Example:
        >>> # backend = VaultSecretStore(...)
        >>> # plaintext_key = await backend.get_secret("providers/openai/default", tenant_id="...")
    """

    @abstractmethod
    async def get_secret(self, secret_reference: str, *, tenant_id: str) -> str:
        """Return plaintext secret value for a reference.

        Args:
            secret_reference: Opaque pointer from config (not the secret itself).
            tenant_id: Tenant identifier used for scoping or key derivation.

        Returns:
            Plaintext credential value.

        Rationale for arguments:
            ``secret_reference`` identifies *where* the secret is stored,
            while ``tenant_id`` preserves tenant scoping for backends that
            derive tenant-specific keys.

        Raises:
            KeyError: When reference does not exist.
            ValueError: When stored value is invalid or decryption fails.
            PermissionError: When backend access is denied.
        """

    async def aclose(self) -> None:
        """Close backend resources if needed (default no-op).

        Network backends should override this to close clients cleanly.
        Sync/in-memory backends can keep the no-op implementation.
        """
        return
