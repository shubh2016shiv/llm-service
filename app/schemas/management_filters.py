"""
Management List Filters
=======================

Typed filter objects shared by management list and count operations.

Why explicit filter objects instead of just passing raw query parameters?
    Each management endpoint accepts optional filters (status, tier, provider,
    active-only, etc.). Wrapping them in a frozen dataclass gives the filter
    a name, a type, and a single place to live. The route handler parses query
    strings into a filter object; the service and persistence layers receive
    one typed argument instead of a handful of loose strings and booleans.
    This keeps the query-parsing concern out of the business logic.

Enterprise Pattern: Query Filter Object Pattern
    Route handlers produce filter objects from HTTP query strings. Services
    consume filter objects without knowing where the values came from.

Author: Shubham Singh
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID


@dataclass(frozen=True, slots=True)
class TenantListFilters:
    """Filter contract for tenant list and count operations."""

    status_filter: str | None = None
    tier_filter: str | None = None


@dataclass(frozen=True, slots=True)
class TenantMembershipListFilters:
    """Filter contract for tenant membership list and count operations."""

    tenant_role_filter: str | None = None
    active_only: bool = False


@dataclass(frozen=True, slots=True)
class TenantDeploymentListFilters:
    """Filter contract for tenant deployment list and count operations."""

    provider_id: UUID | None = None
    active_only: bool = False
