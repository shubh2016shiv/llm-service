"""
Secret store — the contract
==========================

What this file is for
---------------------
This file declares the ONE question every secret backend must answer,
without saying anything about HOW. It is a "contract" (an abstract base
class): any class that inherits from ``SecretStore`` promises to provide
``get_secret(...)``. The rest of the app depends only on this contract,
so swapping the backend (environment -> Vault) changes nothing for
callers.

In plain words:
    Callers say "give me secret X for tenant Y". They do not care
    whether the answer comes from an environment variable, a decrypted
    record, or a Vault server — and this contract is what makes that
    true.

Author: Shubham Singh
"""

# This line makes every type hint below a lazy string, so hints can
# mention classes before they are imported. (Standard boilerplate.)
from __future__ import annotations

# ABC / abstractmethod = Python's way of saying: "this class is only a
# contract — you cannot create it directly, and subclasses MUST implement
# the methods marked with @abstractmethod".
from abc import ABC, abstractmethod


class SecretStore(ABC):
    """The one question every secret backend must answer.

    Any subclass promises:
        "Given a secret reference and the tenant asking for it, I will
        return the usable plaintext key — or raise a clear, actionable
        error."

    Example (with a made-up backend):
        >>> # backend = VaultSecretStore(...)
        >>> # plaintext_key = await backend.get_secret(
        >>> #     "providers/openai/default", tenant_id="..."
        >>> # )
    """

    @abstractmethod
    async def get_secret(self, secret_reference: str, *, tenant_id: str) -> str:
        """Return the plaintext secret value for a reference.

        Args:
            secret_reference: An opaque pointer from configuration that
                says WHERE the secret lives (never the secret itself).
            tenant_id: Which tenant is asking. Backends use this for
                scoping or key derivation (the environment backend
                accepts it for signature consistency and logs it only).

        Returns:
            The plaintext credential value.

        Raises (the shared vocabulary every backend uses):
            KeyError: The reference does not exist.
            ValueError: The stored value is invalid or decryption failed.
            PermissionError: The backend denied access.
        """

    async def aclose(self) -> None:
        """Release any backend resources. Default: nothing to release.

        Networked backends (Vault) override this to close their HTTP
        client during app shutdown. Simple in-memory backends keep this
        no-op.
        """
        return
