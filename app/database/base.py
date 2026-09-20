"""
Base Persistence
----------------
Production-ready base class for all database service classes.

Provides shared, battle-tested utilities so entity persistence classes stay
focused on their domain logic rather than boilerplate:

  - Session acquisition via async context manager (commit/rollback lifecycle)
  - Single-query and batch-insert helpers with consistent error logging
  - Dynamic UPDATE query builder (only write the columns that actually changed)
  - Validation utilities that surface errors before opening a database session
  - Structured operation logging for observability

Threading model:
  Each method acquires a fresh session via PostgresSessionProvider.get_session().
  Sessions are not shared across calls. SQLAlchemy's async_sessionmaker handles
  the QueuePool checkout internally, making all methods safe for concurrent use.
"""

from __future__ import annotations

import json
import logging
import math
import re
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.adapters.postgresql import PostgresSessionProvider

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Collection, Mapping
    from enum import StrEnum

    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_SQL_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SIMPLE_WHERE_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*\s*=\s*:[A-Za-z_][A-Za-z0-9_]*"
    r"(?:\s+AND\s+[A-Za-z_][A-Za-z0-9_]*\s*=\s*:[A-Za-z_][A-Za-z0-9_]*)*$",
    re.IGNORECASE,
)


class MissingReferencedResourceError(ValueError):
    """Raised when a write references a row that does not exist."""

    def __init__(self, resource_name: str, resource_id: str) -> None:
        """Initialize with the missing resource name and identifier."""
        self.resource_name = resource_name
        self.resource_id = resource_id
        super().__init__(f"{resource_name} not found: {resource_id!r}.")


