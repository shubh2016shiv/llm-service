# API Endpoints Reference — Multi-Tenant LLM Provider Service

> **Audience**: Engineers building, extending, or integrating with this service. This document covers every REST endpoint, its auth requirements, the rationale behind each access decision, request flow diagrams, and the enterprise patterns the codebase enforces.

---

## Table of Contents

1. [Role Hierarchy](#1-role-hierarchy)
2. [Entity-Relationship Context](#2-entity-relationship-context)
3. [Request Flow Diagrams](#3-request-flow-diagrams)
4. [Complete Endpoint Reference](#4-complete-endpoint-reference)
5. [Endpoint Count Summary](#5-endpoint-count-summary)
6. [Cross-Cutting Concerns](#6-cross-cutting-concerns)
7. [Role-to-Operation Matrix](#7-role-to-operation-matrix)
8. [Enterprise Patterns](#8-enterprise-patterns)

---

## 1. Role Hierarchy

The system defines two overlapping role namespaces — one platform-wide, one tenant-scoped — and a unified set of FastAPI auth guards that enforce access at the endpoint level.

### 1.1 Platform Roles (`users.platform_role`)

Stored on the user's identity record. Determines what the user can do **across the entire platform**.

| Role | Meaning |
|------|---------|
| `owner` | Platform owner. Unrestricted access to every tenant and every resource. |
| `admin` | Platform administrator. Can manage tenants, users, providers, and models globally. |
| `operator` | Platform operator. Can view all tenants and operational state but cannot create or destroy. |
| `developer` | Default. No special platform-level privilege. All authority comes from tenant membership. |

### 1.2 Tenant Roles (`tenant_memberships.tenant_role`)

Stored on the membership record that links a user to a tenant. Determines what the user can do **within that specific tenant**.

| Role | Meaning |
|------|---------|
| `owner` | Tenant owner. Full control over the tenant, its deployments, and its members. |
| `admin` | Tenant administrator. Can manage deployments and members, but cannot delete the tenant. |
| `operator` | Tenant operator. Can view and manage operational state but not change configuration. |
| `developer` | Tenant developer. Can use LLM inference endpoints and read configuration. |
| `viewer` | Read-only observer. Can list resources but cannot invoke inference. |

### 1.3 Auth Guards

FastAPI `Depends()` guards defined in `app/auth/auth_dependencies.py`. Each guard admits a set of **role names** — higher roles inherit lower roles' access. Guard instances are module-level singletons; their permitted role sets are validated at startup against the canonical `UserRole` Literal, so a misconfigured guard fails at process start rather than at request time.

| Guard | Permitted Roles | Used For |
|-------|-----------------|----------|
| `require_developer` | `developer`, `operator`, `admin`, `owner` | Read operations, inference |
| `require_operator` | `operator`, `admin`, `owner` | Cross-tenant read operations |
| `require_admin` | `admin`, `owner` | Create, update, delete, lifecycle control |
| `require_owner` | `owner` | Platform-destructive operations |

> **Note:** Routes protected by `require_admin` admit a user who holds **either** a platform `admin` role **or** the `admin` tenant role for the relevant tenant. Where tenant-scoped authority is required beyond the JWT role check, the service layer performs an additional membership lookup after the guard passes.

---

## 2. Entity-Relationship Context

Understanding the URL structure requires understanding the entity hierarchy:

```
provider_catalog ──────────────< model_catalog
(global)                          (global, per provider)

tenants
(org boundary, rate limits, provider policy)
├── tenant_memberships ─────────> users
│   (tenant_role per user)         (platform_role, identity)
├── tenant_deployments
│   (tenant + provider + model + deployment_key + secret_ref + capacity)
└── user_entitlements
    (user-specific override of a tenant deployment route)
```

**URL nesting follows ownership:**

| URL Pattern | Meaning |
|-------------|---------|
| `/api/v1/providers/...` | Global catalog — no tenant prefix |
| `/api/v1/tenants/{tenant_id}/...` | Scoped to one tenant |
| `/api/v1/users/{user_id}/...` | Scoped to one user identity |
| `/api/v1/tenants/{tenant_id}/members/...` | Bridge: users within a tenant |
| `/api/v1/tenants/{tenant_id}/deployments/...` | Routing config within a tenant |
| `/api/v1/users/{user_id}/entitlements/...` | User-specific routing overrides |
| `/api/v1/llm/...` | Inference — tenant and deployment supplied via headers |

---

## 3. Request Flow Diagrams

### 3.1 Inference Request Flow

Every `POST /api/v1/llm/{chat|embed|rerank}` passes through two sequential dependency chains before the handler body executes. Both chains run as FastAPI `Depends()` — they raise `HTTPException` directly if they fail, so the handler body only runs on a fully authorized, fully resolved context.

```
HTTP Client
    │
    │  Headers: Authorization: Bearer <JWT>
    │           X-Tenant-ID: <uuid>
    │           X-Deployment-Key: <string>
    │
    ▼
[Middleware] attach_request_id                    main.py
    Reads X-Request-ID from caller or generates UUID4.
    Stores in ContextVar. Echoes back in response header.
    │
    ▼
[Router] api/llm_inference_router.py
    │
    ├── [Depends] require_inference_access         api/dependencies.py
    │       │
    │       ├── [Depends] get_current_user         auth/auth_dependencies.py
    │       │       Extracts Bearer token from Authorization header.
    │       │       Calls decode_token() — validates signature + expiry (no DB).
    │       │       Calls verify_token_type() — guards against refresh token misuse.
    │       │       → AuthTokenPayload (user_id, role, expires_at)
    │       │
    │       └── [Depends] get_tenant_authorization_service
    │               └── TenantAuthorizationService.authorize_inference()
    │                       │
    │                       ├── InferenceAuthorizationCache.get_entry()
    │                       │     Redis read. On cache HIT with matching version
    │                       │     snapshot → short-circuit, return cached context.
    │                       │
    │                       ├── TenantPersistence.get_tenant_by_id()
    │                       │     Verifies tenant exists and is active/trial.
    │                       │
    │                       ├── TenantMembershipPersistence.get_membership()
    │                       │     Verifies caller is an active member of the tenant
    │                       │     with a role in {developer, operator, admin, owner}.
    │                       │
    │                       ├── TenantDeploymentPersistence.get_deployment_by_key()
    │                       │     Resolves deployment_key → deployment row.
    │                       │     Verifies deployment status == active.
    │                       │
    │                       ├── UserEntitlementPersistence
    │                       │       .get_active_entitlement_for_route()
    │                       │     Requires an active entitlement linking
    │                       │     (user, tenant, deployment_key, provider, model).
    │                       │
    │                       └── InferenceAuthorizationCache.set()
    │                             On cache MISS: write context + version snapshot.
    │                             Skipped if versions changed mid-query (stale guard).
    │                             → InferenceAccessContext
    │
    └── [Depends] _get_inference_service           Retrieved from app.state
            (process-scoped singleton — ProviderRegistry cache lives here)
            │
            └── InferenceService.execute_{chat|embed|rerank}()
                    │
                    ├── TokenManagerClient.check_quota()
                    │     Verifies token budget for (tenant, user, deployment).
                    │
                    ├── ProviderRegistry.get_or_create(provider_name)
                    │     Returns a cached BaseProvider instance or builds one.
                    │
                    └── provider.generate(request)
                            Translates normalized request to provider wire format.
                            Executes HTTP call to external LLM API.
                            Maps provider errors → domain exceptions.
                            → ChatResponse | SSE stream | EmbedResponse | RerankResponse
    │
    ▼
[ExceptionHandler] translate_inference_error      api/exception_handlers.py
    Only reached if InferenceService raises LLMServiceError.
    MRO-walk maps exception type → HTTP status code.
    Propagates Retry-After header for RateLimitError subtypes (RFC 6585 §4).

    ↓ (safety net — should not fire if route handlers are complete)
[ExceptionHandler] _on_unhandled_llm_service_error
    Global fallback. Logs ERROR (5xx) or WARNING (4xx). Returns JSON response.
    │
    ▼
HTTP Response
```

---

### 3.2 Management Request Flow

Management requests (CRUD on tenants, users, deployments, etc.) follow a simpler path: JWT guard → service call → persistence.

```
HTTP Client
    │
    │  Header: Authorization: Bearer <JWT>
    │
    ▼
[Middleware] attach_request_id                    main.py
    │
    ▼
[Router] api/management_routers/{entity}_router.py
    │
    ├── [Depends] require_{admin|developer|...}   auth/auth_dependencies.py
    │       Validates JWT. Checks role against permitted set.
    │       → AuthTokenPayload
    │
    └── [Depends] get_{entity}_service            api/dependencies.py
            Constructs the service with injected persistence + auth helpers.
            │
            └── {Entity}Service.{method}()        services/{layer}/{entity}.py
                    │
                    ├── TenantAccessService.ensure_tenant_{read|admin}()
                    │     Optional. Performs tenant-scoped membership check
                    │     beyond the JWT role guard. Platform admins bypass.
                    │
                    ├── ManagementReferenceValidationService (on create/update)
                    │     Validates that referenced UUIDs (provider_id, model_id,
                    │     tenant_id, user_id) exist before INSERT/UPDATE.
                    │
                    ├── {Entity}Persistence.{operation}()
                    │     Executes parameterised SQL via asyncpg.
                    │     Returns plain dict row(s).
                    │
                    └── InferenceAuthorizationCache.invalidate_{scope}()
                          On any mutation that affects the inference path
                          (deployment create/update/delete, membership change,
                          entitlement change): advance the relevant version key
                          in Redis so in-flight cached grants are invalidated.
    │
    ▼
[ExceptionHandler] translate_management_error     api/exception_handlers.py
    MRO-walk maps exception → HTTP status. Logs structured WARNING with request_id.
    │
    ▼
HTTP Response  (ResourceResponse | PaginatedResponse | 204 No Content)
```

---

### 3.3 Cache Invalidation Flow

When a management mutation touches an entity that sits in the inference authorization cache, the service layer advances a version key in Redis. The next inference request for that route will find its cached version snapshot does not match the current snapshot and will re-execute the full DB authorization query.

```
Mutation endpoint (deployment/membership/entitlement change)
    │
    └── {Entity}Service.{mutate}()
            │
            ├── {Entity}Persistence.{insert|update|delete}()
            │
            └── InferenceAuthorizationCache.invalidate_{scope}()
                    Scope is the smallest unit that covers the affected grants:

                    invalidate_tenant(tenant_id)
                        → bumps tenant-scope version key
                        → invalidates ALL grants for ALL users of that tenant

                    invalidate_membership(tenant_id, user_id)
                        → bumps membership-scope version key
                        → invalidates ALL grants for that user in that tenant

                    invalidate_deployment(tenant_id, deployment_key)
                        → bumps deployment-scope version key
                        → invalidates ALL grants targeting that deployment route

                    invalidate_route(tenant_id, user_id, deployment_key)
                        → bumps route-scope version key
                        → also deletes the specific grant key directly
                        → most targeted: only affects one user+deployment pair
```

---

## 4. Complete Endpoint Reference

### 4.0 Health

| # | Method | Path | Auth | Status |
|---|--------|------|------|--------|
| 1 | GET | `/health` | None | ✅ |
| 2 | GET | `/health/ready` | None | ✅ |

`/health` — liveness probe. No I/O. Returns `{"status": "ok"}`. Safe to poll at high frequency.

`/health/ready` — readiness probe. Checks every runtime dependency (currently: Redis). Returns `{"status": "ready", "dependencies": {...}}` on 200 or `{"status": "degraded", "dependencies": {...}}` on 503. Orchestrators (Kubernetes) use this to gate traffic.

---

### 4.1 LLM Inference — `/api/v1/llm`

All three endpoints require **two request headers** in addition to the `Authorization: Bearer <JWT>`:

| Header | Type | Constraint |
|--------|------|------------|
| `X-Tenant-ID` | UUID | Must identify an active or trial tenant. |
| `X-Deployment-Key` | string | 1–128 chars, pattern `[A-Za-z0-9][A-Za-z0-9_.-]{0,127}`. Must exist and be active for the tenant. |

The caller never specifies provider, model, or credentials directly. Those are resolved by the authorization + deployment pipeline.

| # | Method | Path | Guard | Stream Support | Status |
|---|--------|------|-------|----------------|--------|
| 1 | POST | `/api/v1/llm/chat` | `require_inference_access` | Yes — `stream: true` in body returns SSE | ✅ |
| 2 | POST | `/api/v1/llm/embed` | `require_inference_access` | No | ✅ |
| 3 | POST | `/api/v1/llm/rerank` | `require_inference_access` | No | ✅ |

**SSE wire format** (`/chat` with `stream: true`):
```
data: {"content": "The", "finish_reason": null, "index": 0}

data: {"content": " answer", "finish_reason": null, "index": 0}

data: {"content": " is 42.", "finish_reason": "stop", "index": 0}

data: [DONE]

# On mid-stream provider error:
data: {"error": {"code": "RATE_LIMIT_ERROR", "message": "..."}}

data: [DONE]
```

#### Rationale

| Decision | Reasoning |
|----------|-----------|
| Tenant + deployment via headers, not body | Headers are transparent to the request body schema. The same `ChatRequest` Pydantic model works across any tenant/deployment combination. Body schema is purely semantic (messages, temperature, etc.). |
| `require_inference_access` as a single dependency | The full authorization chain (JWT decode → tenant check → membership check → deployment check → entitlement check → cache read/write) is encapsulated in one `Depends()` call. Route handlers receive a pre-validated `InferenceAccessContext` and never touch auth logic. |
| No `provider` or `model` in the request | The caller is abstracted from infrastructure. Swapping a deployment's underlying provider/model requires zero client changes. |

---

### 4.2 Provider Catalog — `/api/v1/providers`

Providers are global platform entities. Every tenant deployment references a provider by UUID.

| # | Method | Path | Guard | Status |
|---|--------|------|-------|--------|
| 1 | POST | `/api/v1/providers` | `require_admin` | ✅ |
| 2 | GET | `/api/v1/providers` | `require_developer` | ✅ |
| 3 | GET | `/api/v1/providers/{provider_id}` | `require_developer` | ✅ |
| 4 | PATCH | `/api/v1/providers/{provider_id}` | `require_admin` | ✅ |
| 5 | DELETE | `/api/v1/providers/{provider_id}` | `require_owner` | ✅ |

#### Rationale

| Decision | Reasoning |
|----------|-----------|
| Create/Update → `admin`/`owner` | Adding a provider expands the platform's capabilities. Requires knowledge of auth modes, endpoint URLs, and supported operations — a deliberate platform-engineering action. |
| Read → `developer`+ | Any authenticated caller needs to browse providers to configure deployments. The catalog is not sensitive. |
| Delete → `owner` only | Deleting a provider cascades to every model, deployment, and entitlement that references it. Irreversible and platform-destructive. |

---

### 4.3 Model Catalog — `/api/v1/providers/{provider_id}/models`

Models are nested under their provider. The UUID-based nesting enforces referential integrity and prevents orphaned model lookups.

| # | Method | Path | Guard | Status |
|---|--------|------|-------|--------|
| 1 | POST | `/api/v1/providers/{provider_id}/models` | `require_admin` | ✅ |
| 2 | GET | `/api/v1/providers/{provider_id}/models` | `require_developer` | ✅ |
| 3 | GET | `/api/v1/providers/{provider_id}/models/{model_id}` | `require_developer` | ✅ |
| 4 | PATCH | `/api/v1/providers/{provider_id}/models/{model_id}` | `require_admin` | ✅ |
| 5 | PATCH | `/api/v1/providers/{provider_id}/models/{model_id}/activate` | `require_admin` | ✅ |
| 6 | PATCH | `/api/v1/providers/{provider_id}/models/{model_id}/deactivate` | `require_admin` | ✅ |

#### Rationale

| Decision | Reasoning |
|----------|-----------|
| Create/Update → `admin`/`owner` | Registering a model sets context windows, pricing metadata, and supported operations. Incorrect values cause inference failures at runtime. |
| Read → `developer`+ | Tenants must browse available models to configure deployments. |
| Activate/Deactivate → `admin`/`owner` | Deactivation is a soft-delete that prevents new deployments from selecting the model without destroying existing deployment records. |
| Nested under provider | A model record is meaningless without its provider context. Nesting enforces this at the URL level. |

---

### 4.4 Tenants — `/api/v1/tenants`

Tenants are the top-level organisational boundary. Every deployment, membership, entitlement, and token allocation is scoped to a tenant.

| # | Method | Path | Guard | Status |
|---|--------|------|-------|--------|
| 1 | POST | `/api/v1/tenants` | `require_admin` | ✅ |
| 2 | GET | `/api/v1/tenants` | `require_operator` | ✅ |
| 3 | GET | `/api/v1/tenants/{tenant_id}` | `require_developer` + membership check | ✅ |
| 4 | PATCH | `/api/v1/tenants/{tenant_id}` | `require_admin` + tenant-admin check | ✅ |
| 5 | PATCH | `/api/v1/tenants/{tenant_id}/suspend` | `require_admin` | ✅ |
| 6 | PATCH | `/api/v1/tenants/{tenant_id}/activate` | `require_admin` | ✅ |
| 7 | DELETE | `/api/v1/tenants/{tenant_id}` | `require_owner` | ✅ |

#### Rationale

| Decision | Reasoning |
|----------|-----------|
| Create → `admin`/`owner` | Tenant creation is an onboarding action that sets billing tier, rate limits, and provider policy. The creating admin typically becomes the tenant's first owner via a membership created in the same transaction. |
| List (global) → `operator`+ | The full tenant list is an operational concern. A `developer` in Tenant A has no legitimate need to know Tenant B exists. |
| Get single → `developer`+ with membership check | A user may only inspect a tenant they belong to. The JWT guard ensures authentication; the membership check enforces tenant-scoped isolation. Platform `operator`+ bypasses the membership check. |
| Update → tenant `admin`/`owner` | Rate limits, tier, and provider policy are tenant-level configuration that the tenant's own administrators should control. |
| Suspend/Activate → `admin`/`owner` | Lifecycle state changes may be initiated by the tenant's admin (self-service) or a platform admin (compliance action). |
| Delete → `owner` only | Tenant deletion cascades to every deployment, membership, entitlement, and allocation. Irreversible. |

---

### 4.5 Tenant Memberships — `/api/v1/tenants/{tenant_id}/members`

Memberships bridge users to tenants. They carry the tenant-scoped role that governs what a user can do within a specific tenant.

| # | Method | Path | Guard | Status |
|---|--------|------|-------|--------|
| 1 | POST | `/api/v1/tenants/{tenant_id}/members` | `require_admin` + tenant-admin check | ✅ |
| 2 | GET | `/api/v1/tenants/{tenant_id}/members` | `require_developer` + membership check | ✅ |
| 3 | GET | `/api/v1/tenants/{tenant_id}/members/{membership_id}` | `require_developer` + membership check | ✅ |
| 4 | PATCH | `/api/v1/tenants/{tenant_id}/members/{membership_id}` | `require_admin` + tenant-admin check | ✅ |
| 5 | DELETE | `/api/v1/tenants/{tenant_id}/members/{membership_id}` | `require_admin` + tenant-admin check | ✅ |

#### Rationale

| Decision | Reasoning |
|----------|-----------|
| Create → tenant `admin`/`owner` | Adding a member grants access to the tenant's deployments and data. Only the tenant's own administrators should make this decision, verified by a DB membership lookup — not just the JWT role. |
| List/Get → tenant member | Membership lists (who else is in my tenant) are visible to any active member for collaboration. Non-members must not see the list. |
| Update/Delete → tenant `admin`/`owner` | Changing a member's role or removing them is a tenant-level security decision. |

---

### 4.6 User-Centric Membership View — `/api/v1/users/{user_id}/memberships`

| # | Method | Path | Guard | Status |
|---|--------|------|-------|--------|
| 1 | GET | `/api/v1/users/{user_id}/memberships` | `require_developer` | ✅ |

The service layer restricts non-admin callers to querying only their own `user_id`. Platform `admin`/`owner` may query any user.

---

### 4.7 Tenant Deployments — `/api/v1/tenants/{tenant_id}/deployments`

Deployments are the core routing records. Each row answers: *for this tenant, map requests with this `deployment_key` to this provider + model, using this credential reference.*

| # | Method | Path | Guard | Status |
|---|--------|------|-------|--------|
| 1 | POST | `/api/v1/tenants/{tenant_id}/deployments` | `require_admin` + tenant-admin check | ✅ |
| 2 | GET | `/api/v1/tenants/{tenant_id}/deployments` | `require_developer` + membership check | ✅ |
| 3 | GET | `/api/v1/tenants/{tenant_id}/deployments/{deployment_id}` | `require_developer` + membership check | ✅ |
| 4 | PATCH | `/api/v1/tenants/{tenant_id}/deployments/{deployment_id}` | `require_admin` + tenant-admin check | ✅ |
| 5 | PATCH | `/api/v1/tenants/{tenant_id}/deployments/{deployment_id}/activate` | `require_admin` + tenant-admin check | ✅ |
| 6 | PATCH | `/api/v1/tenants/{tenant_id}/deployments/{deployment_id}/maintenance` | `require_admin` + tenant-admin check | ✅ |
| 7 | DELETE | `/api/v1/tenants/{tenant_id}/deployments/{deployment_id}` | `require_admin` + tenant-admin check | ✅ |

#### Rationale

| Decision | Reasoning |
|----------|-----------|
| Create → tenant `admin`/`owner` | Provisioning a deployment involves selecting a provider/model, storing a credential reference, and setting token capacity. A deliberate infrastructure action. |
| List/Get → tenant member | Any member needs to know available `deployment_key` values to make inference requests. |
| Update → tenant `admin`/`owner` | Changing endpoint URLs, credentials, or capacity affects live inference traffic. Mistakes cause outages. |
| `maintenance` status | Signals "do not route new requests here" without destroying the deployment record. Allows drain-and-replace operations without downtime. |
| Delete → tenant `admin`/`owner` | Destroying a deployment immediately stops all routing through it. Any cached authorization grants for that deployment key are invalidated via `InferenceAuthorizationCache.invalidate_deployment()`. |

---

### 4.8 Users — `/api/v1/users`

| # | Method | Path | Guard | Status |
|---|--------|------|-------|--------|
| 1 | POST | `/api/v1/users` | `require_admin` | ✅ |
| 2 | GET | `/api/v1/users` | `require_admin` | ✅ |
| 3 | GET | `/api/v1/users/email/{email}` | `require_developer` | ✅ |
| 4 | GET | `/api/v1/users/{user_id}` | `require_developer` | ✅ |
| 5 | PATCH | `/api/v1/users/{user_id}` | `require_admin` | ✅ |
| 6 | PATCH | `/api/v1/users/{user_id}/suspend` | `require_admin` | ✅ |
| 7 | PATCH | `/api/v1/users/{user_id}/activate` | `require_admin` | ✅ |
| 8 | DELETE | `/api/v1/users/{user_id}` | `require_admin` | ✅ |

> **Route ordering note:** `/email/{email}` is registered before `/{user_id}` in `user_router.py` so the literal `/email/` path segment takes precedence over the parameterised UUID segment in FastAPI's route resolution.

#### Rationale

| Decision | Reasoning |
|----------|-----------|
| Create → `admin`/`owner` | User creation provisions a new platform identity with a platform role. Self-registration is not supported — this is an administrative action. |
| List → `admin`/`owner` | The full user directory contains PII (email, status). Only platform administrators should enumerate all users. |
| Get single → `developer`+ | A user can look up their own profile. Cross-user lookup is permitted for collaboration (finding a teammate's UUID) but still requires authentication. |
| Update/lifecycle → `admin`/`owner` | Changing email, role, or status is an administrative action with compliance implications. |

---

### 4.9 User Entitlements — `/api/v1/users/{user_id}/entitlements`

Entitlements are user-specific routing overrides. They exist for cases where a user has an approved personal API key or a user-specific endpoint for the same tenant route.

| # | Method | Path | Guard | Status |
|---|--------|------|-------|--------|
| 1 | POST | `/api/v1/users/{user_id}/entitlements` | `require_admin` | ✅ |
| 2 | GET | `/api/v1/users/{user_id}/entitlements` | `require_developer` | ✅ |
| 3 | GET | `/api/v1/users/{user_id}/entitlements/{entitlement_id}` | `require_developer` | ✅ |
| 4 | PATCH | `/api/v1/users/{user_id}/entitlements/{entitlement_id}` | `require_admin` | ✅ |
| 5 | DELETE | `/api/v1/users/{user_id}/entitlements/{entitlement_id}` | `require_admin` | ✅ |

#### Rationale

| Decision | Reasoning |
|----------|-----------|
| Create → `admin`/`owner` | Granting a user a personal credential override is a privileged action. The entitlement carries a `secret_reference` and must be approved by an administrator. |
| List/Get → `developer`+ | A user must be able to see their own entitlements. The service layer restricts non-admin callers to their own `user_id`. |
| Update/Delete → `admin`/`owner` | Modifying or revoking an entitlement affects that user's LLM access path. On delete, `InferenceAuthorizationCache.invalidate_route()` is called for the affected (user, tenant, deployment_key) triple. |

---

## 5. Endpoint Count Summary

| Module | Router File | Endpoints |
|--------|-------------|-----------|
| Health | `main.py` | 2 |
| LLM Inference | `llm_inference_router.py` | 3 |
| Provider Catalog | `management_routers/catalog_router.py` | 5 |
| Model Catalog | `management_routers/catalog_router.py` | 6 |
| Tenants | `management_routers/tenant_router.py` | 7 |
| Tenant Memberships | `management_routers/tenant_router.py` | 5 |
| User Memberships (view) | `management_routers/user_router.py` | 1 |
| Tenant Deployments | `management_routers/deployment_router.py` | 7 |
| Users | `management_routers/user_router.py` | 8 |
| User Entitlements | `management_routers/entitlement_router.py` | 5 |
| **Total** | | **49** |

All 49 endpoints are implemented and registered. No endpoints are commented out or pending.

---

## 6. Cross-Cutting Concerns

### 6.1 Two-Layer Authorization on Tenant-Scoped Endpoints

Endpoints that operate on tenant resources apply authorization at two independent layers:

```
Layer 1 — JWT guard (e.g., require_admin)
    Validates the token signature and expiry.
    Checks the platform role from the JWT claim against the permitted set.
    Stateless — no database I/O.

Layer 2 — Membership check (TenantAccessService)
    Queries tenant_memberships for the (tenant_id from URL, user_id from JWT) pair.
    Verifies the membership is active and the tenant_role meets the requirement.
    Platform admin/owner roles bypass this check.
```

This prevents a platform-level `admin` from accidentally operating on a tenant they do not belong to, while still allowing platform `owner` to override when necessary.

### 6.2 Secret References Never Exposed

Deployment and entitlement records carry a `secret_reference` — a pointer into HashiCorp Vault or environment variables. The **actual credential value is never returned in any API response**. Create/update endpoints accept the value, store it in the secret store backend, and persist only the opaque reference string.

### 6.3 Pagination

All list endpoints support cursor-free pagination via two query parameters:

| Parameter | Type | Default | Maximum | Description |
|-----------|------|---------|---------|-------------|
| `limit` | int | 100 | 1000 | Number of records to return. |
| `offset` | int | 0 | — | Number of records to skip before returning results. |

All list responses use `PaginatedResponse`:
```json
{
  "items": [...],
  "total": 42,
  "limit": 100,
  "offset": 0
}
```

`total` reflects the count matching the applied filters, allowing the client to compute page count without a separate request.

### 6.4 Router Registration

All routers are registered in `app/main.py`:

```python
app.include_router(llm_inference_router)   # from app.api
app.include_router(management_router)      # from app.api.management_routers
register_exception_handlers(app)           # from app.api.exception_handlers
```

`management_router` is an aggregated `APIRouter` defined in `app/api/management_routers/__init__.py` that includes all five sub-routers (tenant, deployment, user, entitlement, catalog). Adding a new management router requires only adding one `include_router` call to that `__init__.py`.

### 6.5 Exception Handling Architecture

Exception translation is centralised in `app/api/exception_handlers.py`. No status code decision is made anywhere else.

```
Route handler catches LLMServiceError
    │
    ├── Inference path  → translate_inference_error(exc)
    │                     MRO walk against _INFERENCE_EXCEPTION_STATUS.
    │                     Adds Retry-After header for RateLimitError subtypes.
    │
    └── Management path → translate_management_error(exc)
                          MRO walk against _MANAGEMENT_EXCEPTION_STATUS.
                          Logs structured WARNING with request_id before raising.

Global safety net (registered via register_exception_handlers):
    Any LLMServiceError that escaped route-level handling.
    Logs ERROR (5xx) or WARNING (4xx).
    Should never fire in normal operation — treat each firing as a bug report.
```

---

## 7. Role-to-Operation Matrix

| Operation | `developer` (platform) | Tenant `viewer` | Tenant `developer` | Tenant `operator` | Tenant `admin` | Tenant `owner` | Platform `operator` | Platform `admin` | Platform `owner` |
|-----------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Health checks | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Read providers/models | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Create/update providers/models | — | — | — | — | — | — | — | ✓ | ✓ |
| Delete provider | — | — | — | — | — | — | — | — | ✓ |
| Invoke LLM inference | — | — | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ |
| Read own tenant | — | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| List all tenants | — | — | — | — | — | — | ✓ | ✓ | ✓ |
| Create tenant | — | — | — | — | — | — | — | ✓ | ✓ |
| Update/suspend/activate tenant | — | — | — | — | ✓ | ✓ | — | ✓ | ✓ |
| Delete tenant | — | — | — | — | — | — | — | — | ✓ |
| Read tenant members | — | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ |
| Add/update/remove members | — | — | — | — | ✓ | ✓ | — | ✓ | ✓ |
| Read deployments | — | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ |
| Create/update/delete deployments | — | — | — | — | ✓ | ✓ | — | ✓ | ✓ |
| Read own user profile | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| List all users | — | — | — | — | — | — | — | ✓ | ✓ |
| Create/update/delete users | — | — | — | — | — | — | — | ✓ | ✓ |
| Read own entitlements | — | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ |
| Create/update/delete entitlements | — | — | — | — | ✓ | ✓ | — | ✓ | ✓ |

---

## 8. Enterprise Patterns

Patterns applied consistently across the codebase. These are not aspirational — they are enforced in the current implementation.

| Pattern | Where Applied | What It Solves |
|---------|--------------|----------------|
| **MRO-based exception translation** | `api/exception_handlers.py` — `_resolve_status()` walks `type(exc).__mro__` against a static dict | No manual `isinstance` chains. Adding a new exception subclass with the parent already mapped requires zero changes to the translate functions. The most specific type always wins. |
| **Single source of truth for role sets** | `auth_schema.py` defines `UserRole = Literal[...]`. Both `jwt_token_service.py` and `auth_dependencies.py` derive `_VALID_ROLES` via `frozenset(get_args(UserRole))` | Adding or renaming a role is a one-file change. No risk of diverging copies silently accepting stale role names. |
| **Startup-time guard validation** | `RoleGuard.__init__` validates permitted roles against `_VALID_ROLES` at construction. All guards are module-level singletons — constructed at import time. | Misconfigured guards (`require_admin(["adimn"])`) fail the process on startup, not on the first request from a real user. |
| **Stateless JWT authentication** | `get_current_user` in `auth/auth_dependencies.py` — signature + expiry validated cryptographically, no DB query | Authentication is O(1) regardless of load. Horizontally scalable without a shared session store. |
| **ContextVar request ID propagation** | `set_request_id()` / `get_request_id()` in `core/request_context.py`, set by `attach_request_id` middleware | Request correlation ID is available anywhere in the async call stack without threading it through every function signature. All structured log entries include it automatically. |
| **Protocol-based cache backend** | `AuthorizationCacheBackend` Protocol in `auth/authorization/cache.py` | `InferenceAuthorizationCache` is not coupled to Redis. Any object implementing `get / set / delete` is a valid backend. Enables in-process dict cache for tests without mocking. |
| **Scoped cache invalidation** | `InferenceAuthorizationCache` exposes four invalidation scopes: tenant, membership, deployment, route | Smallest possible invalidation on each mutation. A deployment config change does not evict grants for unrelated deployments in the same tenant. |
| **Version-snapshot compare-and-swap** | `TenantAuthorizationService.authorize_inference()` reads version snapshot before DB query, re-reads after, only caches if snapshots match | Guards against caching a result that became stale during the DB query execution itself. A concurrent mutation between the pre-query and post-query snapshot reads prevents a stale write. |
| **Process-scoped service singleton** | `InferenceService` stored on `app.state` in `main.py` lifespan handler. Retrieved per-request via `_get_inference_service()` in the router | `ProviderRegistry` maintains an in-process HTTP connection pool and provider instance cache. Re-creating it per request would destroy those caches and leak connections. |
| **Dependency injection via `Depends()`** | All service construction in `api/dependencies.py` — persistence objects, auth services, and caches are injected, never constructed inside service methods | Services are independently testable. The dependency graph is explicit and visible. FastAPI's `Depends()` de-duplicates shared sub-dependencies within a request (e.g., `InferenceAuthorizationCache` is constructed once even if multiple services depend on it). |
| **Frozen Pydantic models for cache entries** | `ConfigDict(frozen=True)` on `InferenceAccessContext`, `AuthorizationVersionSnapshot`, `CachedInferenceAuthorization` | Immutable cache payloads cannot be accidentally mutated after retrieval. Frozen models are also hashable, enabling set/dict membership tests. |
| **Layer-enforced import boundaries** | `api/` never imports `database/`. `services/` never imports `api/`. `core/` imports nothing application-internal. | Violations are caught at import time, not at runtime. The dependency graph is a DAG with no cycles. |
| **Aggregated router `__init__.py`** | `api/management_routers/__init__.py` includes all five sub-routers into one `management_router` | `main.py` registers one router, not five. Adding a new management router is a single-line change in `__init__.py`. The sub-router files are independently readable and testable. |
| **RFC 6585 §4 `Retry-After` propagation** | `_retry_after_headers()` in `exception_handlers.py` — sets `Retry-After` header when `retry_after_seconds` is present on a `RateLimitError` | Callers (and upstream gateways) receive the provider-supplied retry delay. Prevents unnecessary retry storms and allows intelligent backoff without client-side hardcoding. |
