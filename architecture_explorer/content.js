/* ============================================================
   llm_services — content model
   ============================================================
   Everything the engine renders lives here: the pressure probes (Q),
   the eight pipeline stages plus four cross-cutting foundations
   (STAGES), the component contracts (COMPONENTS), and the rail order.

   Scope note: this describes the architecture of a multi-tenant LLM
   gateway. Every mechanism below is drawn from the service's own
   source — the module names, the ordering, the failure handling and
   the trade-offs are the ones the code actually implements. Tenant
   names, deployment keys and identifiers used as examples are
   synthetic. There are no measured latency figures here, and none are
   claimed: the service has no published benchmark, and inventing one
   would be the least defensible thing on the page.
   ============================================================ */

/* ---------- pressure probes (question bank) ---------- */

const Q = {
  twoServices: {
    q: "Quota lives in a second service, reached over HTTP, on the critical path of every inference call. Why is that not just a self-inflicted network hop?",
    a: "Because the thing being protected is a shared, finite resource that no single replica can see. A provider endpoint has one real concurrency and token ceiling, and this service runs as many independent workers; if each one counted its own usage, the sum would exceed the ceiling by exactly the replica count and the provider would start rejecting calls that the system believed it had budget for. Centralising the count is the only way the number means anything. The honest cost is real and it is three separate things. First, latency: a reservation is a synchronous round trip sitting between a fully resolved route and the provider call, and it is pure overhead from the caller's point of view. Second, availability: the token manager becomes a hard dependency of inference, so its outage is an inference outage — the client translates that to a 503 rather than pretending it had capacity, which is the correct choice and still a reduction in availability. Third, correctness under partition: a reservation acquired and then never released because the network dropped is capacity the system has lost until the allocation expires. What makes the trade defensible is that the alternative is not 'no coordination', it is per-replica guessing, and a guess that is wrong in the unsafe direction gets you rate-limited by the provider instead.",
  },
  cacheStaleness: {
    q: "A successful authorization is cached in Redis. What stops a revoked user from continuing to get inference from a cached 'yes'?",
    a: "Version markers, checked on every read and replaced on every management write. A cached grant carries the four version values that were observed when it was created — one for the tenant, one for the membership, one for the deployment, one for the exact route — and a read fetches the grant and all four markers in a single round trip. If any marker has changed, the grant is discarded and the four database gates run again. Revoking access is not a matter of finding and deleting the right cache entries: the management path writes a fresh random value into one marker, and every grant that depended on it is invalid from that moment, whether it is one entry or ten thousand. The subtle part is the write side, not the read side. Between the gates passing and the grant being stored, a revocation could land — so the store is a compare-and-set against all four markers in one Lua script, and a grant whose dependencies moved mid-check is simply not written. What this does not protect is the window already granted: if a marker write fails, the code raises rather than continuing, so an unavailable Redis makes management writes fail loudly instead of silently leaving stale grants alive. That is deliberate, and it is the opposite choice from the sign-in limiter.",
  },
  whyReread: {
    q: "Authorization already verified the entitlement and cached the answer. Route resolution then reads the same entitlement from PostgreSQL again. Why pay for it twice?",
    a: "Because they are answering different questions and only one of them is safe to cache. Authorization answers 'may this caller use this grant?' — an identity question, whose inputs are the tenant, the membership and the deployment, all of which have explicit invalidation markers behind them. Routing answers 'can that grant execute this operation right now?' and its inputs include the entitlement's live status and the tenant's live status, which are the two values a revocation actually changes. The routing reader is deliberately uncached for exactly that reason, and the comment in the adapter says so: tenant suspension and entitlement revocation are security decisions, so the next request must observe them. There is a cost and it is not hidden — every inference call carries two PostgreSQL reads that a fully cached design would not. What you buy is that the blast radius of a stale cache is bounded to the authorization decision, where invalidation is explicit, and never extends to the question of whether this tenant is still allowed to exist. The resolver also refuses to substitute: if the reader hands back an entitlement whose id differs from the authorized one, that is a configuration error, not a fallback.",
  },
  fingerprint: {
    q: "Every request hashes the whole entitlement into a SHA-256 route fingerprint. What is that actually for, and is a hash per request not wasteful?",
    a: "It is the cache key for the built provider adapter, and it has to be a hash of the whole thing rather than a tuple of the obvious fields because the identity that matters is 'would this produce a different provider object?' — which includes the endpoint, the secret reference, the region and the provider-specific extra config. If the key were the deployment key, a credential rotation would not change it and the cached adapter would keep using the old key until its TTL expired. Hashing the serialised entitlement means any change to any field that feeds the adapter produces a different key, so the next call builds a fresh one. The cost is a JSON serialisation and a hash of a small object on a path that is about to make a network call to a language model — it is not the term that matters in that budget. The weakness is the opposite of wastefulness: it makes the cache key sensitive to fields that do not affect the adapter at all, so an unrelated edit to extra_config evicts a perfectly good provider. That is the safe direction to be wrong in, and I would rather rebuild an adapter unnecessarily than serve a request with a revoked credential.",
  },
  breakerLocal: {
    q: "Circuit breakers are per-process and in-memory. With many replicas, every one of them has to discover the provider outage separately. Why not share that state?",
    a: "Because the obvious way to share it makes the event loop a hostage. The breaker library's shared-state backend performs blocking Redis commands whenever it reads or changes state, and a blocking call inside an asyncio worker freezes every other request in that process while a slow socket resolves — so the mechanism protecting against a slow provider would itself become a slow dependency in the hot path. Beyond the implementation detail, a client-side breaker is protecting this replica's connection pool, and that is genuinely per-replica state: one worker's pool exhaustion is not another's. Shared state also has a failure mode nobody wants, which is that a single unhealthy replica — bad DNS, a broken egress route, a clock problem — trips the circuit for every healthy one. The cost of the local choice is exactly what the question says: the first N failures are paid once per replica rather than once in total, so a provider outage costs the fleet more failed calls before it is contained, and the state is only visible per process. The mitigation is that the registry exposes a state snapshot per provider, so central monitoring can aggregate what it wants to see without the request path depending on it.",
  },
  streamNoRetry: {
    q: "Once a stream has sent its first byte, a failure cannot become an HTTP error. How is that not a worse experience than buffering the whole answer?",
    a: "It is a worse failure experience bought deliberately with a much better success experience, and both halves should be stated. Buffering means the caller waits the full generation before seeing anything; streaming means they read while the model writes, and for a long completion that is the difference between a usable product and a spinner. The cost lands in three places. A post-header failure cannot be an error response, so it is emitted as a named error event inside the stream and the connection closes — a client that ignores event names sees a truncated answer with no signal. The unhandled-exception middleware re-raises rather than trying to write JSON over a live text/event-stream, because mixing the two wire formats is worse than a dropped connection. And an open stream holds a worker and a provider connection for the whole generation, which is why there is a hard per-worker concurrency limiter in front of it that rejects with 503 and a Retry-After rather than queueing an open socket. What is genuinely unsolved is that the delivery layer emits a terminal 'complete' event with a status, but nothing guarantees the client acted on a preceding 'error' event — the contract is documented and not enforced.",
  },
  finalizeAsymmetry: {
    q: "A non-streaming call propagates a quota-finalization failure to the caller. A streaming call only logs it. Defend the inconsistency.",
    a: "It is deliberate and it is still a seam worth naming. For a non-streaming call, committing the terminal accounting record is part of the operation, not cleanup after it — if the reservation cannot be released, returning 200 would tell the caller the request succeeded while the quota ledger disagrees, and that divergence compounds silently across every subsequent request. Propagating it is the only answer that keeps the two stories the same. For a streaming call the same choice is unavailable: by the time usage is known the response headers left long ago, so raising cannot produce a second HTTP response. The failure is therefore logged with the reservation id and the request continues to its terminal event. The consequence is an accounting hole exactly where it is hardest to notice — a token manager outage during a long stream leaks the reservation until it expires, and the only evidence is a log line. What would close it is not a different HTTP choice, it is an outbox: write the terminal accounting record locally and reconcile asynchronously, so a transient outage delays the ledger rather than losing an entry. That does not exist yet, and pretending the log line is equivalent would be dishonest.",
  },
  secretsInMemory: {
    q: "Provider adapters hold the plaintext API key in memory and are cached. How long is a rotated credential still in use, and why is caching them at all acceptable?",
    a: "Bounded by the provider cache TTL, and that bound is the entire reason the cache is time-based rather than purely size-based. Building an adapter means a Vault round trip for the credential, and doing that per request would put a secret-store call in front of every inference call — a latency cost and, worse, a new hard dependency on Vault for traffic that is already authorized. So adapters are cached, and because they contain a SecretStr holding real key material, the cache is bounded in two dimensions at once: a TTL so a rotation becomes visible without a process restart, and an LRU ceiling so route cardinality cannot turn into unbounded retention of credentials in memory. There is also an explicit eviction path for a single route, and the shutdown sequence clears the registry before closing the transports and the secret store, so the plaintext goes first. The honest limitation is the window: between a rotation in Vault and the TTL expiring, calls keep using the old key, and nothing pushes an invalidation. Making that immediate needs the credential-write path to evict the affected route fingerprints, which is a change to the management side, not to the registry.",
  },
  rawSql: {
    q: "Persistence is hand-written SQL strings rather than an ORM. In a codebase this size, is that not asking for an injection bug?",
    a: "It would be, if the strings were built from caller input — and the base class is built specifically so they cannot be. Every query is a named constant with bound parameters, and the one place that assembles SQL dynamically, the partial-update builder, validates the table name and every column name against a plain-identifier pattern, refuses a WHERE clause that is not parameterised equality joined by AND, rejects parameter-name collisions between the SET and WHERE bindings, and requires an explicit RETURNING projection rather than allowing a bare star. That last rule is doing more work than it looks: it means a future column addition cannot silently start appearing in API responses. What the approach buys is that the query the database runs is the query in the file — for a routing read on the request path, being able to read and index for the exact statement matters more than mapper convenience. What it costs is that nothing checks the SQL against the schema at build time. A renamed column is a runtime failure, caught only by a test that actually executes it, and the safe-column tuples must be maintained by hand alongside the DDL. That is a real maintenance tax and it is paid in exchange for the request path having no ORM between it and the plan.",
  },
  guestDoor: {
    q: "There is an unauthenticated guest superuser endpoint in the codebase. Explain why that is not simply a backdoor.",
    a: "It is a development affordance with a startup-level prohibition on reaching production, and I would rather describe it plainly than leave it to be discovered. A fresh local stack has no password for anyone, and the administration surface is exactly what you need in order to create the first user — so there is a configured guest identity that issues a real session token for an existing user id, without a credential. The controls are that it is off unless explicitly enabled, it can only act as a user that already exists in the database, and the settings composition root refuses to construct settings at all when the environment declares production and the guest door is enabled. That last one is the part that matters: it is not a warning log, it is a failed process start, so a promoted compose file fails loudly rather than publishing an open administrative door. What I would not defend is the shape of the risk. It is a single boolean between a deployment and an unauthenticated owner-role session, the check is on the declared environment rather than on anything externally verifiable, and an environment mislabelled as staging gets no protection at all. A build-time exclusion of the route would be stronger than a runtime validator.",
  },
  redisPolicy: {
    q: "The sign-in limiter fails open when Redis is down; authorization-grant invalidation fails closed. Both use the same Redis. Why do they disagree?",
    a: "Because failing open and failing closed are not stylistic choices, they follow from what the component would be wrong about. The sign-in limiter is a rate limit: when Redis is unavailable, refusing every sign-in converts a cache outage into a total authentication outage — a self-inflicted denial of service — while allowing them costs only the rate limit, since password verification, account status and token signing all still apply. So it allows and logs. Grant invalidation is the opposite: if a management write cannot advance the version marker, then cached 'yes' answers that should have died remain usable, and the failure mode is continued access after a revocation. There the safe answer is to fail the management operation, which the cache does by raising a typed error that the API translates to 503. The general rule is that a component whose unavailability costs availability should degrade, and one whose unavailability costs a security invariant should refuse. Where this gets uncomfortable is the readiness probe: it treats Redis as required and pulls the whole instance out of rotation when Redis is down, which is arguably stricter than the per-component behaviour implies — and it is the right call precisely because the invalidation path cannot function without it.",
  },
  quotaObservability: {
    q: "How would you know if the quota ledger had drifted out of step with what the providers actually served?",
    a: "Today, not from anything inside this service, and that is the largest observability gap in it. The instrumentation here is thorough about the mechanics — every provider call emits a structured record with provider, model, operation, latency, status and usage; every request carries a validated correlation id through logs and back out on the response header; readiness probes the two stores it cannot serve without. None of that reconciles. The service reports the usage the provider returned, reports a terminal status for every reservation it opened, and never asks whether the two sides agree. The three places drift can enter are all reachable: a streaming finalization that failed and was only logged, a provider whose usage trailer is absent or partial so the reported counts are estimates, and a reservation abandoned because the process died between acquiring and finalizing. What would close it is a periodic reconciliation against the token manager's own allocation records — comparing reservations opened against reservations terminally accounted for, per deployment, per window — plus an alert on the count of reservations that expired rather than being released, because that number should normally be zero and is currently nobody's dashboard.",
  },
  providerEnum: {
    q: "Provider YAML names its adapter class, but the value is an enum rather than an import path. Is that not just inconvenient indirection?",
    a: "It is the difference between configuration selecting from an audited set and configuration naming arbitrary code to import. A dotted path in a config file that gets resolved at runtime is a code-execution primitive: anyone who can edit that file, or any process that can write it, chooses what the service imports. Making it an enum means the YAML can only select one of five classes that are imported at module load and mapped explicitly, and an unknown value is a validation failure during startup rather than an import attempt during a live request. The registry then resolves through that map and raises a configuration error for anything not registered. The cost is exactly the inconvenience implied: adding a provider is a code change plus a release, not a config change. For a component that holds tenant credentials and makes outbound calls on their behalf, that is the correct side to be inconvenient on. The place it is genuinely awkward is self-hosted models, where teams reasonably want to add an endpoint without a deploy — and the answer there is that the vLLM adapter already covers OpenAI-compatible endpoints, so a new self-hosted model is a catalog row rather than a new class.",
  },
  layering: {
    q: "Why does the role vocabulary live in the schemas package rather than in the auth package that enforces it?",
    a: "Because both the authorization layer and the persistence layer have to agree on what roles exist, and the layering rule says a lower layer never imports from a higher one. Authorization already imports persistence — it reads tenant, membership, deployment and entitlement rows — so auth sits above persistence and cannot be the shared home without inverting that. Schemas sits below both and already owns the canonical role literals, which makes it the only correct place. This is not theoretical tidiness: before it was consolidated, the same role set was hand-typed in four places, and the failure mode of a missed rename is not a crash, it is a persistence validator that happily accepts a role the authorization layer will never grant. Now each namespace is declared once as an ordered tuple, every set and guard list is derived from it by slicing, and an import-time assertion fails the process if the ordering and its literal type drift apart. Two namespaces are kept deliberately separate — platform roles and tenant roles — even though four of the names coincide, precisely so a set derived for one can never be silently reused to check the other.",
  },
};