class BasePersistence:
    """Base class for all persistence layer classes.

    Inherit from this class and call ``super().__init__(session_provider)`` to gain
    access to session management, query helpers, and validation utilities.

    Args:
        session_provider: Application-scoped PostgreSQL session provider.
    """

    def __init__(self, session_provider: PostgresSessionProvider) -> None:
        """Store the explicitly injected PostgreSQL session provider."""
        self.session_provider = session_provider
        self._service_name = self.__class__.__name__

    # =========================================================================
    # SESSION MANAGEMENT
    # =========================================================================

    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Acquire a managed async session from the pool.

        Delegates directly to PostgresSessionProvider.get_session(). The
        transaction is committed on normal exit and rolled back on exception.

        Yields:
            AsyncSession: A live SQLAlchemy async session.
        """
        async with self.session_provider.get_session() as session:
            yield session

    # =========================================================================
    # QUERY EXECUTION HELPERS
    # =========================================================================

    async def execute_single_query(
        self,
        sql_query: str,
        query_parameters: dict[str, Any] | None = None,
        fetch_results: bool = True,
    ) -> list[dict[str, Any]] | None:
        """Execute a single parameterised SQL statement.

        Intended for reads and single-row writes. For bulk inserts use
        execute_batch_insert().

        Args:
            sql_query: Parameterised SQL string (use :param syntax).
            query_parameters: Bind parameters mapped to :param names.
            fetch_results: Return mapped rows when True (default). Pass False
                for statements that return no rows (e.g., DELETE without RETURNING).

        Returns:
            List of row dicts when fetch_results is True; None otherwise.

        Raises:
            Any database exception from SQLAlchemy or asyncpg.
        """
        try:
            async with self.get_session() as session:
                result = await session.execute(text(sql_query), query_parameters or {})
                if fetch_results:
                    rows = result.mappings().all()
                    return [dict(row) for row in rows] if rows else []
                return None
        except Exception:
            logger.error(
                "Database query execution failed",
                extra={
                    "repository": self._service_name,
                    "sql_preview": sql_query[:120],
                },
                exc_info=True,
            )
            raise

    async def execute_batch_insert(
        self,
        sql_query: str,
        parameter_list: list[dict[str, Any]],
        page_size: int = 1000,
    ) -> int:
        """Execute parameterised INSERT statements in pages.

        Each row in parameter_list is executed individually within a single
        session so the entire batch commits or rolls back atomically.

        Args:
            sql_query: Parameterised INSERT SQL string.
            parameter_list: One dict per row to insert.
            page_size: Rows per services page (default 1000). Tune downward
                for rows with large JSONB payloads.

        Returns:
            Total number of rows inserted (sum of rowcount per page).

        Raises:
            Any database exception from SQLAlchemy or asyncpg.
        """
        self.validate_positive_integer(page_size, "page_size")
        if not parameter_list:
            logger.warning(
                "Database batch insert skipped because it was empty",
                extra={"repository": self._service_name},
            )
            return 0

        try:
            async with self.get_session() as session:
                rows_affected = 0
                for i in range(0, len(parameter_list), page_size):
                    chunk = parameter_list[i : i + page_size]
                    for params in chunk:
                        result = await session.execute(text(sql_query), params)
                        rows_affected += getattr(result, "rowcount", 0)

            logger.info(
                "Database batch insert completed",
                extra={"repository": self._service_name, "rows_affected": rows_affected},
            )
            return rows_affected
        except Exception:
            logger.error(
                "Database batch insert failed",
                extra={"repository": self._service_name},
                exc_info=True,
            )
            raise

    # =========================================================================
    # DYNAMIC QUERY BUILDER
    # =========================================================================

    def build_dynamic_update_query(
        self,
        table_name: str,
        update_fields: Mapping[str, object],
        where_clause: str,
        where_parameters: Mapping[str, object],
        returning_columns: Collection[str],
    ) -> tuple[str, dict[str, object]]:
        """Construct an UPDATE statement from only the supplied fields.

        Avoids writing unchanged columns, reducing write amplification and
        trigger churn. Always appends `updated_at = CURRENT_TIMESTAMP` and
        adds an explicit RETURNING projection so future schema changes cannot
        expose newly added columns by accident.

        Args:
            table_name: Target table name.
            update_fields: Column→value pairs for the SET clause. Must not
                be empty; raises ValueError if it is.
            where_clause: Parameterised WHERE expression (e.g. "id = :id").
            where_parameters: Bind values for the WHERE clause.
            returning_columns: Explicit columns safe to return to callers.

        Returns:
            (sql_string, merged_parameters) ready to pass to session.execute().

        Raises:
            ValueError: If update_fields is empty.

        Example:
            sql, params = self.build_dynamic_update_query(
                "users",
                {"email": "new@example.com"},
                "user_id = :user_id",
                {"user_id": some_uuid},
                ("user_id", "email", "updated_at"),
            )
        """
        if not update_fields:
            raise ValueError("update_fields must contain at least one field to update")
        self.validate_sql_identifier(table_name, "table_name")
        for field_name in update_fields:
            self.validate_sql_identifier(field_name, "update field")
        if not _SIMPLE_WHERE_PATTERN.fullmatch(where_clause.strip()):
            raise ValueError(
                "where_clause must contain only parameterized equality conditions joined by AND"
            )

        set_clauses: list[str] = ["updated_at = CURRENT_TIMESTAMP"]
        parameters: dict[str, object] = {}

        for field_name, field_value in update_fields.items():
            param_key = f"set_{field_name}"
            set_clauses.append(f"{field_name} = :{param_key}")
            parameters[param_key] = field_value

        conflicting_parameters = parameters.keys() & where_parameters.keys()
        if conflicting_parameters:
            conflicts = ", ".join(sorted(conflicting_parameters))
            raise ValueError(f"where_parameters conflict with update parameters: {conflicts}")
        parameters.update(where_parameters)

        if not returning_columns:
            raise ValueError("returning_columns must not be empty")
        for column_name in returning_columns:
            self.validate_sql_identifier(column_name, "returning column")
        returning_clause = ", ".join(returning_columns)

        sql_query = (
            f"UPDATE {table_name} SET {', '.join(set_clauses)} "
            f"WHERE {where_clause} RETURNING {returning_clause}"
        )
        return sql_query, parameters

    # =========================================================================
    # VALIDATION UTILITIES
    # =========================================================================

    def validate_sql_identifier(self, identifier: str, parameter_name: str) -> None:
        """Reject table or column names that could alter generated SQL structure."""
        if not _SQL_IDENTIFIER_PATTERN.fullmatch(identifier):
            raise ValueError(f"{parameter_name} must be a plain SQL identifier, got {identifier!r}")

    def validate_uuid(self, uuid_value: UUID | str, parameter_name: str = "UUID") -> None:
        """Validate that a value is a non-None UUID or a valid UUID string.

        Raises:
            ValueError: If the value is None, not a UUID, or not a valid UUID string.
        """
        if isinstance(uuid_value, str):
            try:
                UUID(uuid_value)
                return
            except ValueError as exc:
                raise ValueError(f"{parameter_name} must be a valid UUID string") from exc
        if uuid_value is None:
            raise ValueError(f"{parameter_name} cannot be None")
        if not isinstance(uuid_value, UUID):
            raise ValueError(
                f"{parameter_name} must be a UUID instance, got {type(uuid_value).__name__}"
            )

    def validate_positive_integer(
        self,
        integer_value: int,
        parameter_name: str = "value",
        allow_zero: bool = False,
    ) -> None:
        """Validate that an integer meets the positivity requirement.

        Args:
            integer_value: Value to check.
            parameter_name: Label used in the error message.
            allow_zero: When True, zero is accepted; otherwise the minimum is 1.

        Raises:
            ValueError: If the value does not meet the requirement.
        """
        if isinstance(integer_value, bool) or not isinstance(integer_value, int):
            raise ValueError(
                f"{parameter_name} must be an integer, got {type(integer_value).__name__}"
            )
        minimum = 0 if allow_zero else 1
        if integer_value < minimum:
            threshold = "non-negative" if allow_zero else "positive"
            raise ValueError(f"{parameter_name} must be {threshold}, got {integer_value}")

    def validate_positive_number(
        self,
        numeric_value: int | float,
        parameter_name: str = "value",
        allow_zero: bool = False,
    ) -> None:
        """Validate that a finite number meets the positivity requirement."""
        if isinstance(numeric_value, bool) or not isinstance(numeric_value, (int, float)):
            raise ValueError(
                f"{parameter_name} must be a number, got {type(numeric_value).__name__}"
            )
        if not math.isfinite(numeric_value):
            raise ValueError(f"{parameter_name} must be finite, got {numeric_value}")
        minimum_is_valid = numeric_value >= 0 if allow_zero else numeric_value > 0
        if not minimum_is_valid:
            threshold = "non-negative" if allow_zero else "positive"
            raise ValueError(f"{parameter_name} must be {threshold}, got {numeric_value}")

    def validate_string_not_empty(self, string_value: str, parameter_name: str = "value") -> None:
        """Validate that a string is non-None, non-empty, and not all whitespace.

        Raises:
            ValueError: If the value fails any check.
        """
        if not string_value or not isinstance(string_value, str):
            raise ValueError(f"{parameter_name} must be a non-empty string")
        if not string_value.strip():
            raise ValueError(f"{parameter_name} cannot be whitespace only")

    def validate_enum_value(
        self,
        enum_value: str,
        valid_values: Collection[str],
        parameter_name: str = "value",
    ) -> None:
        """Validate that a string belongs to an allowed set.

        Args:
            enum_value: The value to check.
            valid_values: Exhaustive list of permitted strings.
            parameter_name: Label used in the error message.

        Raises:
            ValueError: If enum_value is not in valid_values.
        """
        if enum_value not in valid_values:
            raise ValueError(
                f"Invalid {parameter_name}: '{enum_value}'. "
                f"Must be one of: {', '.join(sorted(valid_values))}"
            )

    def validate_enum_member(
        self,
        enum_type: type[StrEnum],
        enum_value: str,
        parameter_name: str = "value",
    ) -> None:
        """Validate a string against a shared StrEnum vocabulary.

        Args:
            enum_type: Canonical enum declaring the allowed values.
            enum_value: Raw value received by the repository.
            parameter_name: Label used in the error message.

        Raises:
            ValueError: If enum_value is not declared by enum_type.
        """
        try:
            enum_type(enum_value)
        except ValueError as exc:
            allowed_values = ", ".join(member.value for member in enum_type)
            raise ValueError(
                f"Invalid {parameter_name}: {enum_value!r}. Must be one of: {allowed_values}"
            ) from exc

    def validate_pagination_parameters(
        self, limit: int, offset: int, max_limit: int = 1000
    ) -> None:
        """Validate limit/offset pagination parameters.

        Args:
            limit: Maximum rows to return. Must be 1 ≤ limit ≤ max_limit.
            offset: Rows to skip. Must be ≥ 0.
            max_limit: Ceiling for limit (default 1000).

        Raises:
            ValueError: If any parameter is out of range.
        """
        self.validate_positive_integer(limit, "limit")
        self.validate_positive_integer(offset, "offset", allow_zero=True)
        self.validate_positive_integer(max_limit, "max_limit")
        if limit > max_limit:
            raise ValueError(f"limit cannot exceed {max_limit}, got {limit}")

    def serialize_json(
        self,
        data: Mapping[str, object] | None,
        param_name: str = "metadata",
    ) -> str | None:
        """Serialise a dict to a JSON string after validating serializability.

        Validates before opening a session so the caller gets a clear ValueError
        instead of an opaque asyncpg/sqlalchemy error deep inside a transaction.

        Args:
            data: Dict to serialise. None is returned as None (maps to SQL NULL).
            param_name: Label used in the error message.

        Returns:
            JSON string, or None when data is None.

        Raises:
            ValueError: If data contains non-JSON-serializable values.
        """
        if data is None:
            return None
        try:
            return json.dumps(data, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{param_name} contains non-JSON-serializable values: {exc}") from exc

    # =========================================================================
    # OPERATION LOGGING
    # =========================================================================

    def log_operation(
        self,
        operation_type: str,
        entity_identifier: Any,
        success: bool = True,
        additional_context: str | None = None,
    ) -> None:
        """Emit a structured log line for a database operation.

        Args:
            operation_type: Verb describing the operation (CREATE, UPDATE, DELETE…).
            entity_identifier: Primary key or other identifying value.
            success: True for info-level log; False for error-level.
            additional_context: Optional detail appended to the log message.
        """
        context = {
            "repository": self._service_name,
            "database_operation": operation_type,
            "entity_id": str(entity_identifier),
        }
        if additional_context is not None:
            context["details"] = additional_context

        log_method = logger.info if success else logger.error
        log_method(
            "Database operation succeeded" if success else "Database operation failed",
            extra=context,
        )

    def raise_for_foreign_key_violation(
        self,
        exc: Exception,
        constraint_map: Mapping[str, tuple[str, str]],
    ) -> None:
        """Raise MissingReferencedResourceError when an integrity error is a known FK miss."""
        if not isinstance(exc, IntegrityError):
            return
        message = str(getattr(exc, "orig", exc)).lower()
        if "foreign key" not in message:
            return
        constraint_name = str(getattr(getattr(exc, "orig", None), "constraint_name", "")).lower()
        for expected_constraint, resource in constraint_map.items():
            resource_name, resource_id = resource
            if constraint_name == expected_constraint.lower():
                raise MissingReferencedResourceError(resource_name, resource_id) from exc
            if expected_constraint.lower() in message:
                raise MissingReferencedResourceError(resource_name, resource_id) from exc
