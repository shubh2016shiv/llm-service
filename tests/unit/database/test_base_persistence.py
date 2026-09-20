"""Unit tests for database persistence safety and validation primitives.

Architecture:
    Repository methods -> BasePersistence safeguards -> SQLAlchemy boundary
"""

from __future__ import annotations

from enum import StrEnum

import pytest

from app.database.base import BasePersistence


class ExampleStatus(StrEnum):
    """Small enum used to exercise the shared enum validator."""

    ACTIVE = "active"
    INACTIVE = "inactive"


@pytest.fixture
def persistence() -> BasePersistence:
    """Return an uninitialized instance for testing pure base-class helpers."""
    return object.__new__(BasePersistence)


@pytest.mark.parametrize("invalid_value", [True, False])
def test_validate_positive_integer_with_boolean_raises_value_error(
    persistence: BasePersistence,
    invalid_value: bool,
) -> None:
    """REQ: booleans must not pass integer validation."""
    with pytest.raises(ValueError, match="must be an integer"):
        persistence.validate_positive_integer(invalid_value, "capacity")


@pytest.mark.parametrize("invalid_value", [True, False, float("nan"), float("inf"), 0.0, -1.0])
def test_validate_positive_number_with_invalid_value_raises_value_error(
    persistence: BasePersistence,
    invalid_value: float,
) -> None:
    """REQ: positive numbers must be finite, greater than zero, and not booleans."""
    with pytest.raises(ValueError):
        persistence.validate_positive_number(invalid_value, "timeout_seconds")


@pytest.mark.parametrize(
    ("limit", "offset"),
    [(True, 0), (10, False), (0, 0), (10, -1), (1001, 0)],
)
def test_validate_pagination_parameters_with_invalid_values_raises_value_error(
    persistence: BasePersistence,
    limit: int,
    offset: int,
) -> None:
    """REQ: pagination accepts only bounded integers and rejects booleans."""
    with pytest.raises(ValueError):
        persistence.validate_pagination_parameters(limit, offset)


def test_validate_enum_member_with_unknown_value_reports_allowed_values(
    persistence: BasePersistence,
) -> None:
    """REQ: enum failures identify both the bad value and valid vocabulary."""
    with pytest.raises(ValueError, match=r"archived.*active, inactive"):
        persistence.validate_enum_member(ExampleStatus, "archived", "status")


def test_build_dynamic_update_query_with_known_identifiers_returns_parameterized_sql(
    persistence: BasePersistence,
) -> None:
    """REQ: dynamic updates bind values and include the update timestamp."""
    sql, parameters = persistence.build_dynamic_update_query(
        table_name="users",
        update_fields={"status": "inactive"},
        where_clause="user_id = :user_id",
        where_parameters={"user_id": "user-1"},
        returning_columns=("user_id", "status"),
    )

    assert sql == (
        "UPDATE users SET updated_at = CURRENT_TIMESTAMP, status = :set_status "
        "WHERE user_id = :user_id RETURNING user_id, status"
    )
    assert parameters == {"set_status": "inactive", "user_id": "user-1"}


def test_build_dynamic_update_query_with_safe_projection_excludes_secret_columns(
    persistence: BasePersistence,
) -> None:
    """REQ: repositories can explicitly prevent secrets from being returned."""
    sql, _ = persistence.build_dynamic_update_query(
        table_name="users",
        update_fields={"status": "inactive"},
        where_clause="user_id = :user_id",
        where_parameters={"user_id": "user-1"},
        returning_columns=("user_id", "status"),
    )

    assert sql.endswith("RETURNING user_id, status")
    assert "password_hash" not in sql


def test_build_dynamic_update_query_with_unsafe_returning_column_raises_value_error(
    persistence: BasePersistence,
) -> None:
    """REQ: a RETURNING projection cannot inject arbitrary SQL."""
    with pytest.raises(ValueError, match="returning column"):
        persistence.build_dynamic_update_query(
            table_name="users",
            update_fields={"status": "inactive"},
            where_clause="user_id = :user_id",
            where_parameters={"user_id": "user-1"},
            returning_columns=("user_id", "password_hash; DROP TABLE users"),
        )


@pytest.mark.parametrize(
    ("table_name", "update_fields", "where_clause"),
    [
        ("users; DROP TABLE users", {"status": "inactive"}, "user_id = :user_id"),
        ("users", {"status = 'deleted' --": "inactive"}, "user_id = :user_id"),
        ("users", {"status": "inactive"}, "user_id = :user_id; DELETE FROM users"),
        ("users", {"status": "inactive"}, ""),
    ],
)
def test_build_dynamic_update_query_with_unsafe_sql_shape_raises_value_error(
    persistence: BasePersistence,
    table_name: str,
    update_fields: dict[str, object],
    where_clause: str,
) -> None:
    """REQ: identifiers and WHERE clauses cannot inject arbitrary SQL."""
    with pytest.raises(ValueError):
        persistence.build_dynamic_update_query(
            table_name=table_name,
            update_fields=update_fields,
            where_clause=where_clause,
            where_parameters={"user_id": "user-1"},
            returning_columns=("user_id", "status"),
        )


def test_serialize_json_with_non_finite_number_raises_value_error(
    persistence: BasePersistence,
) -> None:
    """REQ: JSON serialization rejects values PostgreSQL JSONB cannot store."""
    with pytest.raises(ValueError, match="JSON-serializable"):
        persistence.serialize_json({"temperature": float("nan")}, "metadata")