/* ---------- level 2: stages ---------- */

const STAGES = {
  /* ========== 01 — bootstrap ========== */
  bootstrap: {
    rail: { index: "01", tag: "Compose", name: "Application Bootstrap", desc: "Build every pool before the first request" },
    eyebrow: "01 · Application bootstrap",
    title: "Prove the configuration, then build every shared resource exactly once.",
    reveal: "Nothing is created lazily on a request. If the process is accepting traffic, every pool it needs already exists.",
    summary:
      "A factory turns validated settings into a FastAPI application; the lifespan then builds the PostgreSQL pool, the Redis connection, the shared provider transport, the secret backend and the token-manager client — registering each one's shutdown on an exit stack the moment it exists. Provider and cloud YAML is parsed and validated before any connection is opened, so a typo in a catalog file fails the launch rather than the first request that needs it.",
    decisionHead: "One composition root, and a closer registered before the next dependency is built",
    decisionBody:
      "Every owned resource is pushed onto an AsyncExitStack as soon as it is constructed, not at the end. If the third dependency fails, the first two are still closed in reverse order. Request code never constructs a pool: it reaches process-owned resources through typed accessors that fail with startup guidance rather than an unrelated attribute error.",
    failure: "A half-built runtime serving traffic — a connection pool that leaked because construction failed after it, a provider catalog that is valid for four files and broken for the fifth, or a request discovering at call time that the thing it needs was never created.",
    tradeoff: "Startup is strict and slower, and the process refuses to boot on configuration that a lazier design would tolerate until the affected route was called. A single bad provider YAML file takes the whole service down rather than degrading one provider.",
    talk: "The first design decision has nothing to do with models. It is that the set of things this process owns is fixed and visible in one file, and the process either has all of them or does not start.",
    questions: [Q.providerEnum, Q.guestDoor],
    steps: [
      { label: "Validate settings", meta: "Environment and .env composed into one typed object, with cross-concern rules", kind: "gate", component: "settings-root" },
      { label: "Load the static catalog", meta: "Every provider and cloud YAML parsed and frozen before a socket opens", kind: "deterministic", component: "config-loader" },
      { label: "Build owned resources", meta: "Postgres, Redis, transport, secret backend, token-manager client", kind: "deterministic", component: "exit-stack" },
      { label: "Publish on app.state", meta: "Composed services reachable only through typed accessors", kind: "gate", component: "app-state" },
    ],
  },

  /* ========== 02 — admission ========== */
  admission: {
    rail: { index: "02", tag: "Admit", name: "Request Admission", desc: "Correlate, bound, and never leak an unhandled error" },
    eyebrow: "02 · Request admission",
    title: "Give the request an identity, a size limit, and a guaranteed shape of failure.",
    reveal: "Correlation is assigned at the ASGI boundary, before routing — so even a rejected request is traceable.",
    summary:
      "Three pure-ASGI middlewares wrap every request. The outermost mints or validates a correlation id and echoes it on the response; the next bounds the request body by declared length and by counted bytes for chunked uploads; the innermost catches anything untyped and returns a sanitised 500 — unless the response already started, in which case it re-raises rather than mixing JSON into a live stream.",
    decisionHead: "Pure ASGI middleware, ordered so errors still receive CORS and correlation",
    decisionBody:
      "Decorator-style middleware wraps each request in an extra task, which behaves badly for long-lived streaming responses. Writing these as raw ASGI avoids that entirely. Registration order is inverted by the framework, so the body limit is registered first and ends up innermost — meaning a 413 still leaves through the CORS and request-id layers.",
    failure: "An untyped exception reaching the client as a stack trace, a caller-supplied correlation id landing unvalidated in logs and response headers, and an unbounded chunked body consumed into memory before anything inspects it.",
    tradeoff: "The body limit counts bytes as they arrive for chunked requests, so the rejection happens partway through the upload rather than before it starts. And once a streaming response has begun, the safety net can only close the connection — there is no way back to a clean error page.",
    talk: "This layer exists so that everything above it is allowed to be strict. A route can raise a typed domain error and trust that the boundary turns it into one consistent envelope with the same correlation id the logs carry.",
    questions: [Q.streamNoRetry],
    steps: [
      { label: "Resolve the correlation id", meta: "Accept a caller value only if it matches a strict pattern; otherwise mint one", kind: "deterministic", component: "request-context" },
      { label: "Bound the body", meta: "Reject by Content-Length, then by counted bytes for chunked requests", kind: "gate", component: "body-limit" },
      { label: "Route and execute", meta: "Dependencies resolve, the handler runs, domain errors are raised typed", kind: "deterministic" },
      { label: "Translate the failure", meta: "One JSON envelope: detail, error_code, request_id — and Retry-After where the error carries one", kind: "gate", component: "error-boundary" },
    ],
  },

  /* ========== 03 — identity ========== */
  identity: {
    rail: { index: "03", tag: "Identify", name: "Authentication & Roles", desc: "Prove who is asking, then what rung they hold" },
    eyebrow: "03 · Authentication and platform roles",
    title: "Settle identity completely in one operation, so no caller can validate half a token.",
    reveal: "Signature, algorithm, issuer, audience, time bounds, token kind, role and subject — one call, or none.",
    summary:
      "A bearer token is verified in a single function that checks every part of the contract at once and returns a typed identity. Role guards then compare that identity's platform role against a door list derived from one ordered ladder. The same package also issues tokens for the service's own sign-in, deliberately reading the same settings object so issuance and verification cannot drift.",
    decisionHead: "One validator that cannot be partially used, and guards derived from a single ladder",
    decisionBody:
      "Nine claims are required, the lifetime is bounded against a configured maximum, and the role is checked against the vocabulary before it is cast. Guards are constructed at import time from a slice of the role ordering, so a typo in a permitted-role list fails the launch instead of silently locking out a room at request time.",
    failure: "A signed token that is genuine but unusable — wrong audience, wrong kind, absurd lifetime — being accepted because one call site forgot the second check. And a role vocabulary that drifts between the layer that stores it and the layer that enforces it.",
    tradeoff: "Symmetric signing means every service that verifies these tokens also holds the key that can mint them. Issuance living in the same codebase as verification is convenient and is exactly the coupling a separate identity provider would remove.",
    talk: "Authentication answers who is asking and nothing else. It deliberately does not know about tenants — that question belongs one stage later, and keeping them apart is what stops a valid token from implying a scope it never carried.",
    questions: [Q.layering, Q.guestDoor, Q.redisPolicy],
    steps: [
      { label: "Extract the bearer token", meta: "Header parsed without pretending this service owns a login form", kind: "deterministic" },
      { label: "Validate the whole contract", meta: "Signature, issuer, audience, nine claims, kind, bounded lifetime", kind: "gate", component: "jwt-validator" },
      { label: "Check the rung", meta: "Platform role compared against a door list sliced from the ladder", kind: "gate", component: "role-guard" },
      { label: "Issue, where this service signs", meta: "Sign-in mints the exact claim set the validator demands", kind: "deterministic", component: "token-issuer" },
    ],
  },

  /* ========== 04 — authorization ========== */
  authorization: {
    rail: { index: "04", tag: "Authorize", name: "Inference Authorization", desc: "Four gates in order, then a sealed pass" },
    eyebrow: "04 · Inference authorization",
    title: "Four gates, in a fixed order, and a cached answer that cannot outlive its dependencies.",
    reveal: "Every gate opens or nothing runs. There is no partial pass and no fallback to a weaker grant.",
    summary:
      "The caller's tenant must exist and be active; the caller must be an active member holding a role permitted to run inference; the named deployment must exist and be active; and there must be an active entitlement for that exact tenant, user, deployment, provider and model. When all four open, a frozen access context is built carrying every identifier the rest of the request needs, and cached against version markers for its four dependencies.",
    decisionHead: "Cache the yes, version the dependencies, and compare-and-set on write",
    decisionBody:
      "A grant read fetches the cached answer and all four version markers in one round trip. A management change to a tenant, membership, deployment or route replaces one marker with a fresh random value, invalidating every grant that depended on it without enumerating them. The write back is a compare-and-set against all four, so a revocation landing mid-check cannot be overwritten by the answer it invalidated.",
    failure: "A revoked user continuing to reach a model because a cached decision outlived the decision it was based on, and a check-then-write race quietly re-storing a grant that a concurrent management change had just killed.",
    tradeoff: "Invalidation fails closed: if Redis refuses the marker write, the management operation raises rather than completing, so a cache outage becomes visible as failed administration. That is a deliberate availability cost taken to protect the invariant.",
    talk: "This is the stage where the system decides whether the request exists at all. Everything after it is execution detail — and it is the only stage where the answer is allowed to be cached.",
    questions: [Q.cacheStaleness, Q.redisPolicy, Q.whyReread],
    steps: [
      { label: "Ask the cache first", meta: "Grant plus four version markers, read in one operation", kind: "deterministic", component: "grant-cache" },
      { label: "Run the four gates", meta: "Tenant, membership and role, deployment, exact entitlement — in order, failing at the first", kind: "gate", component: "four-gates" },
      { label: "Build the frozen pass", meta: "Tenant, user, deployment, provider, model, tenant role, entitlement — all resolved", kind: "deterministic" },
      { label: "Store only if unchanged", meta: "Compare-and-set against every marker observed at lookup time", kind: "gate", component: "version-invalidation" },
    ],
  },

  /* ========== 05 — routing ========== */
  routing: {
    rail: { index: "05", tag: "Route", name: "Route Resolution", desc: "Turn an approved grant into an execution plan" },
    eyebrow: "05 · Route resolution",
    title: "Answer a different question: not may they, but can this grant execute this operation right now.",
    reveal: "The resolver re-reads the approved entitlement and never searches for a substitute.",
    summary:
      "Tenant policy and the exact authorized entitlement are re-read from PostgreSQL without a cache, the tenant's provider allow-list is applied, and the provider and model are looked up in the startup-validated catalog and checked for the requested capability. What comes out is a frozen route with every default already resolved — endpoint, timeout, temperature, output ceiling, secret reference, quota key and a deterministic fingerprint.",
    decisionHead: "Uncached reads for the two facts a revocation changes",
    decisionBody:
      "Authorization can be cached because its dependencies have explicit invalidation markers. Tenant status and entitlement status cannot, because they are the values a suspension or revocation actually moves — so both are read fresh on every inference request, and a mismatch between the returned entitlement and the authorized one is treated as a configuration error rather than a fallback.",
    failure: "A suspended tenant or revoked entitlement continuing to serve because a cached routing decision lagged, and an embed-only model being handed a chat request because nobody checked the capability before dialling out.",
    tradeoff: "Two PostgreSQL reads on every inference request that a fully cached design would avoid, and a resolver that will fail a request rather than fall back to the tenant's default deployment when the named grant is unavailable.",
    talk: "Splitting authorization from routing is the decision I would defend hardest. One is an identity question with cacheable inputs; the other is a liveness question with inputs that must not be cached. Merging them means picking the wrong policy for one of them.",
    questions: [Q.whyReread, Q.fingerprint, Q.providerEnum],
    steps: [
      { label: "Re-read tenant policy", meta: "Uncached — a suspension must land on the next request", kind: "gate", component: "live-reads" },
      { label: "Re-read the exact entitlement", meta: "By id; a different record is a configuration error, not an alternative", kind: "gate" },
      { label: "Apply the allow-list and capability", meta: "Tenant provider policy, then model capability for this operation", kind: "gate", component: "capability-check" },
      { label: "Build the frozen route", meta: "Defaults resolved once, plus a SHA-256 fingerprint of the whole grant", kind: "deterministic", component: "route-fingerprint" },
    ],
  },

  /* ========== 06 — quota ========== */
  quota: {
    rail: { index: "06", tag: "Reserve", name: "Capacity Reservation", desc: "Buy the capacity before spending it" },
    eyebrow: "06 · Capacity reservation",
    title: "Reserve against a shared ceiling, and refuse a reservation that names a different endpoint.",
    reveal: "The reservation is checked against the route that was authorized. A mismatch is a protocol error, not a redirect.",
    summary:
      "Before any provider call, the service asks a separate token manager for capacity on this exact route, sending an estimate derived from the request and the resolved output ceiling. A rejection or a queued allocation becomes a 429 carrying Retry-After. When the call finishes — completed, failed, cancelled or disconnected — the reservation is finalised with the real token counts the provider reported.",
    decisionHead: "Central accounting, and a client that refuses to be re-routed by it",
    decisionBody:
      "Concurrency and token ceilings belong to a provider endpoint, not to a replica, so the count has to live in one place. But the token manager is an accounting authority, not a routing authority: if the reservation comes back naming a different endpoint than the authorized route, the client raises rather than executing — because accounting for one deployment while calling another is worse than failing.",
    failure: "Replicas each counting their own usage and collectively blowing a provider's ceiling, and a reservation that silently redirects execution to an endpoint the caller was never authorized for.",
    tradeoff: "A synchronous network round trip on the critical path of every inference call, and a hard availability dependency: when the token manager is unreachable the client raises rather than assuming capacity, which is correct and is still a reduction in availability.",
    talk: "This is the one place the service deliberately depends on something outside itself mid-request. The argument for it is that a shared ceiling counted locally is not a ceiling at all.",
    questions: [Q.twoServices, Q.finalizeAsymmetry, Q.quotaObservability],
    steps: [
      { label: "Estimate and request", meta: "Operation-specific input plus the resolved output ceiling, under a short-lived service token", kind: "deterministic", component: "reservation" },
      { label: "Verify the endpoint", meta: "A reserved endpoint that differs from the authorized route is refused", kind: "gate", component: "endpoint-binding" },
      { label: "Execute under the reservation", meta: "Provider work happens inside the window the reservation opened", kind: "deterministic" },
      { label: "Finalise with real usage", meta: "completed, failed, cancelled or disconnected — always exactly one", kind: "gate", component: "finalization" },
    ],
  },

  /* ========== 07 — execution ========== */
  execution: {
    rail: { index: "07", tag: "Execute", name: "Provider Execution", desc: "One contract over five providers, behind a fuse" },
    eyebrow: "07 · Provider execution",
    title: "Five vendor dialects, one internal contract, and a fuse that preserves the real cause.",
    reveal: "The service layer calls generate, embed, rerank or stream. It never learns which vendor answered.",
    summary:
      "The registry builds an adapter for the route's fingerprint — resolving the implementation class from an audited enum, borrowing the shared HTTP transport, fetching the credential only for auth modes that need one, and coalescing concurrent first builds into one. The adapter translates payloads in both directions behind a template method that applies circuit-breaker policy identically for every provider and normalises transport exceptions into typed domain errors.",
    decisionHead: "Template method for the workflow, anti-corruption boundary for the errors",
    decisionBody:
      "Public operations are final and wrap the breaker; subclasses fill in only the vendor-specific translation. Raw httpx and botocore exceptions are classified once into a canonical error family, so retry policy, alerting and HTTP translation stay stable no matter which vendor failed. Unknown failures become an internal provider error rather than leaking a library type upward.",
    failure: "A slow provider consuming every worker and connection while each request waits out the same timeout, and five different vendor error vocabularies leaking into service code that would then have to branch on all of them.",
    tradeoff: "Breaker state is per process, so each replica discovers an outage independently and the fleet pays the failure threshold once per worker. Cached adapters also hold plaintext credentials, which is why the cache is bounded by both a TTL and an LRU ceiling.",
    talk: "The interesting part is not the adapters, it is the boundary. The moment a vendor exception type appears above this layer, every consumer starts encoding that vendor's failure vocabulary — and that is the coupling that makes swapping a provider expensive.",
    questions: [Q.breakerLocal, Q.secretsInMemory, Q.fingerprint, Q.providerEnum],
    steps: [
      { label: "Resolve or build the adapter", meta: "Keyed on the route fingerprint; concurrent first builds share one construction", kind: "deterministic", component: "provider-registry" },
      { label: "Borrow the shared transport", meta: "One pooled HTTP client for every REST provider; an SDK session for Bedrock", kind: "deterministic", component: "transport-factory" },
      { label: "Fetch the credential", meta: "Only for auth modes that need one; never for ambient cloud identity", kind: "gate", component: "credential-resolution" },
      { label: "Call through the fuse", meta: "Breaker policy applied identically; transport errors classified into domain errors", kind: "gate", component: "circuit-breaker" },
    ],
  },

  /* ========== 08 — delivery ========== */
  delivery: {
    rail: { index: "08", tag: "Stream", name: "Delivery & Settlement", desc: "Stream it, then settle the books exactly once" },
    eyebrow: "08 · Delivery and settlement",
    title: "Admit the stream before the headers leave, and settle it exactly once however it ends.",
    reveal: "Capacity, credential and adapter are all acquired eagerly — so a failure becomes an HTTP error, not a broken stream.",
    summary:
      "A per-worker limiter admits the stream or refuses with 503 and a retry hint. Preparation is eager: the reservation and the provider stream are established before the response starts, so anything that can fail still can fail as a status code. A stateful session object then owns the lifecycle, classifying every terminal path and finalising provider cleanup, quota and capacity exactly once, while the SSE layer adds sequence, thread identity and heartbeats.",
    decisionHead: "An explicit iterator, because an async generator cannot clean up what it never started",
    decisionBody:
      "If the client disconnects before the first chunk, a never-started generator has no cleanup hook at all. A real iterator object has an aclose, so the session can close the provider stream, reconcile usage and release the capacity slot on a path where the generator body never ran. Cleanup is shielded from the disconnect cancellation so it completes rather than being cancelled halfway.",
    failure: "A disconnected client leaking a provider connection, a reservation and a concurrency slot at once — and a slow consumer causing unbounded buffering because the source was read faster than it was written.",
    tradeoff: "Backpressure is bought by reading at most one event ahead, which means the source is not being drained while the socket is slow — correct for memory, and it makes the provider stream's own timeouts the thing that governs a stalled client. Post-header failures can only be reported inside the stream.",
    talk: "The design question in streaming is not how to send tokens, it is what happens when the person on the other end closes the tab. Everything here is arranged so that answer is the same as every other ending.",
    questions: [Q.streamNoRetry, Q.finalizeAsymmetry, Q.quotaObservability],
    steps: [
      { label: "Admit or refuse", meta: "Per-worker concurrency ceiling; a refusal is 503 with Retry-After, never a queued socket", kind: "gate", component: "capacity-lease" },
      { label: "Prepare eagerly", meta: "Reservation and provider stream established before response headers leave", kind: "gate" },
      { label: "Own the lifecycle", meta: "completed, failed, cancelled, disconnected — each classified, cleanup run once", kind: "deterministic", component: "streaming-session" },
      { label: "Frame and deliver", meta: "Thread id, monotonic sequence, heartbeats, one terminal event", kind: "deterministic", component: "sse-delivery" },
    ],
  },

  /* ========== off-rail: control plane ========== */
  controlplane: {
    foundation: { name: "Control Plane" },
    eyebrow: "Cross-cutting · management API",
    title: "Every mutation authorizes, validates references, writes the secret, then invalidates what it changed.",
    reveal: "The management API is what makes an inference route exist. Its last act is always an invalidation.",
    summary:
      "Tenants, users, memberships, deployments, entitlements and the provider and model catalogs are managed through resource routers, each backed by a service that applies the same sequence: authorize against tenant scope, pre-validate foreign references so a missing id is a clean 404 rather than a constraint error, persist, then invalidate the authorization grants the change affects. Rows are scrubbed of secret-bearing fields before they leave the service layer.",
    decisionHead: "Authorization-aware CRUD, with cache coherence as part of the write",
    decisionBody:
      "A management write that does not invalidate is a write that has not taken effect yet. Deployment and entitlement changes advance the version markers for exactly the scope they touched, so a deactivated deployment stops serving on the next request rather than at the end of a TTL. Reference validation runs before persistence so callers get the identifier that was wrong.",
    failure: "A deactivated deployment continuing to serve from cached grants, a credential landing in a database column or a log line, and a foreign-key violation surfacing as an opaque database error instead of a named missing resource.",
    tradeoff: "Every mutating path carries an invalidation that can fail, and it fails the operation when it does. Redaction is also a defensive filter on the way out rather than a guarantee at the query — the safe-column projections are maintained by hand.",
    talk: "This side of the service is deliberately boring, and that is the point: the inference path can be strict about what exists because this path is the only thing that creates it.",
    questions: [Q.cacheStaleness, Q.rawSql, Q.secretsInMemory],
    steps: [
      { label: "Authorize the scope", meta: "Platform badge, or tenant admin membership for a write, tenant read for a list", kind: "gate", component: "scoped-crud" },
      { label: "Validate references", meta: "Tenant, user, provider, model checked before the write is attempted", kind: "gate" },
      { label: "Store the credential", meta: "Written to the secret backend at a versioned path; only a reference is persisted", kind: "deterministic", component: "credential-writer" },
      { label: "Invalidate and redact", meta: "Advance the affected version markers; strip secret-bearing fields from the response", kind: "gate", component: "cache-invalidation" },
    ],
  },

  /* ========== off-rail: secrets ========== */
  secrets: {
    foundation: { name: "Secret Management" },
    eyebrow: "Cross-cutting · credentials",
    title: "Two Vault identities, and neither of them can do the other's job.",
    reveal: "The path that serves requests can read credentials and cannot write. The path that creates them can write and cannot read.",
    summary:
      "Credentials never reach PostgreSQL. A typed credential submitted through the management API is written to a versioned KV path by a write-only Vault identity, and only the resulting reference is stored on the row. At inference time a separate read-only identity resolves that reference. One shared client owns login, lease-aware token refresh, bounded full-jitter retry and path normalisation, so both identities behave identically on the network.",
    decisionHead: "Least privilege expressed as two classes, not two configuration flags",
    decisionBody:
      "The reader is a class with no write method and the writer is a class with no read method. A compromised management endpoint cannot retrieve existing credentials, and a compromised inference path cannot plant one. The Vault policies are rendered from the same variables the application builds its paths from, so policy and client cannot disagree about where secrets live.",
    failure: "A credential in a database backup, in a log line, or in an API response — and a single over-scoped service account that turns any application-level compromise into full credential access.",
    tradeoff: "Vault becomes a hard dependency of the inference path, mitigated only by the provider cache's TTL. A rotation is also not pushed: the old credential stays in use for cached adapters until their TTL expires, because nothing evicts route fingerprints on write.",
    talk: "The thing worth noticing is that the split is structural. It is not a permission that could be widened by a config change — the object serving requests does not have the method.",
    questions: [Q.secretsInMemory, Q.redisPolicy],
    steps: [
      { label: "Accept a typed credential", meta: "Only shapes the provider layer can actually use; unimplemented modes are refused", kind: "gate", component: "credential-shapes" },
      { label: "Write with the write-only identity", meta: "Versioned KV path; rotation writes a new version rather than overwriting", kind: "deterministic", component: "vault-write" },
      { label: "Persist the reference only", meta: "The row carries a pointer; the value never touches PostgreSQL", kind: "deterministic" },
      { label: "Read with the read-only identity", meta: "Resolved at adapter build time, held as a secret string, cleared on shutdown", kind: "gate", component: "vault-read" },
    ],
  },

  /* ========== off-rail: persistence ========== */
  persistence: {
    foundation: { name: "Persistence & Schema" },
    eyebrow: "Cross-cutting · data",
    title: "The query the database runs is the query in the file.",
    reveal: "No ORM between the request path and the plan — and no dynamic SQL built from caller input.",
    summary:
      "One pooled engine per worker stamps out short-lived sessions, each its own transaction, committed or rolled back automatically. Queries are named constants with bound parameters; the only dynamic statement is a partial-update builder that validates identifiers, refuses anything but parameterised equality in its WHERE clause, and requires an explicit RETURNING projection. The schema itself carries the invariants: check constraints, partial unique indexes and composite foreign keys.",
    decisionHead: "Invariants in the database, projections by hand, no star selects",
    decisionBody:
      "A tenant can only have one active entitlement per exact route, one default deployment per provider, and a deployment key that matches a format the routing layer can rely on — all enforced by indexes and checks rather than by application code that could be bypassed. Explicit RETURNING columns mean a future column addition cannot silently start appearing in API responses.",
    failure: "An injection through a dynamically assembled statement, a newly added column leaking through a star select, and duplicate active grants for one route making authorization ambiguous.",
    tradeoff: "Nothing checks these strings against the schema at build time. A renamed column is a runtime failure caught only by a test that executes it, and the safe-column tuples must be maintained alongside the DDL by hand.",
    talk: "The database is treated as a participant in correctness rather than a place to put objects. Several rules that would otherwise be service-layer checks are constraints, which means they hold even for a write that did not come through this service.",
    questions: [Q.rawSql, Q.whyReread],
    steps: [
      { label: "Acquire a scoped session", meta: "One pool per worker; each unit of work is its own transaction", kind: "deterministic", component: "session-provider" },
      { label: "Execute a named statement", meta: "Bound parameters only; no statement assembled from caller values", kind: "deterministic", component: "base-persistence" },
      { label: "Return an explicit projection", meta: "Safe column tuples, never a star select", kind: "gate", component: "safe-projections" },
      { label: "Let the schema refuse", meta: "Checks, partial unique indexes and composite foreign keys hold the invariants", kind: "gate", component: "schema-invariants" },
    ],
  },

  /* ========== off-rail: topology ========== */
  topology: {
    eyebrow: "Architecture decision · service boundary",
    title: "Why quota is a second service, and why it is not two databases.",
    reveal: "One shared PostgreSQL, two services, one read contract between them — and no shared tables.",
    summary:
      "This service owns identity, authorization, routing, credentials and provider execution. A sibling token manager owns capacity accounting. They share one PostgreSQL instance but not one surface: the token manager reads active deployment capacity through a dedicated view, writes its own allocations table, and is otherwise reached only over HTTP. Neither service reaches into the other's tables directly.",
    decisionHead: "Split on what must be counted centrally, not on what looks like a microservice",
    decisionBody:
      "The only thing that genuinely cannot live per replica is the count against a shared provider ceiling. Everything else — routing, authorization, provider adapters — is stateless per request and gains nothing from being remote. So exactly one responsibility was moved out, and the coupling between them is a narrow HTTP contract plus one read-only view with a stable shape.",
    failure: "Every replica maintaining its own view of remaining capacity, which sums to a ceiling breach the moment the fleet scales — and, in the other direction, a distributed monolith where two services reach into each other's tables and neither can be deployed alone.",
    tradeoff: "A shared database instance is a shared failure domain and a shared migration surface, and the read contract is a view that this repository owns and the other service depends on. A genuine split would give each service its own store and pay for the resulting duplication.",
    talk: "I would rather defend one carefully chosen boundary than a diagram with eight boxes. The question to ask of any split is what breaks if it stays in-process, and here there is exactly one honest answer.",
    questions: [Q.twoServices, Q.quotaObservability, Q.rawSql],
    steps: [
      { label: "This service resolves the route", meta: "Identity, authorization, routing, credential — all local", kind: "deterministic" },
      { label: "The token manager counts", meta: "Reservations against a shared ceiling, in one place, over HTTP", kind: "gate" },
      { label: "One read contract", meta: "Active deployment capacity exposed as a view with a stable shape", kind: "deterministic" },
      { label: "No shared tables", meta: "Allocations belong to the token manager; catalog and tenancy belong here", kind: "gate" },
    ],
  },
};

