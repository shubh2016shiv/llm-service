"""
Provider catalog service — the librarian for providers
========================================================

What this file is for
---------------------
Providers (OpenAI, Anthropic, Bedrock, ...) are the top-level records in
the catalog. This service owns their lifecycle: create, list, count,
retrieve, update, and delete.

It follows the exact same CRUD pattern as model_catalog.py — so the
pattern is explained once there, and this file keeps only the
provider-specific notes.

What "Row" means in this file
-----------------------------
A database row is returned as a plain dictionary, ``Row = dict[str,
object]``. Secret fields (password, password_hash, secret_reference) are
scrubbed before the dict leaves this class — see clean_row.

Author: Shubham Singh
"""

# This line makes every type hint below a lazy string. (Boilerplate.)
from __future__ import annotations

# TYPE_CHECKING is only True while a type checker reads the file, never at
# runtime — imports under it exist purely for type hints.
from typing import TYPE_CHECKING

# The typed "no such thing" error raised for missing lookups.
from app.core.exceptions import ManagementValidationError, ResourceNotFoundError

# The shared helpers (explained in the package __init__ docstring):
#   Row, clean_row, clean_rows, raise_clean_validation_error.
from app.services.management_helpers import (
    Row,
    clean_row,
    clean_rows,
    raise_clean_validation_error,
)

# Names used only in type hints, so they are imported only for the checker.
if TYPE_CHECKING:
    from uuid import UUID

    from app.core.settings.loader import ConfigLoader
    from app.database import ProviderCatalogPersistence
    from app.schemas.management_schema import ProviderCreateRequest, ProviderUpdateRequest


class ProviderCatalogService:
    """Manage the lifecycle of provider metadata records."""

    def __init__(
        self,
        provider_persistence: ProviderCatalogPersistence,
        config_loader: ConfigLoader,
    ) -> None:
        """Keep persistence plus the startup-validated runtime catalog."""
        self._providers = provider_persistence
        self._config_loader = config_loader

    async def create_provider(self, request: ProviderCreateRequest) -> Row:
        """Register a new provider in the catalog.

        Args:
            request: The already-validated create body (name, auth mode,
                endpoint defaults, capability flags).
        """
        try:
            runtime_config = self._config_loader.load_provider_config(request.provider_name)
        except KeyError as exc:
            raise ManagementValidationError(
                f"Provider {request.provider_name!r} has no runtime configuration."
            ) from exc
        if request.auth_mode.value != runtime_config.auth.mode.value:
            raise ManagementValidationError(
                "Catalog auth_mode must match runtime configuration: "
                f"expected {runtime_config.auth.mode.value!r}."
            )
        runtime_operations = {operation.value for operation in runtime_config.capabilities}
        unsupported = set(request.supported_operations) - runtime_operations
        if unsupported:
            values = ", ".join(sorted(unsupported))
            raise ManagementValidationError(
                f"Provider operations are absent from runtime configuration: {values}."
            )
        try:
            # model_dump() -> plain dict; ** splats it into keyword args.
            row = await self._providers.create_provider(**request.model_dump())
            return clean_row(row)
        except ValueError as exc:
            raise_clean_validation_error(exc)

    async def list_providers(self, include_inactive: bool, limit: int, offset: int) -> list[Row]:
        """List providers, one page at a time, with an active-only switch.

        Args:
            include_inactive: True = list ALL providers; False = active
                ones only.
            limit: Maximum providers for this page.
            offset: How many to skip first.
        """
        # Pick the right query based on the flag, then run it. (A ternary
        # expression: "if include_inactive, list all; otherwise list
        # active only.")
        rows = (
            await self._providers.list_all_providers(limit, offset)
            if include_inactive
            else await self._providers.list_active_providers(limit, offset)
        )
        return clean_rows(rows)

    async def count_providers(self, include_inactive: bool = False) -> int:
        """Count providers with the same active/inactive filter as listing.

        Kept in lock-step with list_providers so the API's "page + total"
        numbers describe the same set.
        """
        if include_inactive:
            return await self._providers.count_all_providers()
        return await self._providers.count_active_providers()

    async def get_provider(self, provider_id: UUID) -> Row:
        """Retrieve one provider record by id."""
        row = await self._providers.get_provider_by_id(provider_id)
        if row is None:
            raise ResourceNotFoundError("Provider", str(provider_id))
        return clean_row(row)

    async def update_provider(self, provider_id: UUID, request: ProviderUpdateRequest) -> Row:
        """Partially update a provider's changeable fields (a PATCH).

        exclude_unset=True means only fields the caller actually sent are
        written — omitted fields keep their current values and are never
        reset to None.
        """
        try:
            row = await self._providers.update_provider(
                provider_id=provider_id,
                **request.model_dump(exclude_unset=True),
            )
        except ValueError as exc:
            raise_clean_validation_error(exc)
        if row is None:
            raise ResourceNotFoundError("Provider", str(provider_id))
        return clean_row(row)

    async def delete_provider(self, provider_id: UUID) -> None:
        """Delete a provider record permanently.

        This does NOT cascade to related records (models, deployments).
        If something still references the provider, the database's own
        foreign-key constraints reject the delete, and that surfaces as a
        domain error through the persistence layer.
        """
        deleted = await self._providers.delete_provider(provider_id)
        if not deleted:
            raise ResourceNotFoundError("Provider", str(provider_id))
