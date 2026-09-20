"""
Model catalog service — the librarian for models
=================================================

What this file is for
---------------------
Every provider can expose many models. This service owns the model side
of the catalog: create, list, count, retrieve, update, and change a
model's status (activate / deactivate).

The full CRUD pattern is explained once here, line by line — the
provider service in provider_catalog.py follows the exact same shape.

What "Row" means in this file
-----------------------------
A database row is returned as a plain dictionary, ``Row = dict[str,
object]``. These services deliberately return that loose dict (not a
rigid Pydantic model) so list endpoints stay flexible and future columns
do not break callers. The only hard rule is: secret fields are scrubbed
before the dict leaves (see clean_row).

Author: Shubham Singh
"""

# This line makes every type hint below a lazy string, so hints can
# mention classes (like ModelCatalogPersistence) before they are
# imported. (Boilerplate.)
from __future__ import annotations

# TYPE_CHECKING is only True while a type checker (mypy/pyright) reads the
# file, never at runtime — imports under it exist purely for type hints.
from typing import TYPE_CHECKING

# The typed "no such thing" error raised for missing lookups.
from app.core.exceptions import ManagementValidationError, ResourceNotFoundError
from app.schemas.enums import ModelLifecycleStatus

# The validated request bodies an admin sends to create/update a model.
from app.schemas.management_schema import ModelCreateRequest, ModelUpdateRequest

# The shared helpers explained in the package __init__ docstring above:
#   Row       = a loose database-row dictionary,
#   clean_row / clean_rows        = scrub secret fields from a row,
#   raise_clean_validation_error  = translate a database error to a typed
#                                   domain error.
from app.services.management_helpers import Row, clean_row, clean_rows, raise_clean_validation_error

# Names used only in type hints, so they are imported only for the checker.
if TYPE_CHECKING:
    from uuid import UUID

    from app.core.settings.loader import ConfigLoader
    from app.database import ModelCatalogPersistence, ProviderCatalogPersistence


