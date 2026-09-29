# Inference routing

## The short version

Inference routing decides **where an approved AI request should go**.

For example, after the service confirms that Alice may use her team's chat
deployment, inference routing turns that approval into an exact instruction:

> Send this chat request to this provider and model, using this endpoint and
> this approved secret reference, with these limits and defaults.

Routing does **not** decide whether Alice is allowed to make the request. That
security decision has already been made by authorization. Routing must preserve
that decision exactly; it must never substitute a different credential or
entitlement because one happens to be available.

## Vocabulary

| Term | Plain-language meaning |
| --- | --- |
| Tenant | A customer or organization using the service. |
| Deployment | A tenant-named route, such as `support-chat`, that points to an approved provider and model. |
| Provider | The company or system that runs the model, such as OpenAI, Anthropic, or AWS Bedrock. |
| Model | The specific AI model that will process the request. |
| Operation | The requested kind of work: chat, embedding, or reranking. |
| Entitlement | A record granting one user access to one exact provider/model credential. |
| Authorization | The earlier security check that proves the user may use that entitlement. |
| Secret reference | A pointer to a secret in Vault or another secret store. It is not the plaintext API key. |
| Catalog | Provider and model configuration loaded from YAML when the application starts. |
| Resolved route | The final, immutable set of instructions handed to provider execution. |
| Fail closed | Refuse the request when security information is missing or inconsistent instead of guessing or falling back. |

## A concrete security example

Suppose Alice is authorized to use entitlement **A**, which points to her
team's OpenAI credential.

Between authorization and execution, an administrator revokes entitlement A.
Routing re-reads that exact entitlement before using it. It sees the revocation
and refuses the request.

Routing does not search for entitlement **B** or silently use a shared company
credential. Doing that would execute the request with access Alice was never
granted. The caller must be authorized again against the current data.

This is why every routing request carries an exact `entitlement_id`.

## Where routing sits in the request flow

```text
User sends an inference request
    |
    v
Authentication proves who the user is
    |
    v
Authorization approves one exact entitlement_id
    |
    v
Inference routing
    |-- re-reads that exact entitlement from PostgreSQL
    |-- re-reads the tenant's current policy
    |-- confirms the provider is allowed
    |-- finds the provider and model in the startup catalog
    |-- confirms the model supports the requested operation
    |
    v
ResolvedRoute: one complete, immutable execution instruction
    |
    v
Provider execution
```

Re-reading the security-sensitive database records means a tenant suspension,
policy change, or entitlement revocation applies to the next request. There is
no stale routing cache between the authorization decision and these checks.

## What routing receives

`ResolutionRequest` contains only the information routing needs:

- tenant and user identifiers;
- the tenant-facing deployment key;
- the exact entitlement identifier approved by authorization;
- the requested operation, such as chat or embedding.

The provider and model are not freely selected from request input here. They
come from the approved entitlement stored in PostgreSQL.

```python
request = ResolutionRequest(
    tenant_id=access_context.tenant_id,
    user_id=access_context.user_id,
    deployment_key=access_context.deployment_key,
    entitlement_id=access_context.entitlement_id,
    operation=OperationType.CHAT,
)

route = await resolver.resolve_route(request)
```

`access_context` is the result of the earlier authorization step. The resolver
does not broaden or replace that approval.

## The two configuration sources

Routing intentionally combines two sources with different jobs.

### PostgreSQL: tenant and user decisions

`InferenceRoutingConfigReader` reads:

- the tenant's current status and provider allow-list;
- the exact entitlement authorization approved;
- tenant/user-specific endpoint and secret-reference information.

These records can change while the process is running, so routing reads their
current values for every resolution.

### Startup catalog: application-supported providers and models

`ProviderConfigCatalog` supplies validated provider and model configuration
loaded from YAML during startup. It describes:

- provider implementation and endpoint rules;
- authentication method;
- default headers, timeouts, and generation settings;
- available models, capabilities, and limits.

The database says what a tenant or user is configured to use. The catalog says
what this application build knows how to execute. Both must agree.

## What routing returns

`ResolvedRoute` is the complete handoff to provider execution. Important fields
include:

- provider, model, endpoint, and optional cloud region;
- a secret reference, never a plaintext secret;
- effective timeout, temperature, token limit, and headers;
- tenant-specific extra configuration;
- a quota key;
- a route fingerprint used by the provider cache.

The route is immutable. Downstream code consumes it but does not make another
routing or authorization choice.

## Why the route fingerprint matters

Provider instances are cached so the application does not rebuild a client for
every request. The route fingerprint is the cache identity.

It is a SHA-256 hash of the routing inputs, including:

- the entitlement;
- deployment key;
- provider configuration;
- selected model specification.

Consequently, changing a header, timeout, endpoint, model limit, entitlement,
or deployment key creates a different fingerprint. This prevents a future
catalog reload from accidentally reusing a provider client built from stale
configuration. The hash is used for comparison and cache keys; it is not an
authorization decision.

## Failure meanings and ownership

The HTTP status tells operators whether the caller can correct the problem.

| Failure | Meaning | Who can fix it? | HTTP result |
| --- | --- | --- | --- |
| Tenant missing | The requested tenant no longer exists. | Caller or administrator | `404` |
| Tenant suspended | Current tenant policy blocks inference. | Administrator | `403` |
| Authorized entitlement unavailable | The exact approved grant was deleted, revoked, or became inactive. | Administrator; caller must be authorized again | `403` |
| Provider not allowed | The tenant's provider allow-list changed. | Administrator | `403` |
| Unsupported operation | The configured model cannot perform this endpoint's operation. | Caller or administrator can choose compatible configuration | `422` |
| Provider/model missing from catalog | PostgreSQL refers to configuration this application did not load. Error code: `ROUTING_CATALOG_DRIFT`. | Operator must repair database/YAML drift | `500` |
| Resolver returned a different entitlement | An internal adapter violated the authorization contract. | Developer/operator | `500` |

Catalog drift is deliberately a server error. The caller did not select the
provider or model stored in the entitlement and cannot repair the catalog.
Reporting `422` would incorrectly blame the request and could hide the problem
from server-error monitoring.

## File map for junior developers

Read this package in the following order:

1. [`models.py`](models.py) — the `ResolutionRequest` input and `ResolvedRoute`
   output contracts.
2. [`contracts.py`](contracts.py) — the two read-only interfaces injected into
   the resolver.
3. [`route_resolution.py`](route_resolution.py) — the ordered policy checks and
   orchestration.
4. [`route_builder.py`](route_builder.py) — construction of the immutable route
   and its fingerprint.
5. [`exceptions.py`](exceptions.py) — named failures raised by routing.
6. [`../adapters/inference_routing/postgres_config_reader.py`](../adapters/inference_routing/postgres_config_reader.py)
   — the PostgreSQL implementation of the read contracts.

The main entry point is `InferenceRouteResolver.resolve_route()`. A useful way
to trace the code is to start there, follow each validation method in order,
and then continue into `build_entitlement_route()`.

## Rules to preserve when changing this package

1. Never search for or substitute a different entitlement after authorization.
2. Re-read security-sensitive tenant and entitlement state before execution.
3. Treat database/catalog disagreement as an operator-visible server failure,
   not as a client validation error.
4. Include every provider-cached route input in the route fingerprint.
5. Pass secret references through routing, never plaintext credentials.
6. Add a typed exception and an intentional HTTP mapping for every new failure
   category.
