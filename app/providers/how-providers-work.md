# How Providers Work

Author: Shubham Singh

## Why this document exists

The `app/providers` folder is one of the hardest parts of the codebase to understand at first glance.
It deals with:

- external LLM APIs,
- different authentication styles,
- circuit breaker behavior,
- streaming responses,
- error translation,
- and provider object reuse.

That is a lot to hold in your head at once.

This guide is meant to explain the folder in plain language, step by step, with examples and rationale.
The goal is not just to tell you *what* the code does, but also *why the design was chosen*.


## The short version

This folder is the layer that actually talks to external model providers such as OpenAI, Anthropic, Azure OpenAI, Bedrock, or self-hosted vLLM.

The rest of the application should not have to know:

- what URL a provider expects,
- what headers it needs,
- what its request body looks like,
- how it streams tokens,
- or what kind of exception it throws.

So this folder hides those differences behind one common interface.


## The main idea in one sentence

The application asks for "a provider that can handle this resolved route", and the provider layer returns an object with a small stable set of methods like:

```python
await provider.generate(request)
await provider.embed(request)
await provider.rerank(request)
```

That means the service layer can stay simple even when the external providers are very different.


## The mental model

If you are reading this late at night, the easiest mental model is this:

- `inference_routing` decides *where a request should go*.
- `providers` decides *how to talk to that destination safely*.

Another way to say it:

- routing answers: "Which provider, model, endpoint, and secret reference should we use?"
- providers answer: "Now that we know where to go, how do we actually call it?"


## A request journey from start to finish

Here is the normal path for an inference request:

1. The API layer receives a request.
2. The routing layer resolves the tenant, deployment, model, endpoint, and secret reference.
3. The service layer asks `ProviderRegistry` for the correct provider instance.
4. The registry either returns a cached provider or builds one.
5. The service calls `generate`, `embed`, `rerank`, or `stream_generate`.
6. The provider adapter translates the generic request into the provider-specific API call.
7. The provider returns a normalized response back to the service layer.

In compact form:

```text
API request
-> routing decides the destination
-> provider registry returns the adapter
-> provider adapter calls the external LLM API
-> normalized response comes back
```


## Why not call OpenAI or Anthropic directly from the service layer?

Because that makes the whole application harder to change.

Imagine the service layer directly knew all of this:

- OpenAI wants `Authorization: Bearer ...`
- Anthropic wants `x-api-key` plus an API version header
- Azure OpenAI uses deployment-specific URLs
- Bedrock uses the AWS SDK instead of `httpx`
- vLLM is OpenAI-like, but not exactly identical

If those details were spread across services, then every new provider would force changes in many places.
That creates coupling, duplication, and bugs.

The provider layer exists to keep those details in one place.


## The important files

### `base_provider.py`

This is the common contract for all provider implementations.

It says, in effect:

"No matter which provider you are, you must support the same public entry points and follow the same resilience rules."

This is important because consistency is a feature.
If every provider had different method names or different failure behavior, the rest of the application would become more complex.


### `registry.py`

This file builds and caches provider instances.

It matters because provider construction is not free.
Building a provider may involve:

- loading the provider class,
- creating or reusing a transport client,
- wiring the circuit breaker,
- looking up secrets,
- and preparing the provider object.

Doing all of that for every request would waste time and memory.

So the registry keeps a cache of provider instances by route fingerprint.


### `http_errors.py`

This file translates raw transport or SDK exceptions into application-level provider errors.

That matters because upstream layers should not have to understand:

- `httpx.HTTPStatusError`
- AWS `ClientError`
- timeout variations
- vendor-specific error payloads

Instead, they should see stable domain errors with stable meaning.


### `direct/`

This folder contains providers that talk to external services over HTTP APIs using `httpx`.

Examples:

- `openai_provider.py`
- `anthropic_provider.py`
- `vllm_provider.py`


### `cloud/`

This folder contains providers that use cloud-specific behavior or SDK patterns.

Examples:

- `azure_openai_provider.py`
- `bedrock_provider.py`


## Why `BaseProvider` matters so much

`BaseProvider` is not just an abstract class.
It is where the shared safety rules live.

It makes sure that:

- provider calls go through the circuit breaker,
- shared logging happens consistently,
- shared error translation happens consistently,
- providers do not store request-specific mutable state on the instance.

This is a big architectural decision.
It says:

"Provider-specific code should focus on translating requests and responses, not re-implementing resilience and plumbing every time."


## Circuit breaker: what it is and why it matters

This is one of the most important ideas in the folder.

A circuit breaker protects the system from repeatedly calling a dependency that is currently failing.

Very simply:

- if a provider is healthy, requests flow normally,
- if it starts failing too much, the breaker "opens" and blocks more calls for a while,
- later it allows a small trial call,
- if the trial succeeds, normal traffic resumes.

Why this matters:

Without a breaker, a failing provider can drag the whole service into a storm of timeouts, retries, and slow responses.

With a breaker, the system fails faster and more predictably.


## A plain example of circuit breaker value

Imagine OpenAI is timing out for 30 seconds.

Without a breaker:

- request 1 waits and fails,
- request 2 waits and fails,
- request 3 waits and fails,
- hundreds more requests keep piling up.

That can waste threads, event-loop time, and customer patience.

With a breaker:

- repeated failures are noticed,
- the breaker opens,
- later requests fail fast instead of waiting on a known-bad dependency,
- the system gets a chance to recover.

