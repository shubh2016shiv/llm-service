"""
Core Package
============

Shared foundational building blocks used across the service.

Scope boundary:
    ``app.core`` should contain cross-cutting primitives, not business
    workflows. If logic answers domain questions (tenant rules, deployment
    policy, inference orchestration), it belongs outside this package.

Step-by-step relationship flow:
    1. ``settings`` loads environment and YAML-backed configuration.
    2. ``logging`` configures structured log output using those settings.
    3. ``request_context`` propagates request correlation identifiers.
    4. ``exceptions`` provides typed error contracts used by all layers.

Package structure:
    - ``exceptions.py``: typed domain/service exception hierarchy.
    - ``logging.py``: JSON/text logging formatters and startup configuration.
    - ``request_context.py``: async-safe request ID storage via ``ContextVar``.
    - ``settings/``: configuration loading and immutable settings models.

Author: Shubham Singh
"""

from __future__ import annotations

from . import exceptions, settings

__all__: list[str] = [
    "exceptions",
    "settings",
]
