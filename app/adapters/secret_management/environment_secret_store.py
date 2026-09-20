"""
Environment secret store — the simplest backend
================================================

What this file is for
---------------------
Reads secret values straight from environment variables (the variables
the operating system gives every running program). Best for local
development, tests, and CI where secrets are injected as env vars. It is
deliberately simple: no encryption, no network — just a lookup.

Flow in three steps:
    1. The secret reference is treated as an environment variable name.
    2. This store reads ``os.environ[reference]``.
    3. A missing variable raises KeyError with guidance on how to fix it.

Author: Shubham Singh
"""

# This line makes every type hint below a lazy string. (Boilerplate.)
from __future__ import annotations

# logging = writing to the application log.
import logging

# os = the operating system; here only for os.environ (environment vars).
import os
import re

# The contract this backend fulfils (see secret_store.py).
# Relative import: the package works under any parent package name.
from .secret_store import SecretStore

logger = logging.getLogger(__name__)
_ENVIRONMENT_VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class EnvironmentSecretStore(SecretStore):
    """Resolve secrets from environment variables.

    In plain words:
        This backend does not encrypt or decrypt anything. It simply maps
        a reference name to an environment variable of the same name —
        convenience for local development, not a production backend.

    Example:
        >>> # export OPENAI_API_KEY='sk-...'
        >>> # store = EnvironmentSecretStore()
        >>> # key = await store.get_secret("OPENAI_API_KEY", tenant_id="tenant-a")
    """

    async def get_secret(self, secret_reference: str, *, tenant_id: str) -> str:
        """Fetch one secret from ``os.environ`` by its reference name.

        The ``tenant_id`` argument is accepted (and logged) only to keep
        the signature identical to the other backends — environment
        variables are not tenant-aware.

        An optional ``env:`` prefix (e.g. ``env:OPENAI_API_KEY``) is
        stripped before lookup, matching the reference format management
        tooling and operators commonly type by analogy with the ``aes:``
        prefix used for encrypted-record credentials.

        Args:
            secret_reference: The environment variable name to read,
                optionally prefixed with ``env:``.
            tenant_id: Logged for context; otherwise unused.

        Returns:
            The variable's value.

        Raises:
            KeyError: When the variable is not set, with fix-it guidance.
        """
        variable_name = secret_reference.removeprefix("env:")
        if _ENVIRONMENT_VARIABLE_NAME.fullmatch(variable_name) is None:
            raise ValueError(
                "Environment secret reference must be a valid environment variable name."
            )
        if not tenant_id:
            raise ValueError("tenant_id must not be empty.")
        # Read the variable. .get() returns None when it is not set,
        # which lets us raise a helpful error instead of a bare KeyError.
        value: str | None = os.environ.get(variable_name)
        if value is None:
            raise KeyError(
                f"Environment variable {variable_name!r} is not set. "
                "Ensure it is defined in your .env file or shell environment."
            )
        if not value:
            raise ValueError(f"Environment variable {variable_name!r} is set but empty.")
        # Log ONLY the reference and tenant — never the secret value.
        logger.debug(
            "Secret retrieved from environment",
            extra={"secret_reference": secret_reference, "tenant_id": tenant_id},
        )
        return value
