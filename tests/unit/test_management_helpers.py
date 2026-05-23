"""Unit tests for app.services.management_helpers — pure domain logic.

These helpers are stateless, deterministic functions with no I/O or async
dependencies. They are the ideal Layer 1 targets per the test strategy.
"""

from __future__ import annotations

import pytest

from app.core.exceptions import (
    ManagementValidationError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.database.base import MissingReferencedResourceError
from app.services.management_helpers import (
    _SECRET_FIELDS,
    clean_row,
    clean_rows,
    raise_clean_validation_error,
    require_row,
)

# ═══════════════════════════════════════════════════════════════════════════════
# clean_row
# ═══════════════════════════════════════════════════════════════════════════════


class TestCleanRow:
    """Tests for clean_row — redacts secret-bearing keys from a row dict."""

    def test_clean_row_removes_password_fields(self) -> None:
        """Secret fields must be stripped from the returned copy."""
        row = {"id": "u-1", "email": "a@b.com", "password": "secret", "password_hash": "abc"}

        result = clean_row(row)

        assert "password" not in result
        assert "password_hash" not in result
        assert "secret_reference" not in result

    def test_clean_row_preserves_non_secret_fields(self) -> None:
        """Fields not in _SECRET_FIELDS must pass through unchanged."""
        row = {"id": "u-1", "email": "a@b.com", "name": "Alice"}

        result = clean_row(row)

        assert result["id"] == "u-1"
        assert result["email"] == "a@b.com"
        assert result["name"] == "Alice"

    def test_clean_row_returns_new_dict_not_mutate_original(self) -> None:
        """Original row must remain intact — clean_row returns a copy."""
        row = {"id": "u-1", "password": "secret"}

        _ = clean_row(row)

        assert "password" in row  # original untouched

    def test_clean_row_empty_dict_returns_empty(self) -> None:
        """An empty row should produce an empty result."""
        result = clean_row({})

        assert result == {}

    def test_clean_row_all_secret_fields_only_returns_empty(self) -> None:
        """When every key is a secret field, the result is an empty dict."""
        row = {key: "redacted" for key in _SECRET_FIELDS}

        result = clean_row(row)

        assert result == {}


# ═══════════════════════════════════════════════════════════════════════════════
# clean_rows
# ═══════════════════════════════════════════════════════════════════════════════


class TestCleanRows:
    """Tests for clean_rows — batch redaction of secret fields."""

    def test_clean_rows_applies_redaction_to_every_element(self) -> None:
        """Every row in the sequence must have its secret fields removed."""
        rows = [
            {"id": "u-1", "password": "a"},
            {"id": "u-2", "password": "b"},
        ]

        result = clean_rows(rows)

        assert len(result) == 2
        for r in result:
            assert "password" not in r
            assert "id" in r

    def test_clean_rows_empty_sequence_returns_empty_list(self) -> None:
        """An empty input sequence must produce an empty list."""
        result = clean_rows([])

        assert result == []

    def test_clean_rows_returns_list_type(self) -> None:
        """The result must be a concrete list, not a lazy iterator."""
        rows = [{"id": "u-1"}]

        result = clean_rows(rows)

        assert isinstance(result, list)

    def test_clean_rows_idempotent_on_already_clean_rows(self) -> None:
        """Applying clean_rows to rows with no secret fields is a no-op."""
        rows = [{"id": "u-1", "name": "Alice"}, {"id": "u-2", "name": "Bob"}]

        result = clean_rows(rows)

        assert result == rows  # structurally identical, no-op


# ═══════════════════════════════════════════════════════════════════════════════
# require_row
# ═══════════════════════════════════════════════════════════════════════════════


class TestRequireRow:
    """Tests for require_row — non-null guard with domain error."""

    def test_require_row_returns_value_when_not_none(self) -> None:
        """A non-None value must be passed through unchanged."""
        value = {"id": "t-1", "name": "Acme"}

        result = require_row(value, "Tenant", "t-1")

        assert result is value

    def test_require_row_raises_resource_not_found_when_none(self) -> None:
        """A None value must raise ResourceNotFoundError with correct labels."""

        with pytest.raises(ResourceNotFoundError) as exc_info:
            require_row(None, "Tenant", "t-missing")

        error = exc_info.value
        assert error.resource_name == "Tenant"
        assert error.resource_id == "t-missing"
        assert "Tenant" in str(error)
        assert "t-missing" in str(error)

    def test_require_row_preserves_resource_id_type_as_passed(self) -> None:
        """The resource_id is stored as-is (may be UUID, str, etc.)."""
        from uuid import UUID

        rid = UUID("10000000-0000-0000-0000-000000000001")

        with pytest.raises(ResourceNotFoundError) as exc_info:
            require_row(None, "User", rid)

        error = exc_info.value
        # resource_id is stored exactly as passed — not coerced to string
        assert error.resource_id == rid
        assert isinstance(error.resource_id, UUID)

    def test_require_row_preserves_typed_generic_return(self) -> None:
        """The returned value type must match the input type (generic T)."""
        value = 42

        result = require_row(value, "Count", "any")

        assert result == 42
        assert isinstance(result, int)


# ═══════════════════════════════════════════════════════════════════════════════
# raise_clean_validation_error
# ═══════════════════════════════════════════════════════════════════════════════


class TestRaiseCleanValidationError:
    """Tests for raise_clean_validation_error — maps raw errors to domain types."""

    def test_missing_referenced_resource_becomes_resource_not_found(self) -> None:
        """MissingReferencedResourceError must translate to ResourceNotFoundError."""
        original = MissingReferencedResourceError("Provider", "p-99")

        with pytest.raises(ResourceNotFoundError) as exc_info:
            raise_clean_validation_error(original)

        assert exc_info.value.resource_name == "Provider"
        assert exc_info.value.resource_id == "p-99"

    def test_conflict_hint_yields_resource_conflict_error(self) -> None:
        """A ValueError containing 'already' must map to ResourceConflictError."""
        original = ValueError("Email already registered")

        with pytest.raises(ResourceConflictError) as exc_info:
            raise_clean_validation_error(original)

        assert "Email already registered" in str(exc_info.value)

    @pytest.mark.parametrize(
        "hint",
        ["already exists", "duplicate key", "unique constraint", "already"],
    )
    def test_conflict_hints_detected_case_insensitively(self, hint: str) -> None:
        """All conflict-like substrings must trigger ResourceConflictError."""
        with pytest.raises(ResourceConflictError):
            raise_clean_validation_error(ValueError(hint.upper()))

    def test_generic_value_error_becomes_management_validation_error(self) -> None:
        """A ValueError without conflict hints must translate to ManagementValidationError."""
        original = ValueError("Invalid field format")

        with pytest.raises(ManagementValidationError) as exc_info:
            raise_clean_validation_error(original)

        assert "Invalid field format" in str(exc_info.value)

    def test_chained_exception_preserves_cause(self) -> None:
        """The original exception must be set as __cause__ for traceability."""
        original = ValueError("Duplicate entry")

        with pytest.raises(ResourceConflictError) as exc_info:
            raise_clean_validation_error(original)

        assert exc_info.value.__cause__ is original
