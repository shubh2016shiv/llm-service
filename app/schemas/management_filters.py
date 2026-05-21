"""
Management List Filters
=======================

Typed filter DTOs shared by management list and count operations.

Architecture:
-------------
    management API routes
        |
        v
    management filter DTOs
        |
        v
    execution services -> database persistence

Dependencies:
    - stdlib dataclasses and UUID types only

Author: Engineering Team
Last Updated: 2026-05-22
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
