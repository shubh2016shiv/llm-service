"""Operational errors raised by local infrastructure tooling."""


class InfrastructureError(RuntimeError):
    """Base class for actionable infrastructure-management failures."""


class CommandFailedError(InfrastructureError):
    """A required external command returned a non-zero exit code."""


class PreflightFailedError(InfrastructureError):
    """Host prevalidation found failures that make mutation unsafe."""
