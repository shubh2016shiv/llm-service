# Provider Layer — `app/providers/`

> **Who this is for**: Anyone who needs to understand how the application translates a fully
> resolved routing decision into an actual call to OpenAI, Anthropic, Azure OpenAI, AWS
> Bedrock, or a self-hosted vLLM instance. You do not need prior knowledge — this document
> starts from zero, explains every design decision, and traces the full path from HTTP request
> to AI provider response and back.

---

## Table of Contents

1. [Why this layer exists](#1-why-this-layer-exists)
2. [The mental model in one paragraph](#2-the-mental-model-in-one-paragraph)
3. [Where this package fits — upstream and downstream](#3-where-this-package-fits--upstream-and-downstream)
4. [Startup wiring](#4-startup-wiring)
5. [Upstream: how InferenceService hands off to providers](#5-upstream-how-inferenceservice-hands-off-to-providers)
6. [ProviderRegistry — the fingerprint cache](#6-providerregistry--the-fingerprint-cache)
7. [Provider construction: the four-step build](#7-provider-construction-the-four-step-build)
8. [BaseProvider — the shared contract and resilience boundary](#8-baseprovider--the-shared-contract-and-resilience-boundary)
9. [Streaming architecture — the producer-queue-consumer pattern](#9-streaming-architecture--the-producer-queue-consumer-pattern)
10. [Provider adapters — the wire-format translators](#10-provider-adapters--the-wire-format-translators)
11. [Authentication differences across providers](#11-authentication-differences-across-providers)
12. [Provider capability matrix](#12-provider-capability-matrix)
13. [Error classification — `http_errors.py`](#13-error-classification--http_errorspy)
14. [Downstream: normalized responses back to the service layer](#14-downstream-normalized-responses-back-to-the-service-layer)
15. [Package structure](#15-package-structure)
16. [How to add a new provider](#16-how-to-add-a-new-provider)
17. [How to debug a provider failure](#17-how-to-debug-a-provider-failure)
18. [Common gotchas](#18-common-gotchas)

---

## 1. Why this layer exists

The `app/providers` folder is one of the hardest parts of the codebase to understand at first
glance. It deals with:

- external LLM APIs with incompatible request/response shapes,
- different authentication styles (Bearer token, API key header, AWS SigV4 IAM),
- circuit breaker behavior to stop cascading failures,
- streaming responses that arrive as chunks over time,
- error translation from SDK/transport exceptions to domain errors,
- and provider object reuse across requests.

That is a lot to hold in your head at once.

**The reason for the complexity**: External providers are inconsistent, failure-prone, and
operationally important. The purpose of this package is to absorb that complexity once so the
rest of the codebase can stay clean.

**What the rest of the application needs to know**: Zero details about any specific provider.
It calls `provider.generate(request)` and gets back a `ChatResponse`. It does not know or
care whether the call went to OpenAI via HTTP or AWS Bedrock via SDK.

**Why not call OpenAI or Anthropic directly from the service layer?**

If the service layer knew that:
- OpenAI wants `Authorization: Bearer ...`
- Anthropic wants `x-api-key` plus a required `anthropic-version` header
- Azure OpenAI routes through deployment names and adds `?api-version=...`
- Bedrock uses the AWS SDK instead of httpx
- vLLM is OpenAI-like but not exactly identical

...then every new provider would force changes in many places. That creates coupling,
duplication, and bugs. The provider layer keeps those details in exactly one place per
provider.

---

## 2. The mental model in one paragraph

Think of this package as a **translation bureau**. Each external LLM system speaks its own
language — its own URL patterns, its own JSON shapes, its own auth conventions, its own
streaming format. The `app/inference_routing` layer decided WHERE the request should go.
This package decides HOW to speak to that destination. It translates the generic internal
request format into the provider-specific wire format, fires the actual network call,
translates the response back into a normalized internal format, and reports errors using
internal domain error types so upstream layers never need to know whether the failure was
an httpx timeout or a boto3 `ClientError`.

---

## 3. Where this package fits — upstream and downstream

```
┌──────────────────────────────────────────────────────────────────────┐
│ UPSTREAM: app/services/InferenceService                              │
│                                                                      │
│ Receives ResolvedExecutionContext from inference_routing pipeline.   │
│ Checks quota with TokenManagerClient.                                │
│ Calls ProviderRegistry.get_provider(context).                        │
│ Calls provider.generate(request) / embed / rerank / stream_generate. │
│ Reports token usage back to TokenManagerClient.                      │
└───────────────────────────────┬──────────────────────────────────────┘
                                │  context.route_fingerprint (cache key)
                                │  context.secret_reference (credential pointer)
                                │  context.provider_name, model_name, etc.
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│ THIS PACKAGE: app/providers/                                         │
│                                                                      │
│ ProviderRegistry                                                     │
│   → Checks fingerprint cache (fast path, no lock)                   │
│   → On miss: builds provider (dynamic import + transport +           │
│               circuit breaker + plaintext secret)                    │
│   → Returns cached/new BaseProvider subclass instance               │
│                                                                      │
│ BaseProvider (abstract contract + resilience boundary)               │
│   → generate / embed / rerank / stream_generate                      │
│   → All calls wrapped by circuit breaker                             │
│   → Shared structured logging                                        │
│   → Shared error classification (http_errors.py)                    │
│                                                                      │
│ Concrete Provider Adapters (one per external system):                │
│   direct/openai_provider.py   → OpenAI REST API                     │
│   direct/anthropic_provider.py → Anthropic Messages API             │
│   direct/vllm_provider.py     → Self-hosted vLLM (OpenAI-compat.)   │
│   cloud/azure_openai_provider.py → Azure OpenAI Service             │
│   cloud/bedrock_provider.py   → AWS Bedrock (aioboto3 SDK)          │
└───────────────────────────────┬──────────────────────────────────────┘
                                │  HTTP/SDK call to external LLM
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│ DOWNSTREAM: External AI Provider                                     │
│                                                                      │
│ OpenAI API     POST /v1/chat/completions                             │
│ Anthropic API  POST /messages                                        │
│ Azure OpenAI   POST /openai/deployments/{name}/chat/completions      │
│ AWS Bedrock    SDK client.converse() / client.invoke_model()         │
│ vLLM           POST /v1/chat/completions  (OpenAI-compatible)        │
│                                                                      │
│ Returns: JSON response / SSE stream / SDK response object            │
└───────────────────────────────┬──────────────────────────────────────┘
                                │  raw JSON / chunks / SDK objects
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│ NORMALIZED RESPONSE (back up to service layer)                       │
│                                                                      │
│ ChatResponse   — content, role, finish_reason, usage, raw_response   │
│ EmbedResponse  — embeddings (list of float vectors), model, usage    │
│ RerankResponse — ranked documents with scores                        │
│ ChatStreamChunk — content fragment, finish_reason, index, raw_chunk  │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 4. Startup wiring

`ProviderRegistry` is built **once** at application startup in `main.py`'s lifespan handler
and stored on `app.state`. All requests share the same registry instance — this is what makes
provider caching possible.

```python
# From main.py lifespan():

http_client_factory = HTTPClientFactory(pool_config=global_config.http_pool)
# Manages shared connection pools for httpx (REST providers) and aioboto3 (Bedrock)

secret_store = VaultSecretStore(...)  # or EnvironmentSecretStore() in dev
# Fetches plaintext API keys at provider-build time only

provider_registry = ProviderRegistry(
    http_client_factory=http_client_factory,
    cache=redis_cache,        # for per-provider circuit breaker state
    secret_store=secret_store,
)

inference_service = InferenceService(
    token_manager_client=token_manager_client,
    provider_registry=provider_registry,
)
app.state.inference_service = inference_service
```

The registry receives three dependencies injected at startup:

| Dependency | What it provides |
|---|---|
| `HTTPClientFactory` | Creates transport clients (httpx for REST providers, aioboto3 session for Bedrock) from a shared connection pool |
| `RedisCache` | Backs the per-provider circuit breaker state — failure counts and breaker state survive restarts |
| `SecretStore` | Fetches plaintext API keys from Vault (prod) or env vars (dev) — called only when building a new provider instance |

---

## 5. Upstream: how InferenceService hands off to providers

`InferenceService` receives a `ResolvedExecutionContext` from the routing pipeline. Here is
the exact call sequence for a non-streaming chat request:

```python
# app/services/inference.py — InferenceService.execute_chat()

async def execute_chat(self, context: ResolvedExecutionContext, request: ChatRequest):

    # Step 1: quota check BEFORE touching the provider
    await self._token_manager.check_quota(
        context.tenant_config.tenant_id,
        context.quota_key,   # "gpt4-prod" (deployment) or entitlement UUID (user)
        request,
    )

    # Step 2: get the provider adapter (cached by fingerprint)
    provider = await self._registry.get_provider(context)
    #           ↑ This is the handoff to app/providers

    # Step 3: call the provider's public method
    response = await provider.generate(request)
    #          ↑ BaseProvider.generate() wraps _generate() in circuit breaker

    # Step 4: report actual token usage
    if response.usage:
        await self._token_manager.report_usage(
            context.tenant_config.tenant_id,
            context.quota_key,
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
        )

    return response
```

**Key principle**: `InferenceService` passes `context` to `get_provider()` and `request` to
`provider.generate()`. It never knows which provider class was selected, which URL was called,
or how the response was parsed. That is entirely the provider layer's concern.

---

## 6. ProviderRegistry — the fingerprint cache

### 6.1 The cache key: route fingerprint

The `route_fingerprint` is a SHA-256 hash computed by `ResolvedExecutionContextFactory` in the
routing pipeline. It uniquely encodes:

```
provider_name + model_name + api_endpoint_url + cloud_region +
credential_scope + secret_reference + tenant_id + deployment_key (or entitlement_id)
```

Two requests that resolve to identical route parameters produce the same fingerprint and
therefore reuse the same provider instance. If any route-defining value changes (new secret,
updated endpoint, different model), the fingerprint changes and a new provider is built.

### 6.2 Fast path vs slow path

```python
async def get_provider(self, context: ResolvedExecutionContext) -> BaseProvider:
    cache_key = context.route_fingerprint

    # Fast path — no lock, no await, dict read is GIL-safe in CPython
    if cache_key in self._providers:
        return self._providers[cache_key]

    # Slow path — acquire lock, double-check, build
    async with self._lock:
        if cache_key in self._providers:   # second check after acquiring lock
            return self._providers[cache_key]
        provider = await self._build_provider(context)
        self._providers[cache_key] = provider
        return provider
```

**Why double-checked locking?** Under concurrent traffic, many requests for the same new route
could all miss the cache simultaneously. Without the second check, all of them would build a
provider in parallel — wasting work, wasting secret fetches, and creating duplicate instances.
The second check inside the lock ensures only one request does the build; the others wait and
then hit the now-populated cache.

**Why no lock on the fast path?** Python's GIL makes plain dict reads safe without a lock in
CPython. The fast path covers the overwhelming majority of requests in production (every repeat
request to an existing route). Avoiding a lock here keeps the hot path free of contention.

### 6.3 Cache invalidation

```python
async def invalidate(self, route_fingerprint: str) -> None:
    async with self._lock:
        self._providers.pop(route_fingerprint, None)
```

Called when configuration or credential changes propagate via Redis pub/sub events. The next
request for that route will rebuild the provider from scratch, fetching the latest secret from
the store.

---

## 7. Provider construction: the four-step build

When the registry needs to build a new provider instance, `_build_provider()` runs four steps:

```python
async def _build_provider(self, context: ResolvedExecutionContext) -> BaseProvider:

    # Step 1: Dynamic class import from YAML-configured implementation_class
    provider_class = self._resolve_implementation_class(
        context.provider_static_config.implementation_class
    )
    # e.g. "app.providers.direct.openai_provider.OpenAIProvider"
    # → importlib.import_module("app.providers.direct.openai_provider")
    # → getattr(module, "OpenAIProvider")
    # → returns the class object, not an instance

    # Step 2: Transport client from shared pool
    http_client = self._http_client_factory.create_client(
        context.provider_static_config.provider_type
    )
    # provider_type "http" → httpx.AsyncClient with connection pool
    # provider_type "bedrock" → aioboto3 Session object

    # Step 3: Circuit breaker from Redis-backed state
    circuit_breaker = await get_provider_circuit_breaker(
        context.provider_name, self._cache
    )
    # Returns aiobreaker.CircuitBreaker keyed by provider_name
    # Failure state is stored in Redis so it persists across restarts

    # Step 4: Fetch plaintext API key — ONLY moment it exists in memory
    plaintext_api_key = await self._secret_store.get_secret(
        context.secret_reference,              # e.g. "ACME_OPENAI_KEY"
        tenant_id=str(context.tenant_config.tenant_id),
    )
    # VaultSecretStore → GET /v1/secret/data/llm-provider-service/ACME_OPENAI_KEY
    # EnvironmentSecretStore → os.environ["ACME_OPENAI_KEY"]

    # Construct the provider, wrapping the key in SecretStr immediately
    return provider_class(
        context=context,
        http_client=http_client,
        circuit_breaker=circuit_breaker,
        api_key=SecretStr(plaintext_api_key),   # plaintext wrapped, never logged
    )
```

### Step 1 detail: `implementation_class` from YAML

Each provider's static YAML config declares which Python class to use:

```yaml
# config/providers/openai.yaml (example)
provider_name: openai
provider_type: http
implementation_class: app.providers.direct.openai_provider.OpenAIProvider
default_timeout_seconds: 30.0
default_max_retries: 3
default_temperature: 0.7
```

This is how adding a new provider requires only a new YAML file and a new Python class — no
changes to the registry or any other module. The registry dynamically imports whatever class
the YAML names.

### Step 2 detail: transport client types

| `provider_type` | Transport | Used by |
|---|---|---|
| `http` | `httpx.AsyncClient` (shared pool) | OpenAI, Anthropic, Azure OpenAI, vLLM |
| `bedrock` | `aioboto3.Session` | AWS Bedrock |

`HTTPClientFactory` maintains shared connection pools. All requests to the same provider type
reuse the same underlying TCP/TLS connections, which is a significant latency and memory
saving compared to creating a new HTTP client per request.

### Step 3 detail: circuit breaker backed by Redis

```
Provider name → Redis key → aiobreaker.CircuitBreaker instance
```

The circuit breaker tracks failure counts in Redis. This means:
- Failure counts persist across process restarts and rolling deploys.
- All application instances share the same breaker state — one failing instance opens the
  breaker for the whole fleet.
- The breaker state (closed / open / half-open) is consistent across all pods.

### Step 4 detail: the plaintext secret boundary

The routing layer (`app/inference_routing`) never fetches or holds the plaintext API key. It
only carries `secret_reference` — an opaque string like `"ACME_OPENAI_KEY"`. The plaintext
key is fetched here, in step 4 of `_build_provider()`, and immediately wrapped in Pydantic's
`SecretStr`. `SecretStr` masks the value in `repr()` and `str()` output, so the key never
appears in logs, stack traces, or debugger output.

After `_build_provider()` returns, the plaintext key exists only in the `SecretStr` object
stored on the provider instance, read only at auth header construction time via
`self._api_key.get_secret_value()`.

---

## 8. BaseProvider — the shared contract and resilience boundary

### 8.1 What it is

`BaseProvider[TransportT]` is an abstract generic class that every provider adapter extends.
The `[TransportT]` generic parameter is filled in by each concrete subclass:

```python
class OpenAIProvider(BaseProvider[httpx.AsyncClient]):   # TransportT = httpx.AsyncClient
class BedrockProvider(BaseProvider[object]):              # TransportT = object (aioboto3 session)
```

This lets the type checker verify that `self._http_client` is used correctly in each
subclass without forcing all providers to share the same transport type.

### 8.2 Constructor fields

Every provider instance holds these immutable fields:

| Field | Type | Set from | Purpose |
|---|---|---|---|
| `_context` | `ResolvedExecutionContext` | routing pipeline | endpoint URL, model name, extra_headers, effective parameters |
| `_static` | `ProviderStaticConfig` | `context.provider_static_config` | default timeout, auth config, provider name |
| `_http_client` | `TransportT` | `HTTPClientFactory` | outbound transport (httpx or aioboto3) |
| `_circuit_breaker` | `aiobreaker.CircuitBreaker` | Redis-backed factory | resilience guard |
| `_api_key` | `SecretStr` | `SecretStore` at build time | plaintext credential, never logged |

**Critical rule**: No per-request mutable state on the instance. All requests to the same
provider instance share these fields. If a field changed per-request, concurrent requests
would corrupt each other's state.

### 8.3 Public methods and the circuit breaker

The public interface has five methods. Four delegate through the circuit breaker:

```
generate(request)        → _call_with_breaker(_generate, request)
embed(request)           → _call_with_breaker(_embed, request)
rerank(request)          → _call_with_breaker(_rerank, request)
stream_generate(request) → producer-queue pattern (see Section 9)
health_check()           → NOT wrapped by breaker (diagnostic only)
```

`_call_with_breaker()` is the generic circuit breaker wrapper:

```python
async def _call_with_breaker[ResponseT](self, func, *args) -> ResponseT:
    return cast("ResponseT", await self._circuit_breaker.call_async(func, *args))
```

**Why `cast()` is needed**: `aiobreaker`'s type stubs declare `call_async` with no return
type. Without `cast()`, the type checker would lose track of the return type and infer `Any`,
silently disabling type checking for everything downstream. `cast()` is a runtime no-op — it
returns its argument unchanged. Its only purpose is to restore type information at this
boundary.

### 8.4 Circuit breaker state machine

The circuit breaker has three states:

```
           too many failures
CLOSED ─────────────────────────► OPEN
  ▲                                 │
  │                                 │ timeout expires
  │                                 ▼
  │                           HALF-OPEN
  │      trial call succeeds        │
  └─────────────────────────────────┘
         trial call fails → back to OPEN
```

| State | Behavior | Transition |
|---|---|---|
| **CLOSED** (normal) | All requests pass through | Opens after N failures within a time window |
| **OPEN** (tripped) | All requests fail immediately without calling the provider | Moves to HALF-OPEN after a cooldown period |
| **HALF-OPEN** (testing) | One trial request is allowed through | Success → CLOSED; failure → OPEN again |

**Why the breaker is in `BaseProvider`**: If each subclass managed its own breaker, the policy
could drift. One provider might wrap `generate` but forget `embed`. Another might handle
streaming differently. Centralizing the breaker in the base class guarantees uniform resilience
behavior across all providers and all operations.

**Plain example of why it matters**: Imagine OpenAI is timing out for 30 seconds.

Without a breaker:
- Request 1 waits 30s and fails, request 2 waits 30s and fails, hundreds pile up.
- The event loop is saturated with pending timeouts. Memory climbs. Latency degrades for all
  tenants, even those using different providers.

With a breaker:
- After N failures, the breaker opens.
- Subsequent requests fail immediately (fast-fail) instead of waiting 30 seconds each.
- The system stays responsive for other providers and for this provider once it recovers.

### 8.5 Shared helpers

**`_build_auth_headers()`**: Reads `self._static.auth.header_name` and
`self._static.auth.header_prefix` from the provider YAML. Default behavior produces
`Authorization: Bearer <key>`. Subclasses that use a different scheme (Anthropic, Azure)
override this method instead of duplicating the logic.

**`_emit_structured_log()`**: Emits one structured log record per provider call with fields:
`provider_name`, `model_name`, `operation`, `latency_ms`, `status_code`, `retry_count`,
`usage`, `error_type`. All providers produce the same log shape — dashboards and alert
queries work identically across OpenAI, Bedrock, Anthropic, etc.

**`_handle_provider_error(exc)`**: Delegates to `http_errors.classify_error()`. This is the
anti-corruption boundary: raw httpx or botocore exceptions are converted into canonical domain
errors before propagating upward.

**`_effective_timeout()`**: Returns `self._context.effective_timeout_seconds`, which was
already resolved by the context factory from `(deployment_config.timeout_seconds OR
provider_static_config.default_timeout_seconds)`. Providers call this once per request to
pass the correct timeout to their HTTP client.

---

## 9. Streaming architecture — the producer-queue-consumer pattern

Streaming is the hardest part of this layer. Understanding why the queue pattern was chosen
requires understanding the constraint it solves.

### 9.1 The constraint: circuit breakers guard coroutines, not async generators

`aiobreaker.CircuitBreaker.call_async()` expects a coroutine (an `async def` function that
returns a value). It does NOT support async generators (functions that `yield` values over
time).

If we tried to pass `_stream_generate` directly to the breaker, the breaker could not count
a mid-stream failure as a "failure event" because the generator never raises at the start —
it raises partway through, after the breaker has already considered the call "successful."

### 9.2 The solution: producer task + bounded queue + consumer generator

`BaseProvider.stream_generate()` bridges the gap with this design:

```
                    ┌─────────────────────────────────────────────┐
                    │ asyncio.Queue(maxsize=1)                     │
                    │                                             │
 _run_guarded_      │  ChatStreamChunk   → put → get → yield     │
 stream() task      │  _StreamError      → put → get → raise     │
 (producer)         │  _STREAM_COMPLETE  → put → get → break     │
                    │                                             │
                    └─────────────────────────────────────────────┘
```

Here is the actual code and what each part does:

```python
async def stream_generate(self, request: ChatRequest) -> AsyncIterator[ChatStreamChunk]:

    # Bounded queue with maxsize=1 provides backpressure:
    # the producer cannot get more than one chunk ahead of the consumer.
    queue: asyncio.Queue[ChatStreamChunk | _StreamError | _StreamComplete] = asyncio.Queue(
        maxsize=1
    )

    # _consume_stream: the async generator that reads from the provider.
    # This is what the circuit breaker will guard.
    async def _consume_stream() -> None:
        async for chunk in self._stream_generate(request):
            await queue.put(chunk)
    #                           ↑ blocks if consumer hasn't read the last chunk yet

    # _run_guarded_stream: the coroutine that wraps _consume_stream in the breaker.
    # It catches all exceptions and converts them to queue signals.
    async def _run_guarded_stream() -> None:
        try:
            await self._circuit_breaker.call_async(_consume_stream)
            # ↑ The breaker guards _consume_stream (a coroutine, not a generator).
            # Any exception raised inside _consume_stream counts as a breaker failure.
        except asyncio.CancelledError:
            raise                              # propagate cancellation, don't swallow
        except Exception as exc:
            await queue.put(_StreamError(exc)) # signal failure to consumer
        else:
            await queue.put(_STREAM_COMPLETE)  # signal clean completion to consumer

    # Launch the producer as a background task (runs concurrently with consumer)
    producer = asyncio.create_task(_run_guarded_stream())

    # Consumer: pulls items from the queue and yields to the caller
    try:
        while True:
            item = await queue.get()
            if isinstance(item, _StreamComplete):
                break                          # clean end of stream
            if isinstance(item, _StreamError):
                raise item.exception           # surface the error to the caller
            yield item                         # real chunk — forward to SSE layer
    finally:
        # If the caller stops reading early (client disconnect), cancel the producer
        if not producer.done():
            producer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await producer

    await producer  # propagate any unhandled producer exception
```

### 9.3 The three queue signal types

The queue must carry three completely different things. Three distinct types are used instead
of `None` or strings because:

- `None` is a valid Python value; the type checker cannot distinguish "deliberate sentinel
  None" from "accidental None".
- A plain string like `"DONE"` cannot be used with `isinstance()` to discriminate the union.
  After a string equality check, the type checker still thinks the variable could be a chunk.

```python
@dataclass(frozen=True)
class _StreamError:
    exception: Exception     # carries the real exception across the queue boundary

class _StreamComplete:       # pure signal, no data — a class body of `pass` is correct
    pass

_STREAM_COMPLETE = _StreamComplete()  # singleton reused every time a stream finishes
```

The consumer does:
```python
if isinstance(item, _StreamComplete): break        # type checker knows: end of stream
if isinstance(item, _StreamError):    raise item.exception  # type checker knows: error
yield item                            # type checker knows: ChatStreamChunk (only option left)
```

### 9.4 Why `maxsize=1` on the queue

The queue is bounded to a single slot. This creates backpressure: the producer cannot put the
next chunk until the consumer has taken the previous one. This prevents the producer from
buffering the entire response in memory if the client is slow to consume the stream.
For most LLM responses (thousands of small tokens), this is the right default.

### 9.5 Client disconnect handling

If the caller stops iterating (e.g., the HTTP client disconnected), the `finally` block
cancels the producer task. Without this, the producer would continue reading chunks from the
external provider and buffering them into the queue indefinitely, wasting network I/O and
memory on a connection nobody is reading.

---

## 10. Provider adapters — the wire-format translators

Each concrete adapter is responsible for exactly four things:

1. **Build headers**: Provider-specific authentication headers + `Content-Type` + extra_headers
   from the execution context.
2. **Build payload**: Translate the internal `ChatRequest`/`EmbedRequest`/`RerankRequest` into
   the provider's expected JSON body or SDK parameters.
3. **Make the call**: POST to the provider endpoint or invoke the SDK method.
4. **Parse response**: Translate the provider's raw JSON or SDK response back into internal
   schema objects (`ChatResponse`, `EmbedResponse`, etc.).

Every adapter follows the same pattern:

```python
async def _generate(self, request: ChatRequest) -> ChatResponse:
    headers = self._build_request_headers()    # step 1
    payload = self._build_chat_payload(request) # step 2
    t0 = time.monotonic()
    try:
        response = await self._http_client.post(url, headers=headers, json=payload, timeout=...)
        response.raise_for_status()            # step 3
        latency_ms = int((time.monotonic() - t0) * 1000)
        data = response.json()
        self._emit_structured_log(...)         # shared telemetry
        return self._parse_chat_response(data) # step 4
    except httpx.HTTPStatusError as exc:
        raise self._handle_provider_error(exc) from exc  # anti-corruption layer
```

### 10.1 OpenAI (`direct/openai_provider.py`)

**Endpoint**: `{api_endpoint_url}/chat/completions` (POST)

**Chat payload fields**:
- `model`: `context.model_name`
- `messages`: list of `{role, content}` dicts
- `temperature`, `max_tokens`, `top_p`, `stop`: included only if present on the request

**Streaming**: `payload["stream"] = True`, then reads `data:` lines from SSE response.
`[DONE]` line signals end of stream.

**Embed endpoint**: `{api_endpoint_url}/embeddings`

**Rerank**: Not supported by OpenAI. Raises `ProviderError` immediately.

**Usage field mapping**: `prompt_tokens`, `completion_tokens`, `total_tokens`
(OpenAI native field names match our internal `Usage` model exactly).

---

### 10.2 Anthropic (`direct/anthropic_provider.py`)

**Endpoint**: `{api_endpoint_url}/messages` (POST)

**Critical difference — system prompt handling**: Anthropic separates system instructions
from conversation turns. The adapter splits the message list:

```python
system_prompts = [m for m in request.messages if m.role == "system"]
messages = [m.model_dump() for m in request.messages if m.role != "system"]

payload["messages"] = messages
if system_prompts:
    payload["system"] = "\n".join(m.content for m in system_prompts)
```

**`max_tokens` is required** by Anthropic (OpenAI treats it as optional). The adapter always
sets it: `request.max_tokens or context.effective_max_tokens`.

**Streaming events**: Anthropic sends named event types. The adapter maps them:
- `content_block_delta` → extract `delta.text` → `ChatStreamChunk`
- `message_stop` → `ChatStreamChunk(content="", finish_reason="stop")`
- Other events → pass-through `ChatStreamChunk(content="", finish_reason=None)`

**Usage field mapping**: Anthropic uses `input_tokens` / `output_tokens`. The adapter
renames them: `prompt_tokens = input_tokens`, `completion_tokens = output_tokens`,
`total_tokens = input + output`.

**Embed and Rerank**: Not supported. Both raise `ProviderError` immediately.

---

### 10.3 Azure OpenAI (`cloud/azure_openai_provider.py`)

**URL building is the most complex of all providers**:

```python
def _build_url(self, path: str) -> str:
    base = context.api_endpoint_url.rstrip("/")

    # Azure deployment name resolution (3-tier priority):
    azure_deployment_name = (
        str(context.extra_config["azure_deployment_name"])  # 1. explicit config
        if "azure_deployment_name" in context.extra_config
        else (
            context.deployment_config.deployment_key          # 2. routing key
            if context.deployment_config is not None
            else context.model_name                           # 3. model name (entitlement path)
        )
    )

    url = f"{base}/openai/deployments/{azure_deployment_name}/{path}"
    api_version = context.extra_config.get("api_version") or "2024-02-15-preview"
    return f"{url}?api-version={api_version}"
```

**Example URL produced**:
```
https://my-org.openai.azure.com/openai/deployments/gpt4-prod/chat/completions?api-version=2024-02-15-preview
```

**Azure payload difference**: Does NOT include `model` in the payload body (the model is
already encoded in the URL via the deployment name).

**`api-version` query parameter**: Required by Azure. Defaults to `2024-02-15-preview` if not
specified in `extra_config`.

---

### 10.4 AWS Bedrock (`cloud/bedrock_provider.py`)

**Transport**: NOT httpx. Uses an `aioboto3.Session` object. Each call creates a short-lived
`bedrock-runtime` client within an async context manager:

```python
async with self._bedrock_session.client("bedrock-runtime", region_name=...) as client:
    response = await client.converse(...)
```

**Auth**: IAM credential chain, NOT an API key. The `api_key` parameter is accepted in the
constructor for registry compatibility but is not used. AWS credentials are resolved by the
aioboto3 SDK from the environment (IAM role, instance profile, env vars, etc.).

**AWS region resolution** (3-tier priority):
1. `context.cloud_region` — explicit per-route value
2. `context.extra_config["aws_region"]` — provider-specific override
3. Hard default: `"us-east-1"`

**Chat operation**: Uses the Bedrock **Converse API** (`client.converse()`). This is the
higher-level, model-agnostic API that works across Claude, Titan, Llama, etc.

**Embed operation**: Uses the lower-level **InvokeModel API** (`client.invoke_model()`),
because the Converse API does not support embeddings. Body format: `{"inputText": "..."}`.
Only the first input text is used (Bedrock embedding is single-input per call).

**Message format for Converse**: System prompts are silently skipped (Bedrock's Converse API
handles them via a separate `system` parameter not currently wired). Non-system messages are
converted to Bedrock's content-block format:
```python
{"role": "user", "content": [{"text": "Hello"}]}
```

**Response field mapping**: Bedrock uses `inputTokens` / `outputTokens` / `totalTokens`
(camelCase). The adapter maps these to our `Usage(prompt_tokens, completion_tokens,
total_tokens)`.

---

### 10.5 vLLM (`direct/vllm_provider.py`)

**Design**: OpenAI-compatible REST API, nearly identical to `OpenAIProvider`.

**Key difference — auth is optional**: vLLM can run without authentication (private cluster
without a load balancer). The adapter checks:

```python
api_key = self._api_key.get_secret_value()
if api_key:                                  # only add auth header if a key is configured
    headers["Authorization"] = f"Bearer {api_key}"
```

This is the only provider where auth is conditional on whether a secret was actually provided.

**Health endpoint**: Unlike OpenAI (which probes `/models`), vLLM has a dedicated `/health`
endpoint.

**Rerank**: Not supported by vLLM. Raises `ProviderError`.

---

## 11. Authentication differences across providers

| Provider | Header | Format | Override method? |
|---|---|---|---|
| **OpenAI** | `Authorization` | `Bearer <key>` | No (uses base `_build_auth_headers()`) |
| **Anthropic** | `x-api-key` | `<key>` (no prefix) | Yes — overrides `_build_request_headers()` completely |
| **Azure OpenAI** | `api-key` | `<key>` (no prefix) | Yes — overrides `_build_request_headers()` completely |
| **Bedrock** | None | IAM/SigV4 (SDK-managed) | N/A — `api_key` parameter is ignored |
| **vLLM** | `Authorization` (optional) | `Bearer <key>` or omitted | Yes — conditional on key presence |

**Anthropic also requires** the `anthropic-version: 2023-06-01` header on every request.
This is a breaking-change versioning mechanism — requests without it are rejected.

**Azure also requires** `Content-Type: application/json` and the `?api-version=...` query
parameter on every URL.

---

## 12. Provider capability matrix

Not every provider supports every operation. The routing layer validates this via the YAML
catalog (model capabilities). The provider layer enforces it defensively by raising `ProviderError`
for unsupported operations rather than silently no-oping:

| Provider | `generate` (chat) | `stream_generate` | `embed` | `rerank` |
|---|---|---|---|---|
| **OpenAI** | ✓ | ✓ | ✓ | ✗ raises ProviderError |
| **Anthropic** | ✓ | ✓ | ✗ raises ProviderError | ✗ raises ProviderError |
| **Azure OpenAI** | ✓ | ✓ | ✓ | ✗ raises ProviderError |
| **Bedrock** | ✓ | ✓ | ✓ (single-input) | ✗ raises ProviderError |
| **vLLM** | ✓ | ✓ | ✓ | ✗ raises ProviderError |

**Why raise at the provider level if routing already validated capability?**

Defense in depth. The routing layer validated against the YAML catalog before the provider
was ever called. But the provider layer is the last safety net. If a provider class is
registered for a model that does not actually support rerank, the provider raises immediately
with a clear error message rather than sending a malformed request to the external API.

---

## 13. Error classification — `http_errors.py`

### 13.1 Why a separate module

Each provider and SDK emits different exception types:
- OpenAI, Anthropic, Azure: `httpx.HTTPStatusError`, `httpx.TimeoutException`,
  `httpx.RequestError`
- Bedrock: `botocore.exceptions.ClientError`, `ConnectTimeoutError`, `ReadTimeoutError`

Without classification, upstream layers would need to `isinstance` check against every
possible exception type from every library. That creates coupling to implementation details.

`classify_error()` is the anti-corruption boundary. It always returns a `ProviderError`
subclass — never re-raises the raw exception. Upstream layers branch on stable domain error
types.

### 13.2 Classification logic

```
classify_error(exc, provider_name)
    │
    ├─ httpx.TimeoutException
    │     → ProviderTimeoutError
    │
    ├─ httpx.HTTPStatusError
    │     ├─ 401 (unauthorized)
    │     │     ├─ "expired" in body → ExpiredTokenError
    │     │     └─ otherwise        → InvalidAPIKeyError
    │     ├─ 429 (rate limited)
    │     │     ├─ "token" in body → TokensPerMinuteExceededError (+ Retry-After)
    │     │     └─ otherwise       → RequestsPerMinuteExceededError (+ Retry-After)
    │     ├─ 400 (bad request)
    │     │     ├─ "model" + "not found" in body → ModelNotSupportedError
    │     │     └─ otherwise                    → InvalidRequestError
    │     ├─ 500/502/503/504 (server error)
    │     │     → ServiceDownError
    │     └─ other status
    │           → ProviderInternalError
    │
    ├─ httpx.RequestError (network-level, no HTTP response)
    │     → ServiceDownError
    │
    ├─ botocore ConnectTimeoutError / ReadTimeoutError
    │     → ProviderTimeoutError
    │
    ├─ botocore ClientError
    │     ├─ AccessDeniedException / UnrecognizedClientException / InvalidSignatureException
    │     │     → InvalidAPIKeyError
    │     ├─ ThrottlingException
    │     │     → RequestsPerMinuteExceededError
    │     ├─ ValidationException
    │     │     → InvalidRequestError
    │     ├─ ResourceNotFoundException
    │     │     → ModelNotSupportedError
    │     └─ InternalServerException / ServiceUnavailableException
    │           → ServiceDownError
    │
    └─ anything else
          → ProviderInternalError (safe fallback — never crashes)
```

### 13.3 Optional boto dependency

`botocore` is optional — the application also serves httpx-based providers without it. The
module handles this with a try/except import:

```python
try:
    from botocore.exceptions import ClientError, ConnectTimeoutError, ReadTimeoutError
except ImportError:
    class _BotocoreStub(Exception):
        response: ClassVar[dict] = {}

    ClientError = ConnectTimeoutError = ReadTimeoutError = _BotocoreStub
```

If botocore is not installed, `isinstance(exc, ClientError)` is always `False` (the stub
never matches a real exception), so AWS-specific branches are silently skipped. The fallback
`ProviderInternalError` at the bottom still catches anything not classified.

### 13.4 `Retry-After` header extraction

For 429 (rate limited) responses, the classifier reads the `Retry-After` header:

```python
retry_after = int(exc.response.headers.get("Retry-After", 0)) or None
```

This value is carried in `RequestsPerMinuteExceededError` and `TokensPerMinuteExceededError`
so the caller (or the circuit breaker policy) can implement deterministic backoff instead of
guessing retry intervals.

---

## 14. Downstream: normalized responses back to the service layer

All five provider adapters translate their raw responses into the same internal schemas:

### `ChatResponse`
```python
ChatResponse(
    content="The answer is 42.",    # extracted text content
    role="assistant",               # always "assistant" for completions
    finish_reason="stop",           # "stop", "length", "content_filter", etc.
    usage=Usage(
        prompt_tokens=12,
        completion_tokens=8,
        total_tokens=20,
    ),
    model="gpt-4o",                 # model name echoed from provider response
    raw_response={...},             # full raw JSON preserved for diagnostics
)
```

**`raw_response`** stores the provider's complete JSON response. This is useful for
debugging when the normalized fields don't capture some provider-specific metadata.

### `ChatStreamChunk`
```python
ChatStreamChunk(
    content=" world",   # text fragment for this chunk (may be empty)
    finish_reason=None, # None until the final chunk, then "stop" / "length" etc.
    index=0,            # choice index (0 for single-completion requests)
    raw_chunk={...},    # full raw SSE chunk for diagnostics
)
```

### `EmbedResponse`
```python
EmbedResponse(
    embeddings=[[0.012, -0.045, ...], ...],  # list of float vectors
    model="text-embedding-3-small",
    usage=Usage(prompt_tokens=6, total_tokens=6),
)
```

### Usage field normalization across providers

Each provider uses different field names. The adapter is responsible for the rename:

| Provider | Input tokens field | Output tokens field |
|---|---|---|
| OpenAI | `prompt_tokens` | `completion_tokens` |
| Anthropic | `input_tokens` | `output_tokens` |
| Bedrock | `inputTokens` | `outputTokens` |
| Azure | `prompt_tokens` | `completion_tokens` |
| vLLM | `prompt_tokens` | `completion_tokens` |

All are mapped to `Usage(prompt_tokens=..., completion_tokens=..., total_tokens=...)`.

---

## 15. Package structure

```
app/providers/
│
├── __init__.py               # Re-exports BaseProvider, ProviderRegistry
│
├── base_provider.py          # Abstract contract + shared resilience + streaming architecture
│                             # BaseProvider[TransportT] (generic abstract class)
│                             # generate / embed / rerank / stream_generate (public)
│                             # _generate / _embed / _rerank / _stream_generate (abstract)
│                             # health_check (abstract)
│                             # _call_with_breaker / _build_auth_headers / _emit_structured_log
│                             # _handle_provider_error / _effective_timeout (shared helpers)
│                             # _StreamError / _StreamComplete / _STREAM_COMPLETE (queue signals)
│
├── registry.py               # ProviderRegistry
│                             # get_provider(context) → cached or newly built BaseProvider
│                             # invalidate(fingerprint) → removes cached instance
│                             # _build_provider → 4-step construction (class + transport + breaker + secret)
│                             # _resolve_implementation_class → dynamic import from YAML config
│
├── http_errors.py            # classify_error(exc, provider_name) → ProviderError
│                             # Anti-corruption boundary: httpx + botocore → domain errors
│                             # Optional botocore support with stub fallback
│
├── direct/                   # Providers that call REST HTTP APIs via httpx
│   ├── __init__.py
│   ├── openai_provider.py    # OpenAI API (chat + embed; rerank unsupported)
│   ├── anthropic_provider.py # Anthropic Messages API (chat only)
│   └── vllm_provider.py      # Self-hosted vLLM, OpenAI-compatible (chat + embed)
│
└── cloud/                    # Providers using cloud SDKs or platform-specific conventions
    ├── __init__.py
    └── azure_openai_provider.py  # Azure OpenAI (chat + embed; deployment URL building)
    └── bedrock_provider.py       # AWS Bedrock (aioboto3 SDK; Converse + InvokeModel)
```

**`direct/` vs `cloud/` split**:

- `direct/` providers use httpx and API-key-style auth. If you are debugging HTTP payload
  shapes, headers, or SSE parsing, look here.
- `cloud/` providers use cloud-platform SDKs or URL conventions. If you are debugging IAM
  credentials, deployment naming, API versioning, or SDK invocation, look here.

---

## 16. How to add a new provider

Adding a new provider requires three things and no changes to the registry or service layer:

**Step 1 — Create the provider class**

Add a file (e.g., `app/providers/direct/cohere_provider.py`):

```python
class CohereProvider(BaseProvider[httpx.AsyncClient]):

    async def _generate(self, request: ChatRequest) -> ChatResponse:
        headers = self._build_request_headers()      # or override for Cohere-specific auth
        payload = self._build_cohere_chat_payload(request)
        ...
        return self._parse_cohere_response(data)

    async def _embed(self, request: EmbedRequest) -> EmbedResponse: ...
    async def _rerank(self, request: RerankRequest) -> RerankResponse: ...
    async def _stream_generate(self, request: ChatRequest) -> AsyncIterator[ChatStreamChunk]: ...
    async def health_check(self) -> HealthStatus: ...
```

**Step 2 — Create the YAML config**

Add `config/providers/cohere.yaml`:

```yaml
provider_name: cohere
provider_type: http
implementation_class: app.providers.direct.cohere_provider.CohereProvider
default_timeout_seconds: 30.0
default_max_retries: 2
default_temperature: 0.7
auth:
  header_name: Authorization
  header_prefix: Bearer
models:
  - model_name: command-r-plus
    capabilities: [chat, rerank]
    max_output_tokens: 4096
```

**Step 3 — Register deployments**

Create `DeploymentConfig` records in PostgreSQL pointing to `provider_name: "cohere"`.

The registry will dynamically import `CohereProvider` on the first request to a deployment
using this provider.

---

## 17. How to debug a provider failure

### 17.1 Which error tells you what

| Error | What happened | Where to look |
|---|---|---|
| `ProviderTimeoutError` | Request timed out waiting for the provider | Increase `timeout_seconds` in deployment config; check provider status |
| `InvalidAPIKeyError` | API key is wrong or missing | Check `secret_reference` points to the right key; check secret store |
| `ExpiredTokenError` | API key / token has expired | Rotate the key; update secret store |
| `RequestsPerMinuteExceededError` | Rate limit (request count) hit | Check `Retry-After` in error; consider backoff or quota upgrade |
| `TokensPerMinuteExceededError` | Rate limit (token count) hit | Reduce `max_tokens` or request rate |
| `ModelNotSupportedError` | Model name doesn't exist at provider | Verify model name in YAML and provider dashboard |
| `InvalidRequestError` | Malformed payload sent to provider | Check YAML model name, payload building in provider adapter |
| `ServiceDownError` | Provider returning 5xx or unreachable | Check provider status page; breaker may be open |
| `ProviderInternalError` | Unclassified exception | Check logs for raw exception detail; may be new provider error type |

### 17.2 Circuit breaker inspection

The circuit breaker state is stored in Redis. To inspect it:

```bash
# List circuit breaker keys for all providers
redis-cli KEYS "*circuit*"

# Check state for OpenAI
redis-cli GET "circuit_breaker:openai:state"

# Check failure count
redis-cli GET "circuit_breaker:openai:failures"
```

If the breaker is `OPEN` and you want to force a reset after fixing the underlying issue:

```bash
redis-cli DEL "circuit_breaker:openai:state"
redis-cli DEL "circuit_breaker:openai:failures"
```

### 17.3 Structured log fields to filter on

Every provider call emits a log entry with these searchable fields:

| Field | Example value | Use |
|---|---|---|
| `provider_name` | `"openai"` | Filter to one provider |
| `model_name` | `"gpt-4o"` | Filter to one model |
| `operation` | `"chat.generate"` / `"embed"` / `"chat.stream_generate"` | Filter to one operation type |
| `latency_ms` | `842` | Alert on P95 latency; identify slow providers |
| `status_code` | `429` | Count rate limit events |
| `error_type` | `"RequestsPerMinuteExceededError"` | Count error categories |
| `usage` | `{"prompt_tokens": 12, "completion_tokens": 8}` | Token consumption |

### 17.4 Provider build failures

If `ProviderRegistry._build_provider()` fails, the error is one of:

1. **`ImportError`** — `implementation_class` in YAML points to a non-existent module/class.
   Check the fully-qualified class name spelling.

2. **`KeyError` / `RuntimeError` from `SecretStore`** — the `secret_reference` was not found
   in the secret store. Check that the key name matches what is configured in Vault / env var.

3. **`ValueError` from `SecretStore`** — decryption failed (wrong master key or tampered
   ciphertext for AESGCMSecretStore). Check encryption settings.

4. **`RuntimeError` from `get_provider_circuit_breaker`** — Redis is unreachable. Check Redis
   connectivity; the readiness check at `/health/ready` should already report this.

### 17.5 Tracing a request end to end using logs

1. Capture the `X-Request-ID` from the response header.
2. Filter logs by `request_id` to find all entries for this request.
3. Look for: auth layer log → routing layer log → `"Provider call completed"` log.
4. If step 3's log is missing, the call never reached the provider (quota check failed, or
   registry build failed before the call).
5. If the log shows `error_type`, cross-reference with section 17.1.

---

## 18. Common gotchas

### Gotcha 1 — "Provider still using old API key after rotation"

The provider instance is cached by `route_fingerprint`. Rotating a key in the secret store
does NOT automatically rebuild the cached provider. You must trigger cache invalidation:

```python
await provider_registry.invalidate(route_fingerprint)
```

This is typically triggered via a Redis pub/sub event from the management API when a
deployment credential is updated. If that event is not firing, the old key will be used until
the process restarts.

### Gotcha 2 — "Azure request returning 404 or 'deployment not found'"

Azure OpenAI routes through `azure_deployment_name`, NOT the model name. The URL is:
```
{endpoint}/openai/deployments/{azure_deployment_name}/chat/completions
```

The `azure_deployment_name` resolution order is:
1. `extra_config["azure_deployment_name"]` — if set, this wins
2. `deployment_config.deployment_key` — the routing key
3. `model_name` — last resort (Path A / user entitlement)

If your Azure deployment is named `my-gpt4` but `deployment_key` is `gpt4-production`, the
request will go to `/deployments/gpt4-production/...` and Azure will 404 it. Fix: set
`extra_config["azure_deployment_name"] = "my-gpt4"` in the deployment record.

### Gotcha 3 — "Bedrock returning AccessDeniedException even with correct credentials"

Bedrock uses IAM permissions, not API keys. The IAM role/user must have:
```
bedrock:InvokeModel
bedrock:Converse
bedrock:ConverseStream
```
on the model ARN (`arn:aws:bedrock:{region}::foundation-model/{model-id}`). An
`AccessDeniedException` from Bedrock is classified as `InvalidAPIKeyError` by the error
classifier — check IAM policies, not secret store entries.

### Gotcha 4 — "Streaming stops mid-response with no error"

Most likely cause: the client disconnected. When the caller stops iterating, the `finally`
block in `stream_generate()` cancels the producer task. This is expected behavior. Look for
a `asyncio.CancelledError` in the producer's log context. If streaming stops without a client
disconnect, check for `_StreamError` in the queue — the producer encountered an exception but
the consumer exited before reading it.

### Gotcha 5 — "Circuit breaker opens after a spike but doesn't recover"

The breaker moves to `HALF-OPEN` after a cooldown period and allows one trial request. If that
trial request also fails (perhaps because the provider is still recovering), the breaker
returns to `OPEN`. In high-traffic scenarios, even one trial failure per cooldown period can
keep the breaker open indefinitely. Solution: reduce load sent to this provider, or manually
reset the breaker state in Redis after confirming provider recovery.

### Gotcha 6 — "Adding a new provider — 'module not found' at runtime"

The `implementation_class` in the YAML is dynamically imported on first use via
`importlib.import_module()`. A typo in the module path fails at runtime on the first request,
not at startup. Always test a new provider YAML by sending a test request immediately after
deploying, and check logs for `ImportError`.

### Gotcha 7 — "Anthropic returns 400 'messages: must alternate user/assistant'"

Anthropic requires messages to alternate between `user` and `assistant` roles. Two consecutive
`user` messages are rejected with HTTP 400. The adapter does not auto-merge consecutive same-
role messages. The caller must ensure the message list alternates correctly before calling
the endpoint. This is a payload validation responsibility of the API layer, not the provider
adapter.

---

> **Author**: Shubham Singh
>
> **Reading order for new developers**:
> 1. This document (start here)
> 2. [base_provider.py](base_provider.py) — the contract and resilience layer
> 3. [registry.py](registry.py) — how providers are built and cached
> 4. [http_errors.py](http_errors.py) — the error anti-corruption layer
> 5. [direct/openai_provider.py](direct/openai_provider.py) — simplest concrete adapter
> 6. [cloud/bedrock_provider.py](cloud/bedrock_provider.py) — SDK-based adapter (contrast with OpenAI)