class ModelCatalogService:
    """Manage lifecycle operations for provider-scoped model records.

    One job, in plain words: turn a validated request into a database
    call, turn database errors into domain errors, and never let a
    secret-bearing field leave this class.
    """

    def __init__(
        self,
        model_persistence: ModelCatalogPersistence,
        provider_persistence: ProviderCatalogPersistence,
        config_loader: ConfigLoader,
    ) -> None:
        """Keep persistence adapters and the validated runtime catalog.

        The service verifies that a model can actually be routed before its
        persistence adapter writes SQL. This prevents catalog-only records
        that would fail on their first inference request.
        """
        self._models = model_persistence
        self._providers = provider_persistence
        self._config_loader = config_loader

    async def create_model(self, provider_id: UUID, request: ModelCreateRequest) -> Row:
        """Create one model record under a specific provider.

        Args:
            provider_id: The provider that owns this model (ownership
                scope — a model always belongs to exactly one provider).
            request: The already-validated create body.

        Returns:
            The new model row, with secret fields scrubbed.
        """
        provider = await self._providers.get_provider_by_id(provider_id)
        if provider is None:
            raise ResourceNotFoundError("Provider", str(provider_id))
        try:
            runtime_provider = self._config_loader.load_provider_config(
                str(provider["provider_name"])
            )
        except KeyError as exc:
            raise ManagementValidationError(
                "The provider has no runtime configuration and cannot receive models."
            ) from exc
        runtime_model = next(
            (model for model in runtime_provider.models if model.name == request.model_name),
            None,
        )
        if runtime_model is None:
            raise ManagementValidationError(
                f"Model {request.model_name!r} is absent from provider runtime configuration."
            )
        runtime_operations = {operation.value for operation in runtime_model.capabilities}
        unsupported = set(request.supported_operations) - runtime_operations
        if unsupported:
            values = ", ".join(sorted(unsupported))
            raise ManagementValidationError(
                f"Model operations are absent from runtime configuration: {values}."
            )
        try:
            # request.model_dump() turns the validated Pydantic request
            # into a plain dict; ** then splats that dict as keyword
            # arguments, so create_model(...) receives one argument per
            # field. (Pydantic already enforced the rules, so the values
            # are safe to forward.)
            row = await self._models.create_model(provider_id=provider_id, **request.model_dump())
            # Scrub secret fields before the row leaves this method.
            return clean_row(row)
        except ValueError as exc:
            # The database rejected the write (duplicate name, bad foreign
            # key, constraint...). Translate that raw error into a typed
            # domain error. (This helper always raises — it never returns.)
            raise_clean_validation_error(exc)

    async def list_models(
        self, provider_id: UUID, active_only: bool, limit: int, offset: int
    ) -> list[Row]:
        """List one provider's models, one page at a time.

        Args:
            provider_id: Whose models to list.
            active_only: True = only currently active models.
            limit: Maximum rows for this page.
            offset: How many rows to skip first.
        """
        rows = await self._models.list_models_by_provider(provider_id, active_only, limit, offset)
        # Scrub every row, not just one.
        return clean_rows(rows)

    async def count_models(self, provider_id: UUID, active_only: bool) -> int:
        """Count a provider's models, using the same filter as list_models.

        The API returns a page AND a total count; keeping this method's
        filter identical to list_models means the two numbers always
        describe the same set.
        """
        return await self._models.count_models_by_provider(provider_id, active_only)

    async def get_model(self, provider_id: UUID, model_id: UUID) -> Row:
        """Fetch one model, scoped to its provider.

        The lookup requires BOTH the provider id and the model id. That
        prevents a subtle bug: a model id that exists but belongs to a
        different provider must not be returned by mistake.
        """
        row = await self._models.get_model_by_provider_and_id(provider_id, model_id)
        if row is None:
            # No such model (or not under this provider) -> typed 404.
            raise ResourceNotFoundError("Model", str(model_id))
        return clean_row(row)

    async def update_model(
        self, provider_id: UUID, model_id: UUID, request: ModelUpdateRequest
    ) -> Row:
        """Partially update a model's changeable fields.

        This is a PATCH: only the fields the caller actually sent are
        written, and omitted fields keep their current values. That is
        exactly what exclude_unset=True does below.

        Args:
            provider_id: The model's owning provider.
            model_id: The model to update.
            request: The already-validated update body.
        """
        try:
            # model_dump(exclude_unset=True) is the important part: it
            # serializes ONLY the fields the caller explicitly set (a
            # Pydantic model tracks which fields were given a value), so
            # an omitted field is not accidentally reset to None.
            row = await self._models.update_model(
                provider_id=provider_id,
                model_id=model_id,
                **request.model_dump(exclude_unset=True),
            )
        except ValueError as exc:
            raise_clean_validation_error(exc)
        if row is None:
            raise ResourceNotFoundError("Model", str(model_id))
        return clean_row(row)

    async def activate_model(self, provider_id: UUID, model_id: UUID) -> Row:
        """Set a model's status to ``active`` so it can be used again.

        A convenience wrapper: it builds a one-field update request and
        reuses update_model, so "activate" is defined in exactly one
        place instead of being repeated.
        """
        # Only `status` is set here; exclude_unset (inside update_model)
        # ensures nothing else is touched.
        request = ModelUpdateRequest(status=ModelLifecycleStatus.ACTIVE)
        return await self.update_model(provider_id, model_id, request)

    async def deactivate_model(self, provider_id: UUID, model_id: UUID) -> Row:
        """Take a model out of service (status becomes ``deprecated``).

        "Deactivate" here means: stop NEW deployments from using it, while
        keeping the record for history and auditability. (It sets
        "deprecated" rather than "retired" — the softer retirement step.)
        """
        row = await self._models.deprecate_model(provider_id, model_id)
        if row is None:
            raise ResourceNotFoundError("Model", str(model_id))
        return clean_row(row)
