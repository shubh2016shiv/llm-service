# Provider execution: a working reference

This guide follows one authorized request from `ResolvedRoute` to an external
LLM. It explains where each decision belongs and, equally important, where it
does **not** belong.

## 1. The package boundary

```text
InferenceService
      |
      v
ProviderRegistry -----> SecretStore
      |                TransportFactory
      |                CircuitBreakerRegistry
      v
BaseProvider
      |
      +---- direct/OpenAI, Anthropic, vLLM
      '---- cloud/Azure OpenAI, Bedrock
```

Routing has already selected a provider, model, endpoint, credential reference,
timeout, temperature, and token limit. Provider adapters translate those facts
into a vendor request. They must not re-run authorization or choose a different
route.

## 2. Why provider instances are cached briefly

Building an adapter may read a secret and obtain shared transport/breaker
objects. Repeating that work on every request is wasteful, so
`ProviderRegistry` caches by `route_fingerprint`.

The cache is deliberately not permanent:

- `PROVIDER_CACHE_TTL_SECONDS` limits how long a plaintext credential remains
  inside a provider object. After expiry, the secret store is read again, so a
  value rotated at the same reference becomes visible.
- `PROVIDER_CACHE_MAX_ENTRIES` bounds per-worker memory when many entitlements
  create many fingerprints.
- least-recently-used eviction keeps hot routes and retires cold routes.
- concurrent misses for the same fingerprint share one build task; misses for
  different fingerprints may build concurrently.
- cancellation of one HTTP request does not cancel a build another request is
  awaiting.

REST providers borrow one shared `httpx.AsyncClient` from the transport
factory. Provider eviction therefore does not close the transport; application
shutdown closes the factory once.

## 3. Provider classes are an allow-list, not plugins

`ProviderStaticConfig.implementation_class` is a `ProviderImplementation`
enum. YAML may select one audited adapter, but it cannot name an arbitrary
module for `importlib` to execute. A typo or unknown class fails while startup
configuration is validated, before traffic is accepted.

Adding a provider therefore requires an intentional code change:

1. Implement a concrete `BaseProvider` subclass.
2. Add its path to `ProviderImplementation`.
3. Add the class to `_BUILT_IN_PROVIDER_CLASSES` in `registry.py`.
4. Add provider YAML and contract tests.

That friction is a security boundary, not boilerplate.

## 4. Credential rules

The route carries `secret_reference`, never plaintext. The registry materializes
the secret only while constructing an adapter and wraps it in `SecretStr`.

Authentication modes behave differently:

- bearer, API-key-header, and OAuth modes read the configured secret store;
- AWS SigV4 uses the AWS SDK credential chain and does not perform an API-key
  lookup;
- `none` is for explicitly unauthenticated/private endpoints and also skips the
  secret store.

Adapters reveal a secret only when building an outbound authentication header.
They never log it or include it in response objects.

## 5. The shared execution template

The service calls one of these public methods:

```python
response = await provider.generate(chat_request)
response = await provider.embed(embed_request)
response = await provider.rerank(rerank_request)

async for chunk in provider.stream_generate(chat_request):
    ...
```

The public methods live on `BaseProvider`. They run the concrete `_generate`,
`_embed`, or `_rerank` method through the circuit breaker. A concrete adapter
only owns vendor details: URL, headers, payload, and response parsing.

This template gives every integration the same failure behavior. Raw
`httpx`, SDK, JSON, and parsing exceptions are normalized even if a concrete
adapter forgets a local `except` block.

## 6. Streaming is different

An async generator does not execute when it is created; it executes as the
caller requests chunks. A coroutine-oriented circuit breaker cannot guard it
directly.

`CircuitBreakerStream` solves that mismatch:

```text
provider async generator
        |
        v
guarded producer task
        |
        v
one-item queue  <---- backpressure
        |
        v
HTTP/SSE consumer
```

The queue has capacity one. If the client is slow, the producer stops reading
upstream instead of buffering an unbounded response. If the client disconnects,
the producer is cancelled and awaited. Errors cross the queue as typed control
messages and emerge as normal domain exceptions.

## 7. Failure normalization

`http_errors.py` is the anti-corruption boundary:

| Upstream condition | Domain result |
|---|---|
| connect/read timeout | `ProviderTimeoutError` |
| 401 | invalid or expired credential |
| 429 | request/token rate-limit error |
| 400 | safe invalid-request error |
| 5xx / connection failure | provider unavailable/down |
| unknown SDK/parser failure | `ProviderInternalError` |

Upstream response bodies and raw exception strings are not copied into error
metadata. They can contain prompts, URLs with credentials, or SDK diagnostics
that should not become API responses or logs. Only safe classifications and the
exception class name survive.

`Retry-After` currently accepts positive delta-seconds. HTTP-date values safely
degrade to no hint instead of raising a second exception while handling the
first.

## 8. Timeouts and retries

Every REST call supplies `ResolvedRoute.effective_timeout_seconds` to `httpx`.
Bedrock creates a botocore `Config` with the same connect/read timeout and with
SDK retries disabled.

Hidden SDK retries are disabled because chat generation is not automatically
safe to repeat: a provider may have accepted work before the connection failed.
Retry policy should be operation-aware and use provider idempotency support,
not silently replay every request inside the transport.

## 9. Request defaults

Routing resolves effective temperature and token limits from the validated
catalog. Concrete adapters apply request overrides when present and otherwise
send those effective values. Use an explicit `is not None` check: temperature
`0.0` is a valid deterministic setting and must not be mistaken for “missing.”

## 10. Bedrock specifics

Bedrock uses an `aioboto3.Session`, AWS credential resolution, and a short-lived
client context per call. Runtime operations use `bedrock-runtime`; the health
probe uses the `bedrock` control-plane client because `list_foundation_models`
does not exist on the runtime client.

Streaming and normal calls receive the same botocore timeout. The embedding
body is read asynchronously. Titan's single `embedding` vector and Cohere's
`embeddings` collection are normalized to the internal `list[list[float]]`
contract.

## 11. Health checks

Health responses expose availability and latency. Failure detail contains only
the exception type, not `str(exception)`, because exception text may contain a
request URL, account identifier, or credential-bearing diagnostic.

## 12. Where to make a change

- cache lifetime or construction coalescing: `registry.py`
- circuit behavior for normal calls: `base_provider.py`
- stream cancellation/backpressure: `circuit_breaker_stream.py`
- transport/SDK error mapping: `http_errors.py`
- vendor URL/payload/parser: that concrete provider module
- connection pool ownership: `app/adapters/provider_transport`
- provider/model defaults: `config/providers/*.yaml`

The quickest debugging path is route -> registry -> base wrapper -> concrete
adapter -> error classifier. Following that order keeps security, lifecycle,
and vendor concerns separate.
