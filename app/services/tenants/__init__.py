"""
Tenant Services
===============

Business services for tenant administration, tenant membership, and tenant
deployment configuration.

This sub-package contains:
    - ``TenantService``: tenant account lifecycle (create, update, status
      transitions, delete) with tenant-scope authorization controls.
    - ``TenantMembershipService``: role assignment of users within tenants,
      including membership-scoped access and cache invalidation side effects.
    - ``TenantDeploymentService``: deployment key configuration that maps
      tenant routes to provider/model selections for inference execution.

Rationale:
    Tenant operations are tightly related but involve different invariants.
    Grouping them under one package improves discoverability while preserving
    clear service ownership boundaries.

Enterprise Pattern: Domain Service Sub-Package
    Domain-related service classes are co-located to keep the top-level
    ``app/services`` package easy to scan for new contributors.

Author: Shubham Singh
"""
