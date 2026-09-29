"""Shared identifier constraints used at every API and model boundary."""

from __future__ import annotations

KEBAB_IDENTIFIER_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"

__all__ = ["KEBAB_IDENTIFIER_PATTERN"]
