"""
Credential Encoding — turn a typed credential input into stored columns
=========================================================================

Architecture:
-------------
    +--------------------------+     +----------------------+
    | DeploymentCreateRequest  |     | EntitlementCreateReq |
    | .credential (typed)      |     | .credential (typed)  |
    +--------------------------+     +----------------------+
                   \\                       /
                    v                      v
              +--------------------------------+
              |   encode_credential() (this)   |
              +--------------------------------+
                    |                      |
                    v                      v
         secret_reference            extra_config additions
         (Vault KV path, written      (tenant_deployments /
          via CredentialWriter)        user_entitlements column)

Why Vault, not encryption-in-Postgres:
    This service already runs against a live HashiCorp Vault (see
    VaultSecretWriter) whose entire job is guarding exactly this kind of
    secret. Encrypting credentials into a Postgres column would duplicate
    what Vault already does, worse (ciphertext now sits in every DB backup
    and log, instead of in the system built to isolate it). secret_reference
    was always documented as "opaque env-var name or vault path" — this
    module fulfils the vault-path half of that contract.

Author: Shubham Singh
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

import httpx

from app.core.exceptions import ManagementValidationError, SecretBackendUnavailableError
from app.schemas.enums import ProviderCatalogAuthMode

if TYPE_CHECKING:
    from app.schemas.management_schema import CredentialInput

_IAM_DEFAULT_REFERENCE = "iam:default"


class CredentialWriter(Protocol):
    """Persists a credential payload out-of-band and hands back its path.

    VaultSecretWriter is the only real implementation today. The Protocol
    exists so services depend on "something that can store a credential",
    not on Vault specifically — a future backend (for example a cloud
    provider's native key vault) can be swapped in without touching the
    services that call encode_credential.
    """

    async def write_secret(
        self, path: str, *, tenant_id: str, fields: dict[str, str | None]
    ) -> str:
        """Store a credential payload and return its secret_reference."""
        ...


async def encode_credential(
    credential: CredentialInput | None,
    path: str,
    tenant_id: str,
    writer: CredentialWriter | None,
    provider_auth_mode: ProviderCatalogAuthMode,
) -> str:
    """Validate and store one credential, returning its opaque reference.

    Args:
        credential: Typed credential input from the request, or None when
            the provider authenticates via ambient infrastructure
            credentials rather than a stored secret (for example, an AWS
            IAM role for an aws_sigv4 Bedrock deployment — BedrockProvider
            ignores secret_reference entirely and uses the aioboto3
            session's default credential chain).
        path: Stable ownership prefix. A unique version segment is appended
            before the secret is written.
        tenant_id: Tenant the credential is scoped to (for audit logging).
        writer: Where the credential payload actually gets persisted. May
            be None only when credential is also None — a caller supplying
            a real credential with no writer configured is a server
            misconfiguration, not something to silently drop.

    Returns:
        The secret reference ready for persistence. Authentication policy is
        not copied into mutable extra_config; the provider catalog owns it.

    Raises:
        ManagementValidationError: The credential conflicts with provider policy.
        SecretBackendUnavailableError: No usable writer is available.
    """
    if credential is None:
        if provider_auth_mode is not ProviderCatalogAuthMode.AWS_SIGV4:
            raise ManagementValidationError(
                f"A credential is required for provider auth mode {provider_auth_mode.value!r}."
            )
        return _IAM_DEFAULT_REFERENCE
    if credential.auth_mode != provider_auth_mode:
        raise ManagementValidationError(
            "Credential auth mode does not match the selected provider: "
            f"expected {provider_auth_mode.value!r}, received {credential.auth_mode.value!r}."
        )
    if writer is None:
        raise SecretBackendUnavailableError(
            backend_name="credential-writer",
            secret_reference=path,
            reason="writer is not configured",
        )
    payload = _credential_payload(credential)
    versioned_path = build_credential_path(path, "versions", str(uuid4()))
    try:
        secret_reference = await writer.write_secret(
            versioned_path,
            tenant_id=tenant_id,
            fields=payload,
        )
    except SecretBackendUnavailableError:
        raise
    except (PermissionError, ValueError, httpx.HTTPError) as exc:
        # Backend errors may contain URLs or paths. Preserve the original for
        # logs through exception chaining, but publish only its type.
        raise SecretBackendUnavailableError(
            backend_name="credential-writer",
            secret_reference=versioned_path,
            reason=type(exc).__name__,
        ) from exc
    return secret_reference


def build_credential_path(*segments: str) -> str:
    """Join non-empty ownership segments into one normalized secret path."""
    normalized = [segment.strip("/") for segment in segments]
    if not normalized or any(not segment for segment in normalized):
        raise ValueError("Credential path segments must be non-empty.")
    return "/".join(normalized)


def _credential_payload(credential: CredentialInput) -> dict[str, str | None]:
    """Convert typed input into the document stored by the secret backend."""
    return {"api_key": credential.api_key.get_secret_value()}