So the breaker is not just a fancy pattern.
It is a practical way to stop one external outage from becoming an internal outage too.


## Why the circuit breaker is in `BaseProvider`

Putting the breaker in the base class is deliberate.

If each provider subclass handled the breaker on its own, the code would drift.
One provider might wrap `generate` but forget `embed`.
Another might log differently.
Another might treat streaming differently.

Centralizing the breaker means the policy is enforced uniformly.

This is one of those decisions that may seem abstract at first, but it saves real pain later.


## The hardest part: streaming

Streaming is harder than normal request-response calls.

Why?

Because a normal call returns one final result.
A streaming call returns many small pieces over time.

That difference matters for the breaker.
Most breaker libraries are built around normal async functions, not async generators.

So `stream_generate()` in `BaseProvider` uses a small internal queue and a producer task.

That may sound advanced, but the idea is simple:

- the producer reads chunks from the provider,
- each chunk is put into a queue,
- the caller reads chunks from the queue one by one,
- the breaker still guards the producer side of the provider call.

This design exists so streaming still obeys the same resilience rules as non-streaming calls.


## A simple streaming example

Suppose the model generates:

```text
Hello
 there
 friend
```

The provider may yield those pieces one chunk at a time.

Internally, the base class helps move those chunks safely from the provider-facing side to the caller-facing side.

So the caller can do:

```python
async for chunk in provider.stream_generate(request):
    print(chunk.content)
```

without needing to know how the queue or breaker mechanics work.


## Why the provider instances are treated as immutable

This is about safety under concurrency.

In a web application, many requests can use the same provider object over time.
If the provider object stored request-specific mutable state, then one request could affect another.

For example, imagine this bad pattern:

```python
self._current_request_id = request.id
```

If two requests hit the same provider instance close together, that state can be overwritten.
Now logs, retries, or telemetry could attach the wrong context to the wrong request.

That is why provider objects should keep only shared configuration and transport objects, not per-request mutable state.


## Why the registry caches providers

The registry uses a route fingerprint as the cache key.

That fingerprint represents the resolved route identity, such as:

- provider name,
- model,
- endpoint,
- credential reference,
- and other route-defining configuration.

Why cache by fingerprint?

Because two requests that resolve to the same effective route should be able to reuse the same provider object.
That improves performance and avoids rebuilding the same adapter repeatedly.


## A registry example

Imagine two requests resolve to:

- provider: `openai`
- model: `gpt-4o`
- endpoint: same URL
- secret reference: same secret

Those requests should use the same cached provider instance.

But if the endpoint or secret reference changes, the fingerprint changes too.
That produces a different cache entry, which is correct because it is no longer the same effective route.


## Why error mapping is a separate file

External systems fail in many different ways.

A provider may return:

- 401 unauthorized
- 429 rate limited
- 500 internal error
- timeout
- malformed response
- SDK-specific exception types

If raw exceptions leaked upward, the rest of the application would have to know far too much about each provider library.

So `http_errors.py` acts like a translator.

It says:

"No matter what raw thing happened underneath, convert it into a provider error that the rest of the app understands."

That is why the file matters.
It reduces chaos.


## Direct providers vs cloud providers

This split is important and useful.

### Direct providers

These generally:

- call HTTP endpoints directly,
- use `httpx`,
- and often use API-key-style authentication.

Examples:

- OpenAI
- Anthropic
- vLLM


### Cloud providers

These often:

- use cloud-specific URLs or SDKs,
- use IAM or managed identity,
- and require platform-specific calling conventions.

Examples:

- Azure OpenAI
- AWS Bedrock

This separation helps new developers reason about the code faster.
If you are debugging an SDK or cloud-identity problem, you likely want `cloud/`.
If you are debugging plain HTTP payloads and headers, you likely want `direct/`.


## How to read this folder if you are new

If you are starting from zero, read in this order:

1. `how-providers-work.md`
2. `base_provider.py`
3. `registry.py`
4. `http_errors.py`
5. one concrete provider such as `direct/openai_provider.py`
6. one contrasting provider such as `cloud/bedrock_provider.py`

That order works well because it goes from concept -> shared rules -> infrastructure -> examples.


## What to pay attention to in a concrete provider

When reading a provider implementation, look for these questions:

1. How is authentication built?
2. What URL or SDK method is being called?
3. How is the request payload translated?
4. How is the response parsed back into our schema?
5. How are streaming chunks handled?
6. What happens when the provider does not support an operation?

If you can answer those six questions for one provider, you understand most of the provider layer.


## A practical reading example: OpenAI vs Bedrock

OpenAI is a good first provider to read because it is closer to the common mental model:

- send HTTP request,
- receive HTTP response,
- parse JSON.

Bedrock is a good second provider to read because it shows why the abstraction exists:

- it uses SDK calls instead of plain HTTP,
- auth works differently,
- the request and response shapes differ,
- but the rest of the application still sees the same provider interface.

That contrast is one of the best ways to understand the value of the design.


## Final takeaway

The provider layer is not complicated because the team wanted it to be complicated.
It is complicated because external providers are inconsistent, failure-prone, and operationally important.

The purpose of this folder is to absorb that complexity once so the rest of the codebase can stay cleaner.

If you remember only one sentence, remember this:

`app/providers` is the place where we turn many different external LLM systems into one internal way of working.
