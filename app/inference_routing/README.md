# Inference routing

Inference routing runs **after authorization**. Its job is not to decide which
credential a user may use. Authorization already made that security decision
and supplied an exact `entitlement_id`.

```text
HTTP request
    -> authorization validates tenant + membership + deployment + entitlement
    -> ResolutionRequest carries the approved entitlement_id
    -> resolver re-reads that exact active entitlement from PostgreSQL
    -> resolver checks current tenant policy and provider/model capability
    -> route builder returns one immutable ResolvedRoute
    -> provider execution
```

## Why the entitlement id is required

Imagine authorization approves entitlement A. If routing later searched all
records and selected entitlement B, execution would use a credential that was
never authorized. Requiring the identifier makes that impossible. If A is
revoked between the two steps, routing returns
`AUTHORIZED_ENTITLEMENT_UNAVAILABLE` and fails closed.

There is deliberately no fallback. A deleted, revoked, or malformed grant must
be authorized again; silently choosing a deployment credential would turn a
security change into unintended access.

## The two input sources

`InferenceRoutingConfigReader` reads current tenant policy and the exact
entitlement from PostgreSQL. These security-sensitive reads are not cached, so
suspension and revocation apply to the next request.

`ProviderConfigCatalog` supplies provider and model metadata loaded and
validated during application startup. It answers whether the provider exists,
the model exists, and the model supports chat, embedding, or reranking.

## Example

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

`ResolvedRoute` is the handoff to execution. It contains the endpoint, secret
reference (never plaintext secret), effective defaults, quota key, and a stable
fingerprint. Downstream code consumes this answer and makes no routing choices.

## Failure meanings

- Missing or suspended tenant: current tenant policy no longer allows traffic.
- Unavailable authorized entitlement: the exact grant disappeared or was revoked.
- Provider forbidden: the tenant allow-list changed after authorization.
- Unknown provider/model: runtime data disagrees with the startup catalog.
- Unsupported operation: the selected model cannot perform the requested task.

For storage details, continue with
`app/adapters/inference_routing/postgres_config_reader.py`.
