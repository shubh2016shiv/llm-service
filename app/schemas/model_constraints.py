"""Fixed domain constraints for LLM model sampling parameters.

Architecture:
    API schemas / YAML models / persistence validation
                         |
                         v
                  sampling parameter bounds
                         |
                         v
              PostgreSQL CHECK constraints

These are safety invariants, not operator-tunable configuration. Any change
requires an accompanying database migration for model_catalog and
tenant_deployments.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

MIN_TEMPERATURE = Decimal("0.00")
MAX_TEMPERATURE = Decimal("2.00")
MIN_TOP_P = Decimal("0.000")
MAX_TOP_P = Decimal("1.000")


def validate_temperature(value: float | Decimal, parameter_name: str) -> None:
    """Validate a sampling temperature against the database-backed bounds."""
    validate_decimal_range(value, MIN_TEMPERATURE, MAX_TEMPERATURE, parameter_name)


def validate_top_p(value: float | Decimal, parameter_name: str) -> None:
    """Validate a nucleus-sampling probability against its fixed bounds."""
    validate_decimal_range(value, MIN_TOP_P, MAX_TOP_P, parameter_name)


def validate_decimal_range(
    value: float | Decimal,
    minimum: Decimal,
    maximum: Decimal,
    parameter_name: str,
) -> None:
    """Reject non-finite or out-of-range decimal-compatible values."""
    if isinstance(value, bool):
        raise ValueError(f"{parameter_name} must be numeric, got bool")
    try:
        decimal_value = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{parameter_name} must be numeric, got {value!r}") from exc
    if not decimal_value.is_finite() or not minimum <= decimal_value <= maximum:
        raise ValueError(
            f"{parameter_name} must be between {minimum} and {maximum}, got {decimal_value}"
        )
