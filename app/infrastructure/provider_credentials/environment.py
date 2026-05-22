"""
Environment Secret Store
========================

Environment-variable-backed secret store implementation.

When to use:
    Best for local development, test, and CI where secrets are injected via
    environment variables. Not recommended as the long-term production backend.

Step-by-step flow:
    1. ``secret_reference`` is treated as environment variable name.
    2. Store reads ``os.environ[secret_reference]``.
    3. Missing value raises ``KeyError`` with actionable guidance.

Author: Shubham Singh
"""

from __future__ import annotations

import logging
import os

from app.infrastructure.provider_credentials.contracts import SecretStore

logger = logging.getLogger(__name__)


class EnvironmentSecretStore(SecretStore):
    """Resolve secrets from environment variables.

    What a new developer should understand:
        This class does not encrypt/decrypt anything. It simply maps reference
        names to environment variable values and is mainly intended for local
        development ergonomics.

    Example:
        >>> # export OPENAI_API_KEY='sk-...'
        >>> # store = EnvironmentSecretStore()
        >>> # key = await store.get_secret("OPENAI_API_KEY", tenant_id="tenant-a")
    """

    async def get_secret(self, secret_reference: str, *, tenant_id: str) -> str:
        """Fetch secret value from ``os.environ`` by reference key.

        The ``tenant_id`` parameter is accepted for interface consistency even
        though environment-variable lookup itself is not tenant-aware.
        """
        value: str | None = os.environ.get(secret_reference)
        if value is None:
            raise KeyError(
                f"Environment variable {secret_reference!r} is not set. "
                "Ensure it is defined in your .env file or shell environment."
            )
        logger.debug(
            "Secret retrieved from environment",
            extra={"secret_reference": secret_reference, "tenant_id": tenant_id},
        )
        return value
