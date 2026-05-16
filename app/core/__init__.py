"""
Core Infrastructure Package
============================

Domain-agnostic infrastructure for the LLM Provider Service.
This package contains zero business logic — only shared foundations.

Architecture:
-------------
    ┌─────────────────────────────────────────────────────────────────┐
    │                        app.core                                  │
    ├──────────────┬──────────────┬──────────────┬────────────────────┤
    │  settings/     │ exceptions   │   logging    │   secret_store     │
    │              │              │              │                    │
    │  settings    │  Error       │  JSON        │  SecretStore       │
    │  loader      │  hierarchy   │  formatter   │  (abstract +       │
    │  models      │              │  Structured  │   env impl)        │
    │  (frozen     │              │  Logger      │                    │
    │   Pydantic)  │              │              │                    │
    └──────────────┴──────────────┴──────────────┴────────────────────┘

Dependencies:
    - pydantic >= 2.0          — Config model validation
    - pydantic-settings >= 2.0 — Environment variable loading
    - cryptography             — AES-GCM encryption for secrets
    - pyyaml                   — YAML settings file parsing

Author: Engineering Team
Last Updated: 2026-05-16
"""

from __future__ import annotations

from . import exceptions, settings

__all__: list[str] = [
    "exceptions",
    "settings",
]