/* ---------- level 3: component contracts ---------- */

const COMPONENTS = {
  bootstrap: {
    "settings-root": {
      eyebrow: "Bootstrap · configuration",
      title: "Settings Composition Root",
      reveal: "The rules that span two concerns live here, because neither concern alone could catch them.",
      owns: "Composing every environment-backed settings model into one typed object, and enforcing the validations that depend on more than one of them — notably that an issued token lifetime cannot exceed the maximum age the validator will accept, and that production cannot run with development-only values.",
      forbidden: "Declaring fields of its own, and logging or echoing any secret value it holds. Field definitions belong to the focused models; this layer only relates them.",
      receives: "Process environment variables and an optional .env file, read once.",
      validated: "Construction fails the process. A production environment that enables the guest door, keeps a local CORS origin, or uses a placeholder signing key cannot produce a settings object at all, so the service does not start.",
      wrong: "Every check here reads the environment the process declares about itself. A deployment that is production in every meaningful sense but is labelled staging passes all of them — the guest door opens, the placeholder key is accepted, and nothing in the system can tell the difference between a misconfigured label and a genuine staging box.",
      questions: [Q.guestDoor],
    },
    "config-loader": {
      eyebrow: "Bootstrap · static catalog",
      title: "Provider Catalog Loader",
      reveal: "Every provider file is parsed and frozen at startup, so a request-path lookup is a dictionary read.",
      owns: "Reading base configuration merged with the environment overlay, parsing every provider and cloud YAML file into frozen models, and serving preloaded providers by name.",
      forbidden: "Reading from disk on the request path, and accepting a provider file whose declared identity does not match its filename.",
      receives: "A configuration directory and the deployment environment name.",
      validated: "A missing overlay, an empty providers directory, an invalid file or an unknown implementation class all raise during startup. A lookup for a provider that was not loaded raises rather than attempting to read the file.",
      wrong: "The catalog can be entirely valid and still describe a provider nobody can use. Nothing cross-checks these files against the database catalog rows, so a tenant deployment can name a provider that has no YAML at all — and that mismatch is discovered by the first inference call, not by the loader.",
      questions: [Q.providerEnum],
    },
    "exit-stack": {
      eyebrow: "Bootstrap · resource ownership",
      title: "Owned Resource Lifecycle",
      reveal: "A closer is registered the instant a resource exists, not after the graph is complete.",
      owns: "Constructing the PostgreSQL pool, the Redis connection, the shared provider transport, the secret backend and the token-manager client, and registering each one's shutdown on an async exit stack in creation order.",
      forbidden: "Building a resource without immediately registering its closer, and leaving credential-bearing caches alive past the transports they depend on.",
      receives: "Validated settings and the exit stack owned by the application lifespan.",
      validated: "A failure at any point unwinds every resource already created, in reverse order. The provider registry's clear is registered before the transports and secret store, so cached plaintext credentials are dropped first.",
      wrong: "The stack guarantees that what it was given gets closed. It cannot know about a resource created somewhere else — a client constructed inside a request handler is invisible to it, and would leak a connection pool for the life of the process with no error anywhere.",
      questions: [],
    },
    "app-state": {
      eyebrow: "Bootstrap · access boundary",
      title: "Typed State Accessors",
      reveal: "A missing or wrong-typed resource fails with startup guidance, not an attribute error three frames later.",
      owns: "Exposing lifespan-owned resources to request code through accessors that check presence and type, and composing per-request services from those process-owned pieces without creating new pools.",
      forbidden: "Constructing connection pools, HTTP clients or secret backends during a request.",
      receives: "The request, the attribute name, and the type the caller expects.",
      validated: "Both absence and a type mismatch raise immediately with a message naming what the lifespan should have created.",
      wrong: "The check proves an object of the right type is present. It cannot prove the object is usable — a Redis connection manager that is present, correctly typed and unable to reach Redis passes this boundary cleanly, and the failure surfaces one layer deeper as a cache miss.",
      questions: [],
    },
  },

  admission: {
    "request-context": {
      eyebrow: "Admission · correlation",
      title: "Request Correlation",
      reveal: "A caller-supplied id is accepted only if it matches a strict pattern. Otherwise one is minted.",
      owns: "Resolving one correlation id per request, binding it to the logging context for the whole call, and echoing it on the response headers.",
      forbidden: "Trusting a caller-supplied identifier without validating it, and wrapping the request in an extra task, which would misbehave for long-lived streaming responses.",
      receives: "The raw ASGI scope and its headers.",
      validated: "The identifier must match a bounded character pattern before it is used; a rejected value is logged and replaced with a generated one.",
      wrong: "The id can be perfectly valid and still be useless for correlation, because a caller is free to reuse one value across unrelated requests. Nothing enforces uniqueness — the header is a hint the system honours, not a key it can trust to identify one call.",
      questions: [],
    },
    "body-limit": {
      eyebrow: "Admission · resource bound",
      title: "Request Body Limit",
      reveal: "Declared size is rejected immediately; chunked bodies are counted as they arrive.",
      owns: "Refusing a request whose Content-Length exceeds the configured ceiling, and counting bytes for chunked requests that declare no size, stopping once the same limit is crossed.",
      forbidden: "Allowing an unbounded body to be buffered before anything inspects it, and attempting to write an error after a response has already started.",
      receives: "The ASGI scope, its headers, and the receive channel it wraps.",
      validated: "A malformed or negative Content-Length is a 400; an oversized body is a 413 carrying a stable error code and the request id.",
      wrong: "For a chunked upload the rejection lands only once the limit has already been crossed, so the bytes up to that point were still read off the socket. The limit bounds memory retained, not bandwidth consumed — a client can repeatedly spend most of the ceiling before being refused.",
      questions: [],
    },
    "error-boundary": {
      eyebrow: "Admission · failure translation",
      title: "Domain Error Translation",
      reveal: "One typed error root, four maps, and status resolved through the inheritance chain.",
      owns: "Translating typed domain failures into one JSON envelope carrying detail, a machine-readable code and the request id, resolving status through the exception's own ancestry, and surfacing Retry-After when the error carries one.",
      forbidden: "Echoing rejected input values in validation errors, and returning a JSON error over a response that has already begun streaming.",
      receives: "Any exception raised by a route, a dependency or the framework.",
      validated: "Validation failures are rewritten to location, message and type only — deliberately dropping the rejected value, which for a credential-management request could be an API key. Unhandled exceptions are logged with correlation and returned as a sanitised 500.",
      wrong: "Separate maps for inference, sign-in and management exist because the same exception type deserves a different status depending on where it came from. A typed error that escapes a dependency before the route body runs resolves against the merged fallback map instead — which is usually right, and is not guaranteed to be.",
      questions: [Q.streamNoRetry],
    },
  },

  identity: {
    "jwt-validator": {
      eyebrow: "Identity · verification",
      title: "Access Token Validation",
      reveal: "Nine required claims, a bounded lifetime, and a role checked against the vocabulary before it is cast.",
      owns: "Verifying signature, algorithm, issuer and audience, requiring the full claim set, confirming the token is an access token, bounding its lifetime against the configured maximum, and returning a typed identity.",
      forbidden: "Offering a decode-without-verify path, and accepting a role string that is not in the declared vocabulary.",
      receives: "One bearer token string.",
      validated: "Verification is complete in a single operation, so no caller can decode a token and forget the second check. Library errors become 401s; a genuine signature with unusable contents also becomes a 401 rather than a 500.",
      wrong: "A token can pass every check and still be the wrong token to trust: this is symmetric signing, so any holder of the signing key — including the token manager integration in this same process — can mint one with any role it likes. Validation proves the claims were signed by something with the key, not that they were signed by an authority.",
      questions: [Q.layering],
    },
    "role-guard": {
      eyebrow: "Identity · platform authority",
      title: "Role Guard",
      reveal: "Door lists are slices of one ordered ladder, validated at import rather than at request time.",
      owns: "Comparing the authenticated caller's platform role against the roles a route admits, logging both grants and denials with the caller and the requirement.",
      forbidden: "Inventing its own role vocabulary, and deciding anything tenant-scoped — that question belongs to the authorization layer.",
      receives: "The typed identity produced by validation.",
      validated: "A guard constructed with an unknown role name raises at import time, so a typo fails the launch rather than silently locking out a route. Permitted sets are derived from the ladder, so 'this rung and above' cannot drift.",
      wrong: "A platform role is authority across the whole service, and it says nothing about one tenant. An operator badge that legitimately admits read access everywhere is still, from a single tenant's point of view, an outsider reading their configuration — the guard is doing exactly its job and the tenant has no say in it.",
      questions: [Q.layering],
    },
    "token-issuer": {
      eyebrow: "Identity · issuance",
      title: "Token Issuer",
      reveal: "Issuance builds precisely the claim set validation demands, from the same settings object.",
      owns: "Signing one access token carrying a subject and platform role, and returning its time window so a client can schedule re-authentication without parsing a JWT.",
      forbidden: "Inventing claims the validator does not require, and drawing its signing parameters from anywhere but the shared settings object.",
      receives: "An existing user id and a platform role, from the sign-in service.",
      validated: "Token lifetime derives from a setting the composition root refuses to let exceed the validator's maximum age, so the two halves cannot drift apart without a startup check or a test failing first.",
      wrong: "Keeping issuance and verification in one codebase makes drift a compile-and-test problem, and makes compromise a single-key problem. There is no rotation mechanism and no key id in the header, so replacing the signing key invalidates every live session at once rather than rolling over.",
      questions: [Q.guestDoor],
    },
  },

  authorization: {
    "four-gates": {
      eyebrow: "Authorization · source of truth",
      title: "The Four Gates",
      reveal: "Tenant, membership, deployment, entitlement — in order, and the first failure is the one you hear about.",
      owns: "Running the ordered checks against PostgreSQL and raising a distinct typed error for each: tenant missing or not in an active state, membership absent or holding a role not permitted to run inference, deployment missing or inactive, and no active entitlement for the exact route.",
      forbidden: "Continuing past a failed gate, substituting a different deployment or entitlement, and returning a partial context.",
      receives: "The tenant id and deployment key from request headers, and the authenticated identity.",
      validated: "Each gate reads its own source of truth through a dedicated persistence class, and the final context is a frozen model carrying every identifier the rest of the request needs, so nothing downstream re-verifies anything.",
      wrong: "The gates prove this user may use this deployment. They say nothing about whether the credential behind it still works, whether the provider will accept the model, or whether the tenant has any capacity left — three separate refusals that arrive much later, after the caller has been told they are authorized.",
      questions: [Q.cacheStaleness],
    },
    "grant-cache": {
      eyebrow: "Authorization · fast path",
      title: "Authorization Grant Cache",
      reveal: "One round trip fetches the cached answer and all four version markers it depended on.",
      owns: "Reading a cached grant together with its dependency markers, discarding it when any marker has moved, and deleting an entry that is already known to be unusable.",
      forbidden: "Serving a grant whose recorded versions differ from the ones observed now, and treating a corrupt payload as anything other than a miss.",
      receives: "A tenant, a user and a deployment key.",
      validated: "A decode failure or a version mismatch is a miss, not an error — the request simply runs the four gates. A corrupt version marker is repaired with a fresh value rather than trusted.",
      wrong: "The cache is correct about its four declared dependencies and blind to everything else. A provider catalog row deactivated in the management API is not one of the four, so a grant that names that provider stays valid here — the refusal happens one stage later, in routing, which is the layer that actually reads it.",
      questions: [Q.cacheStaleness, Q.redisPolicy],
    },
    "version-invalidation": {
      eyebrow: "Authorization · coherence",
      title: "Version Markers",
      reveal: "Revocation replaces a marker. It never has to find the grants that depended on it.",
      owns: "Advancing one marker per invalidation scope — tenant, membership, deployment or exact route — and storing a new grant only when every marker still matches what was observed during the checks.",
      forbidden: "Completing an invalidation that the cache refused to accept, and writing a grant without comparing every dependency first.",
      receives: "The scope identifiers of whatever the management layer just changed.",
      validated: "The store is a single atomic compare-and-set across the grant key and all four markers, closing the window between the gates passing and the answer being written. A refused marker write raises a typed error that becomes a 503.",
      wrong: "The scheme makes stale grants impossible and makes Redis a correctness dependency for the management path. An invalidation that cannot be written fails the management operation — which is the safe direction, and means an administrator cannot revoke access during a cache outage at all.",
      questions: [Q.redisPolicy, Q.cacheStaleness],
    },
  },

  routing: {
    "live-reads": {
      eyebrow: "Routing · freshness",
      title: "Uncached Policy Reads",
      reveal: "The two facts a revocation changes are read fresh, every request, on purpose.",
      owns: "Reading current tenant policy and the exact authorized entitlement from PostgreSQL with no cache in front of either, and converting untrusted row values into frozen models through explicit validation.",
      forbidden: "Caching either read, and searching for an alternative entitlement when the authorized one is unavailable.",
      receives: "The resolution request built from the authorization context.",
      validated: "Every value crosses a Pydantic boundary in a mapper before it can influence route selection, so schema drift fails loudly at conversion rather than producing a subtly wrong route.",
      wrong: "Freshness here is per request, not per token. A tenant suspended mid-stream keeps streaming to completion, because the check happens once on the way in — the design guarantees the next request is refused, not that the current one stops.",
      questions: [Q.whyReread],
    },
    "capability-check": {
      eyebrow: "Routing · policy",
      title: "Allow-list And Capability",
      reveal: "A tenant's provider allow-list and the model's declared capabilities both have to agree before a call is made.",
      owns: "Enforcing the tenant's provider allow-list against the entitlement's provider, resolving the provider from the startup-validated catalog, and requiring the model to declare the capability matching this operation.",
      forbidden: "Falling back to a different model when the requested capability is absent, and reading from disk to find a provider that was not preloaded.",
      receives: "The tenant policy, the entitlement, and the operation being attempted.",
      validated: "An absent allow-list means all providers are permitted, a present one restricts to the named set. An unknown provider is a configuration error; an unsupported operation is a 422 naming the provider, model and operation.",
      wrong: "The capability declared in YAML is a claim about the model, not a contract with the vendor. A model whose provider silently drops an endpoint still passes this check and fails at the provider call — and a newly added vendor capability is unavailable until someone edits the file.",
      questions: [Q.providerEnum],
    },
    "route-fingerprint": {
      eyebrow: "Routing · identity",
      title: "Route Fingerprint",
      reveal: "A deterministic hash of the whole grant — so any change that would build a different adapter changes the key.",
      owns: "Resolving every default into concrete values once — endpoint, timeout, temperature, output ceiling, headers, quota key — and computing a stable SHA-256 over the deployment key and the serialised entitlement.",
      forbidden: "Leaving a default unresolved for a downstream layer to re-derive, and carrying the credential itself rather than its reference.",
      receives: "The validated entitlement, the provider catalog entry and the model spec.",
      validated: "The result is a frozen model that forbids unknown fields and rejects reassignment, so contract drift fails at construction. The hash is computed over a canonically serialised payload, making it stable across processes.",
      wrong: "The fingerprint is sensitive to every field in the entitlement, including ones that do not affect the adapter at all. An unrelated edit to provider-specific extra config evicts a perfectly good cached provider and forces a fresh credential fetch — safe, and more expensive than it needs to be.",
      questions: [Q.fingerprint],
    },
  },

  quota: {
    reservation: {
      eyebrow: "Quota · acquisition",
      title: "Capacity Reservation",
      reveal: "An estimate is sent before the call; the real counts are reconciled after it.",
      owns: "Requesting capacity for the resolved route under a short-lived internal service token, carrying the operation, the estimation input, the resolved output ceiling and the correlation context.",
      forbidden: "Proceeding when the reservation is rejected or left waiting, and assuming capacity when the token manager cannot be reached.",
      receives: "The resolved route, the caller's user id and the original request.",
      validated: "Rejection statuses and a non-acquired allocation both raise a typed quota error carrying Retry-After where the token manager supplied one; transport failures become an unavailable error rather than a silent success.",
      wrong: "The reservation is sized from an estimate of the prompt plus the configured output ceiling, and the model is free to use less. A reservation is therefore usually an over-reservation, which is the safe direction and means measured utilisation understates real headroom.",
      questions: [Q.twoServices],
    },
    "endpoint-binding": {
      eyebrow: "Quota · integrity",
      title: "Endpoint Binding",
      reveal: "Accounting and execution must name the same endpoint, or the request fails.",
      owns: "Comparing the endpoint returned with the reservation against the endpoint on the authorized route, and refusing the request when they differ.",
      forbidden: "Accepting a different endpoint from the token manager, which would account for one deployment while executing against another.",
      receives: "The reservation response and the resolved route.",
      validated: "The comparison is normalised for trailing slashes, and a mismatch raises a protocol error rather than being treated as a routing instruction.",
      wrong: "This proves the two services agree on a URL string. It cannot prove they agree on the deployment behind it — two deployment rows pointing at the same endpoint compare equal here, so the check catches a redirect and not a mix-up between two routes that happen to share a host.",
      questions: [Q.twoServices],
    },
    finalization: {
      eyebrow: "Quota · settlement",
      title: "Terminal Accounting",
      reveal: "Every reservation ends in exactly one of four states, including the one where the client vanished.",
      owns: "Releasing the reservation with the real prompt and completion counts and a terminal status: completed, failed, cancelled or disconnected.",
      forbidden: "Hiding the original provider error behind a cleanup failure, and returning success to the caller while accounting remains uncommitted on a non-streaming call.",
      receives: "The reservation handle and whatever usage the provider reported.",
      validated: "A successful non-streaming call propagates a finalization failure to the caller, because claiming success with an uncommitted ledger is worse than failing. An already-finalised reservation is treated as success rather than an error.",
      wrong: "On the streaming path the same failure can only be logged, because the headers are long gone. A token manager outage during a long stream leaves the reservation to expire on its own, and the only trace is a log line with the reservation id — which is the accounting hole the observability gap is about.",
      questions: [Q.finalizeAsymmetry, Q.quotaObservability],
    },
  },

  execution: {
    "provider-registry": {
      eyebrow: "Execution · adapter lifecycle",
      title: "Provider Registry",
      reveal: "Concurrent first requests for the same route share one construction, not one each.",
      owns: "Returning a fresh cached adapter for a route fingerprint or building one, coalescing concurrent builds behind a single in-flight task, and bounding the cache by both a TTL and an LRU ceiling.",
      forbidden: "Resolving an arbitrary import path from configuration, and retaining a provider instance indefinitely, since every instance holds plaintext credential material.",
      receives: "The resolved route.",
      validated: "The implementation class is resolved through an audited map keyed by a validated enum; anything unregistered is a configuration error. Expired entries are dropped on read, and shutdown clears the cache before the transports and secret store close.",
      wrong: "The TTL is what makes a rotated credential eventually visible, and nothing shortens it on demand. Between a rotation in the secret backend and the entry expiring, every request on that route uses the old key — successfully, until the provider revokes it, at which point the failure looks like a provider outage.",
      questions: [Q.secretsInMemory, Q.fingerprint],
    },
    "transport-factory": {
      eyebrow: "Execution · connections",
      title: "Shared Transport",
      reveal: "Every REST provider borrows one pooled client. Adapters never close what they did not open.",
      owns: "Constructing one shared HTTP client with the configured pool limits and timeouts for all REST providers, and an SDK session for AWS-based ones.",
      forbidden: "Giving each provider its own pool, retrying at the transport layer — retry policy belongs to the adapters — and handing out a client after shutdown has begun.",
      receives: "The global HTTP pool configuration, and a provider type per request for an adapter build.",
      validated: "The reference is cleared before the close is awaited, so a request racing with shutdown fails immediately rather than borrowing a pool that is halfway closed. A missing optional SDK raises an actionable error instead of producing a broken adapter.",
      wrong: "One pool for every provider means one provider's slowness consumes connections the others need. The bulkhead against that is indirect — the stream concurrency ceiling is validated at startup to be no larger than the pool — which bounds streaming but not a burst of non-streaming calls to a degraded vendor.",
      questions: [Q.breakerLocal],
    },
    "credential-resolution": {
      eyebrow: "Execution · secrets",
      title: "Credential Resolution",
      reveal: "Only auth modes that need a key fetch one. Ambient cloud identity fetches nothing.",
      owns: "Reading the plaintext credential from the secret backend for the route's reference, scoped to the tenant, and wrapping it so it is not rendered in logs or representations.",
      forbidden: "Fetching a secret for providers that authenticate through the cloud SDK's own credential chain, and materialising the plaintext anywhere but the outbound header construction.",
      receives: "The route's secret reference and tenant id.",
      validated: "The auth mode on the provider's static configuration decides whether a fetch happens at all; the value is held as a secret type and read only at the call site that builds request headers.",
      wrong: "Not rendering the secret in logs is not the same as it not being in memory. It lives in the adapter for the cache TTL, and anything with process memory access — a heap dump, a debugger, a crash reporter capturing locals — sees it regardless of how carefully the type refuses to print itself.",
      questions: [Q.secretsInMemory],
    },
    "circuit-breaker": {
      eyebrow: "Execution · resilience",
      title: "Circuit Breaker Boundary",
      reveal: "The call that trips the breaker keeps its real cause. Only later calls get the generic refusal.",
      owns: "Applying one breaker per provider to every operation, bridging generator-based streams through a guarded producer with a one-slot queue so backpressure survives, and classifying raw transport exceptions into typed domain errors.",
      forbidden: "Holding breaker state in a shared store that performs blocking calls inside the event loop, and letting a library exception type reach the service layer.",
      receives: "The provider name, the configured policy, and whatever the transport raised.",
      validated: "Names are normalised before lookup so the same provider cannot acquire two independent failure counters. A trip preserves the underlying cause, while a call arriving at an already-open circuit gets a 503 carrying the configured reset window as a retry hint.",
      wrong: "The breaker counts failures, not wrongness. A provider returning fast, well-formed, confidently incorrect completions keeps the circuit closed forever — it is a latency and error-rate instrument, and nothing in this service measures output quality at all.",
      questions: [Q.breakerLocal, Q.quotaObservability],
    },
  },

  delivery: {
    "capacity-lease": {
      eyebrow: "Delivery · admission",
      title: "Stream Capacity Lease",
      reveal: "Refusal is immediate. An open socket is never parked in a queue waiting for a slot.",
      owns: "Bounding concurrent streams per worker process and issuing a lease that releases its slot exactly once no matter which cleanup path runs first.",
      forbidden: "Queueing a request behind a full limiter, and coordinating the limit across replicas — which would put a network round trip in front of every stream.",
      receives: "A configured maximum concurrency and a retry hint.",
      validated: "Exceeding the limit raises a typed error that becomes a 503 with Retry-After. Release is guarded so competing cleanup paths cannot double-count, and negative accounting raises rather than silently drifting.",
      wrong: "The limit is per worker, so the real ceiling is the limit times the replica count — a number nobody configured directly. Scaling out raises total streaming concurrency against the provider silently, which is exactly the coordination problem the token manager exists to solve for tokens and nobody solves for connections.",
      questions: [Q.streamNoRetry],
    },
    "streaming-session": {
      eyebrow: "Delivery · lifecycle",
      title: "Streaming Session",
      reveal: "A real iterator, not a generator — so a client that leaves before the first chunk is still cleaned up.",
      owns: "Reading provider chunks, accumulating the largest cumulative usage seen, classifying every terminal path, and running cleanup once: close the provider stream, reconcile quota, release the capacity slot.",
      forbidden: "Running cleanup more than once, letting a broken provider iterator hold a capacity slot indefinitely, and coupling any of this to the SSE transport.",
      receives: "The provider chunk iterator, the capacity lease and a finalisation callback.",
      validated: "Cleanup is guarded by a lock and shielded from the disconnect cancellation so it completes rather than being cancelled halfway. Provider close is bounded by a timeout; a hang is logged instead of retaining the slot.",
      wrong: "Usage is whatever the provider chose to report, taken as the maximum of the cumulative snapshots seen. A provider that omits a usage trailer, or emits one only on a clean finish, leaves a disconnected stream reconciled with nothing — the accounting is honest about having no number, and the tokens were still spent.",
      questions: [Q.finalizeAsymmetry, Q.quotaObservability],
    },
    "sse-delivery": {
      eyebrow: "Delivery · transport",
      title: "SSE Delivery",
      reveal: "One read outstanding at a time, so a slow client stops the source instead of filling memory.",
      owns: "Adding thread identity, a monotonic sequence and the request id to every event, emitting comment heartbeats during quiet periods, and terminating with exactly one named completion event carrying a status.",
      forbidden: "Buffering ahead of the consumer, cancelling an in-flight source read when a heartbeat falls due, and leaking raw exception text into an error event.",
      receives: "A provider-neutral event iterator, the thread id and the correlation id.",
      validated: "The pending read is shielded while waiting, so a heartbeat never discards a chunk. Every exit path closes the source iterator, and an application error mapper that itself throws falls back to a generic event rather than breaking termination.",
      wrong: "The sequence numbers are monotonic within one connection and mean nothing across a reconnect — there is no resume token and no replay. A client that drops at sequence 40 and reconnects starts a new stream at one, with no way to discover what it missed.",
      questions: [Q.streamNoRetry],
    },
  },

  controlplane: {
    "scoped-crud": {
      eyebrow: "Control plane · authorization",
      title: "Tenant-Scoped Writes",
      reveal: "Platform badges are checked first, because they need no database read at all.",
      owns: "Deciding whether a caller may read or change one tenant's data — platform administrators and operators pass on their badge alone, everyone else must hold an active membership, and a write additionally requires an admin role inside that tenant.",
      forbidden: "Authorizing inference, which is a different service with different gates, and inferring tenant scope from a platform role for a mutation.",
      receives: "The tenant id in the path and the authenticated identity.",
      validated: "Every denial raises one typed error carrying the specific requirement that failed, which the API maps to a 403. Reads and writes use distinct entry points so a read path cannot accidentally admit a writer's check.",
      wrong: "A platform operator can read every tenant's configuration and the tenant has no mechanism to see that, object to it, or find it afterwards — there is no per-tenant access log for cross-tenant reads, only the service's own application logs.",
      questions: [Q.layering],
    },
    "credential-writer": {
      eyebrow: "Control plane · secrets",
      title: "Credential Write Path",
      reveal: "The value goes to the secret backend at a versioned path; the row gets a pointer.",
      owns: "Validating a submitted credential against the provider's declared auth mode, writing it to a fresh versioned path through the write-only backend, and returning the reference the row will store.",
      forbidden: "Persisting credential material in any database column, accepting a credential shape the provider layer cannot use, and silently dropping a credential when no writer is configured.",
      receives: "A typed credential, an ownership path, the tenant id and the provider's auth mode.",
      validated: "A credential whose auth mode disagrees with the provider is refused as a validation error. A missing writer with a real credential present is a server misconfiguration and raises. Backend errors are re-raised as a typed unavailable error carrying only the exception type, never the URL or path.",
      wrong: "Rotation writes a new version and updates the row, and nothing revokes the old one. Previous versions remain readable in the secret backend by anything holding the read identity, so a rotation limits future exposure without retracting past exposure.",
      questions: [Q.secretsInMemory],
    },
    "cache-invalidation": {
      eyebrow: "Control plane · coherence",
      title: "Write-Side Invalidation",
      reveal: "A management write is not finished until the grants that depended on it are dead.",
      owns: "Advancing the version markers for exactly the scope a mutation touched — the tenant, one membership, one deployment, or one exact route — as the final step of every mutating operation.",
      forbidden: "Returning success on a mutation whose invalidation was refused, and invalidating a wider scope than the change requires.",
      receives: "The identifiers of the changed record, read before the change where the key itself is being updated.",
      validated: "A refused marker write raises a typed error that the API maps to 503, so an administrator learns the change is not yet safe rather than believing it landed.",
      wrong: "Invalidation is by scope, not by consequence. Deactivating a provider catalog row is not one of the four scopes, so grants naming that provider survive here untouched — they fail one stage later in routing, which is correct behaviour arrived at by accident rather than by this component's design.",
      questions: [Q.cacheStaleness],
    },
  },

  secrets: {
    "credential-shapes": {
      eyebrow: "Secrets · input contract",
      title: "Accepted Credential Shapes",
      reveal: "Only the credential types the provider layer can actually use are accepted at all.",
      owns: "Defining the credential shapes the management API accepts — a single API key presented as a bearer token or a custom header — and refusing everything else.",
      forbidden: "Accepting OAuth exchanges or arbitrary custom fields, which would create records that can never authenticate at inference time.",
      receives: "The credential block of a deployment or entitlement request.",
      validated: "The key is held as a secret type with a length bound, redacted from representations, and must match the auth mode declared by the provider catalog row it is being attached to.",
      wrong: "Refusing unimplemented shapes keeps the database honest and also makes the product narrower than the provider catalog suggests: a vendor requiring OAuth cannot be onboarded through this API at all, and the only signal is a validation error at credential entry.",
      questions: [],
    },
    "vault-write": {
      eyebrow: "Secrets · write identity",
      title: "Write-Only Vault Adapter",
      reveal: "A class with no read method. The compromise of a management endpoint cannot retrieve a credential.",
      owns: "Storing a credential payload at a namespaced key-value path using an identity whose policy grants create and update, and returning the canonical reference.",
      forbidden: "Reading a secret back, deleting or listing — rotation writes a new version rather than removing history.",
      receives: "A validated path, the tenant id for audit context, and the credential fields.",
      validated: "Every path segment is normalised and checked before it becomes a URL. An empty payload is refused. Permission denials and rejections become distinct, actionable errors that never echo the response body.",
      wrong: "Least privilege is enforced by the Vault policy, not by this class — the class is the readable expression of it. An operator who grants the write account read capability changes the security property without changing a line of code here, and nothing in the service would notice.",
      questions: [],
    },
    "vault-read": {
      eyebrow: "Secrets · read identity",
      title: "Read-Only Vault Adapter",
      reveal: "The request path can read its own namespace and can never write to it.",
      owns: "Resolving one reference to a validated credential field, translating permission and not-found responses into precise domain errors, and treating a malformed success as backend unavailability.",
      forbidden: "Writing or overwriting any secret, and including secret values or response bodies in logs or error messages.",
      receives: "A secret reference from the resolved route, and the tenant id.",
      validated: "The shared client owns login, lease-aware token refresh, and bounded retry with full jitter — retrying only transport failures, timeouts and 5xx, while 403 and 404 return to this adapter because they carry domain meaning. A rejected token is refreshed once; a second denial is a real policy failure.",
      wrong: "The adapter proves it received a usable value from Vault. It cannot prove the value is the current one for that provider — a credential rotated at the vendor but never updated here resolves perfectly and fails at the provider call, which surfaces as an authentication error attributed to the provider rather than to configuration.",
      questions: [Q.secretsInMemory],
    },
  },

  persistence: {
    "session-provider": {
      eyebrow: "Persistence · pooling",
      title: "Session Provider",
      reveal: "One pool per worker; each unit of work gets its own session and its own transaction.",
      owns: "Building the connection pool from validated settings, handing out short-lived sessions whose transactions commit on success and roll back on failure, and answering the readiness probe.",
      forbidden: "Sharing a session across concurrent operations, logging the connection URL, and serving a session after the pool has been closed.",
      receives: "Validated database configuration.",
      validated: "Statement timeouts are applied server-side and mirrored by a client-side command timeout for the case where the network itself stops responding. Connections are pre-pinged and recycled, and use after close fails loudly.",
      wrong: "Pool sizing is per worker, so the real connection count against PostgreSQL is the configured size times the number of workers times the number of replicas — a number that is never stated anywhere in configuration and is discovered when the database refuses new connections.",
      questions: [Q.rawSql],
    },
    "base-persistence": {
      eyebrow: "Persistence · query safety",
      title: "Query Construction",
      reveal: "The one place that builds SQL dynamically validates every identifier and refuses a free-form WHERE.",
      owns: "Executing named parameterised statements, and building partial UPDATE statements from only the fields that changed.",
      forbidden: "Interpolating any caller-supplied value into a statement, accepting a WHERE clause that is not parameterised equality joined by AND, and allowing update and where bindings to collide.",
      receives: "A table name, the changed fields, a where clause with its bindings, and an explicit returning projection.",
      validated: "Table and column names must match a plain-identifier pattern, the where clause must match a strict equality pattern, an empty update set raises, and updated_at is always appended so no caller can forget it.",
      wrong: "Everything here is checked at runtime against a pattern, and nothing is checked against the actual schema. A column that exists in the tuple and not in the table passes every validation in this class and fails inside the transaction, where the error names a database object rather than the code that asked for it.",
      questions: [Q.rawSql],
    },
    "safe-projections": {
      eyebrow: "Persistence · exposure",
      title: "Explicit Projections",
      reveal: "No star selects, and secret-bearing fields are stripped again on the way out.",
      owns: "Returning only the columns named in a safe projection tuple, and scrubbing secret-named keys — including nested ones — from rows before they leave the service layer.",
      forbidden: "Returning a credential reference to an API caller, and relying on a single layer to prevent that.",
      receives: "A persistence row, or a list of them.",
      validated: "Redaction walks nested mappings and sequences, so burying a credential one level inside a JSON column does not defeat it. The persistence layer exposes the reference only through a dedicated method, never through a list projection.",
      wrong: "Both layers work from a hand-maintained list of names that look like secrets. A field named something the list does not anticipate is returned in full by a projection that was written before it existed — the defence is a convention enforced twice, not a type the compiler understands.",
      questions: [Q.rawSql],
    },
    "schema-invariants": {
      eyebrow: "Persistence · constraints",
      title: "Schema Invariants",
      reveal: "Several rules that look like service logic are actually indexes and checks.",
      owns: "Enforcing at most one active entitlement per exact route, one default deployment per tenant and provider, deployment keys in a format routing can rely on, and referential integrity across composite provider and model keys.",
      forbidden: "Deferring these rules to application code that a direct database write could bypass.",
      receives: "Every insert and update, whatever issued it.",
      validated: "Partial unique indexes scope uniqueness to active rows, check constraints bound statuses and sampling parameters, and a dedicated view gives the sibling service a stable read shape rather than a join it would have to maintain.",
      wrong: "Constraints hold the shape and say nothing about the meaning. A deployment row can satisfy every check while naming an endpoint that does not exist, a capacity limit nobody sized, and a provider with no runtime configuration file — all perfectly valid, and all discovered by the first request that tries to use it.",
      questions: [Q.rawSql, Q.providerEnum],
    },
  },

  topology: {},
};

const RAIL_ORDER = [
  "bootstrap",
  "admission",
  "identity",
  "authorization",
  "routing",
  "quota",
  "execution",
  "delivery",
];

/* No per-stage React Flow low-level diagrams in this explorer: level 2 renders
   the mechanism ladder, which is what carries the clickable level-3 component
   contracts. The consolidated end-to-end canvas lives in full-flow.js. The
   engine checks this object before rendering a stage, so it must exist. */
const DIAGRAM_STAGES = {};
