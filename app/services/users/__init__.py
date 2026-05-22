"""
User Services
=============

Business services for platform user accounts and user-specific entitlements.

This sub-package contains:
    - ``UserService``: core user lifecycle operations, including account
      status transitions and secure password hashing on creation.
    - ``UserEntitlementService``: user-specific deployment override records
      used by routing when per-user credentials or permissions apply.

Rationale:
    User identity management and user routing overrides are related but not
    identical concerns. Grouping them here keeps discoverability high while
    preserving dedicated service classes and test targets.

Enterprise Pattern: Domain Service Sub-Package
    Domain-focused service modules are co-located under ``users/`` to reduce
    navigation overhead for contributors.

Author: Shubham Singh
"""
