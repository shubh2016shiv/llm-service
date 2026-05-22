"""
Management Service Helpers
==========================

Shared utility functions used across CRUD-oriented management services.

Why this file exists:
    Several behaviors repeat in almost every service: removing sensitive
    fields before returning data, translating low-level persistence errors
    into domain exceptions, and enforcing "resource must exist" checks.
    Centralizing those behaviors prevents copy-paste drift and ensures
    consistent API-facing error semantics.

What each helper does:
    - ``clean_row`` / ``clean_rows`` remove secret-bearing fields from
      persistence rows before data leaves the service layer.
    - ``raise_clean_validation_error`` maps generic ``ValueError`` failures
      from persistence into typed domain exceptions that the API layer can
      translate into stable HTTP status codes.
    - ``require_row`` enforces non-null lookup results and raises a
      consistent not-found error with resource context.

Enterprise Pattern: Shared Utility Pattern
    Stateless pure helpers are defined once and reused everywhere.
    This improves consistency and reduces accidental security regressions.

Author: Shubham Singh
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

from app.core.exceptions import ManagementValidationError, ResourceConflictError
from app.database.base import MissingReferencedResourceError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_SECRET_FIELDS: frozenset[str] = frozenset({"password", "password_hash", "secret_reference"})
_CONFLICT_HINTS: tuple[str, ...] = ("already", "duplicate", "unique", "exists")
RowValue = object
Row = dict[str, RowValue]


def clean_row(row: Mapping[str, RowValue]) -> Row:
    """Return a copy of a row with secret-bearing keys removed.

    This helper acts as a defensive boundary. Even if persistence returns
    credential-related fields, callers of service methods should never
    receive those fields in responses or logs.

    Args:
        row: Source row returned by persistence.

    Returns:
        A new dictionary without keys listed in ``_SECRET_FIELDS``.
    """
    return {key: value for key, value in row.items() if key not in _SECRET_FIELDS}


def clean_rows(rows: Sequence[Mapping[str, RowValue]]) -> list[Row]:
    """Apply ``clean_row`` to a sequence of persistence rows.

    List endpoints typically return multiple rows at once. This helper keeps
    redaction behavior identical to single-row endpoints so consumers never
    see different field visibility based on endpoint shape.

    Args:
        rows: Sequence of row-like mappings to sanitize.

    Returns:
        A list of sanitized row dictionaries.
    """
    return [clean_row(row) for row in rows]


def raise_clean_validation_error(exc: ValueError) -> NoReturn:
    """Translate a raw persistence ``ValueError`` into a typed domain exception.

    Why this exists:
        Persistence methods often raise generic ``ValueError`` for distinct
        failure categories (conflict, missing references, invalid payloads).
        Service callers should not parse text messages to determine behavior.
        This helper converts those failures into explicit domain exceptions.

    Translation rules:
        - ``MissingReferencedResourceError`` -> ``ResourceNotFoundError``
        - conflict-like message hints -> ``ResourceConflictError``
        - all other validation errors -> ``ManagementValidationError``

    Args:
        exc: Original persistence exception.

    Raises:
        ResourceNotFoundError: If a referenced resource is missing.
        ResourceConflictError: If a uniqueness or duplicate conflict occurred.
        ManagementValidationError: For all other validation-style failures.
    """
    if isinstance(exc, MissingReferencedResourceError):
        from app.core.exceptions import ResourceNotFoundError

        raise ResourceNotFoundError(exc.resource_name, exc.resource_id) from exc
    message = str(exc)
    if any(hint in message.lower() for hint in _CONFLICT_HINTS):
        raise ResourceConflictError(message) from exc
    raise ManagementValidationError(message) from exc


def require_row[T](value: T | None, resource_name: str, resource_id: str) -> T:
    """Return a non-null lookup value or raise ``ResourceNotFoundError``.

    This helper removes repeated ``if row is None`` checks from services and
    guarantees that not-found errors are emitted with the same resource label
    and identifier formatting across modules.

    Args:
        value: Lookup result that may be ``None``.
        resource_name: Human-readable resource type for the error.
        resource_id: Identifier used in the failed lookup.

    Returns:
        The original non-``None`` value.

    Raises:
        ResourceNotFoundError: If ``value`` is ``None``.
    """
    if value is None:
        from app.core.exceptions import ResourceNotFoundError

        raise ResourceNotFoundError(resource_name, resource_id)
    return value
