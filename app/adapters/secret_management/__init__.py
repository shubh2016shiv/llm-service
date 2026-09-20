"""
Secret management — the secret keepers
=======================================

What this package is for
------------------------
AI providers (OpenAI, AWS Bedrock, ...) require an API key on every
request. Those keys are secrets and must be handled carefully: never
logged, never committed to source control, kept in memory only as long
as strictly needed. This package is the one place that knows HOW to
obtain a key, so the rest of the app can simply ask:
"give me the secret for this reference".

The one shared question
-----------------------
Every backend in this package answers the same single question through
the same method (``SecretStore.get_secret``):

    "Here is a secret reference and the tenant asking for it.
     Give me the usable plaintext key."

The backends differ only in WHERE the secret lives:
    - EnvironmentSecretStore -- reads it from an environment variable.
    - AesGcmSecretStore      -- decrypts it from an encrypted record.
    - VaultSecretStore       -- fetches it from HashiCorp Vault.

The ground rules (followed by every backend)
--------------------------------------------
    1. Only the REFERENCE is ever logged — never the secret value.
    2. Plaintext keys live in memory only as long as strictly needed.
    3. A missing or broken secret raises a clear, actionable error.

Suggested reading order (for learning this package from scratch)
----------------------------------------------------------------
    1. secret_store.py             (about 1 minute)
       The contract: the one question every backend answers.
    2. environment_secret_store.py (about 1 minute)
       The simplest backend — reads an environment variable.
    3. tenant_secret_encryption.py (the crypto helpers)
       How keys are sealed for storage and opened on demand.
    4. aes_gcm_secret_store.py     (the decryption gateway)
       Uses the helpers above to open stored, encrypted records.
    5. vault_client.py             (shared Vault network mechanics)
       Owns timeouts, jittered retries, login tokens, paths, and lifecycle.
    6. vault_secret_store.py       (the read-only Vault adapter)
       Fetches and validates KV-v2 provider secrets.
    7. vault_secret_writer.py      (the write-only Vault adapter)
       Stores credentials through a separate least-privilege identity.

If any line in these files still reads like jargon, it is a bug in the
comments — not in you. Fix it right there.

Author: Shubham Singh
"""

# The decryption gateway backend (opens encrypted records).
from .aes_gcm_secret_store import AesGcmSecretStore

# The simplest backend (reads environment variables).
from .environment_secret_store import EnvironmentSecretStore

# The contract every backend fulfils.
from .secret_store import SecretStore

# The encryption helpers management tooling uses to store keys safely.
from .tenant_secret_encryption import (
    decrypt_api_key,
    derive_tenant_key,
    encrypt_api_key,
)

# Validated Vault networking policy plus separate read/write adapters.
from .vault_client import VaultClientOptions
from .vault_secret_store import VaultSecretStore
from .vault_secret_writer import VaultSecretWriter

__all__ = [
    "AesGcmSecretStore",
    "EnvironmentSecretStore",
    "SecretStore",
    "VaultClientOptions",
    "VaultSecretStore",
    "VaultSecretWriter",
    "decrypt_api_key",
    "derive_tenant_key",
    "encrypt_api_key",
]
