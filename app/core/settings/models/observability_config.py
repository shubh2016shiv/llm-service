"""Observability configuration for process logging.

Architecture:
    environment variables -> ObservabilityConfig -> logging configuration
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class ObservabilityConfig(BaseModel):
    """Define the process-wide log level.

    Algorithm: normalize the configured level and reject unsupported values
    before the logging subsystem is initialized.
    """

    log_level: str = Field(
        default="INFO",
        description="Logging threshold: DEBUG, INFO, WARNING, ERROR, or CRITICAL.",
    )

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        """Normalize and validate a standard-library logging level."""
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        normalized_value = value.upper()
        if normalized_value not in allowed:
            raise ValueError(f"log_level {value!r} is invalid. Must be one of: {sorted(allowed)}")
        return normalized_value
