# Tenant services

## The short version

A tenant is one customer, organization, or team. This package manages the
tenant itself and two kinds of records inside it:

- **memberships** — which users belong to the tenant and their tenant roles;
- **deployments** — tenant-named AI routes such as `support-chat`.

These services do more than database CRUD. They enforce tenant access,
validate related records, protect credentials, and invalidate cached
authorization whenever a change could affect inference access.

## Vocabulary

| Term | Plain-language meaning |
| --- | --- |
| Tenant | An isolated customer or organization. |
| Membership | A link between one user and one tenant, including the user's tenant role. |
| Platform role | A global role such as developer, operator, admin, or owner. |
| Tenant role | A role that applies only inside one tenant. |
| Deployment | A tenant-owned route key mapped to a provider, model, settings, and credential reference. |
| Authorization cache | Cached proof that a user may use a tenant/deployment route. |
| Invalidation | Changing a version marker so old cached authorization can no longer be reused. |
| Persistence | The database-facing class that performs SQL operations. |

## How the three services fit together

```text
TenantService
    owns the organization lifecycle and tenant-wide status

TenantMembershipService
    owns which users belong to that organization and their tenant roles

TenantDeploymentService
    owns the organization's named provider/model routes and credentials
```

They are separate because each record has different authorization and cache
invalidation rules.

## Tenant lifecycle

`TenantService` creates, lists, reads, updates, suspends, activates, and deletes
tenant records.

- Creation and broad listing rely on platform-level guards in the API router.
- Reading one tenant requires tenant-read access.
- Updating, suspending, or activating requires tenant-admin access.
- Deletion relies on the stricter platform-owner route guard.
- Status changes and deletion invalidate the entire tenant authorization
  scope.

Suspension does not delete configuration. It makes current tenant policy deny
inference until the tenant is activated again.

## Membership lifecycle

A membership grants a user a role inside one tenant.

Creating or changing a membership follows this order:

```text
Check caller has tenant-admin access
    -> confirm referenced tenant and user exist
    -> write membership
    -> invalidate authorization for that tenant + user
```

Reads require tenant-read access. User-oriented list/count operations also
apply self-or-platform-admin checks.

When reading by `membership_id`, the service verifies that the returned row
belongs to the tenant in the URL. This prevents a real ID from another tenant
being used for cross-tenant access.

## Deployment lifecycle

A deployment maps a stable tenant key, such as `support-chat`, to an active
provider/model pair and its runtime settings.

Creation performs these steps:

1. require tenant-admin access;
2. confirm tenant, active provider, and active provider-owned model exist;
3. encode the submitted credential according to provider authentication mode;
4. store only the resulting secret reference in PostgreSQL;
5. create the deployment row;
6. invalidate authorization for that deployment key.

Reads require tenant-read access and confirm the deployment belongs to the
tenant in the URL. Updates, activation, maintenance mode, and deletion require
tenant-admin access and invalidate the affected route.

Routing reads deployment configuration directly from PostgreSQL. The cache
being invalidated here is the authorization-grant cache, not a second copy of
deployment configuration.

## Why invalidation is part of the write

Imagine a user has a cached “allowed” result and an administrator then removes
their membership or suspends the tenant. If the write succeeds but cache
invalidation is skipped, the old result could remain usable.

For that reason, invalidation is mandatory business behavior, not optional
cleanup. A mutation is not complete until its relevant invalidation finishes.

| Change | Invalidation scope |
| --- | --- |
| Tenant status or deletion | Entire tenant |
| Membership create/update/delete | Tenant + user |
| Deployment create/update/status/delete | Tenant + deployment key |

## Credentials and returned rows

Plaintext credentials are sent to the configured credential writer, such as
Vault. PostgreSQL receives an opaque `secret_reference`, not the plaintext
secret. Returned rows pass through `clean_row()` or `clean_rows()`, which remove
secret-bearing fields before they leave the service layer.

## File map

1. [`tenant.py`](tenant.py) — tenant lifecycle and tenant-wide invalidation.
2. [`membership.py`](membership.py) — user-to-tenant roles and member-scope
   invalidation.
3. [`deployment.py`](deployment.py) — provider/model routes, credential
   storage, lifecycle, and route invalidation.
4. [`__init__.py`](__init__.py) — package overview.
5. [`../management_reference_validation.py`](../management_reference_validation.py)
   — checks related tenant, user, provider, and model records before writes.
6. [`../credential_encoding.py`](../credential_encoding.py) — converts typed
   credentials into secret references.
7. [`../../auth/authorization/tenant_access.py`](../../auth/authorization/tenant_access.py)
   — tenant read/admin and self/admin authorization rules.

The matching HTTP routers are under
[`../../api/management_routers`](../../api/management_routers).

## Rules to preserve

1. Authorize before reading or mutating tenant-scoped data.
2. Verify a fetched child record belongs to the tenant in the request path.
3. Validate foreign references before persistence writes.
4. Store secret references, never plaintext credentials, in PostgreSQL.
5. Invalidate the exact authorization scope after every access-affecting
   mutation.
6. Clean all rows before returning them.
7. Keep service code independent of HTTP status codes.

