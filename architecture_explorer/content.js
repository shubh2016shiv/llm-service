/* ============================================================
   llm_services — content model
   ============================================================
   Everything the engine renders lives here: the pressure probes (Q),
   one startup prerequisite and seven request stages
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
    rail: { index: "01", tag: "Bootstrap", name: "Application Bootstrap", desc: "Startup prerequisite: build shared resources before requests" },
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
    rail: { index: "02", tag: "Receive", name: "Receive & Validate", desc: "Correlate and bound each request at the ASGI edge" },
    eyebrow: "02 · Receive and validate",
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
    rail: { index: "03", tag: "Authenticate", name: "Authentication", desc: "Verify the caller's token and platform identity" },
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
    rail: { index: "04", tag: "Authorize", name: "Inference Authorization", desc: "Check tenant, membership, deployment and entitlement" },
    eyebrow: "04 · Inference authorization",
    title: "Four gates, in a fixed order, before routing can begin.",
    reveal: "Every gate opens or nothing runs. There is no partial pass and no fallback to a weaker grant.",
    summary:
      "The caller's tenant must exist and be active; the caller must be an active member holding a role permitted to run inference; the named deployment must exist and be active; and there must be an active entitlement for that exact tenant, user, deployment, provider and model. When all four open, a frozen access context carries the identifiers routing needs. Live wiring reads the source of truth on every request; the grant-cache backend is disabled.",
    decisionHead: "Check the source of truth before building the access context",
    decisionBody:
      "Each request checks the active tenant, membership and role, deployment, and exact entitlement in order. The access context is frozen after these checks. The cache implementation remains in the codebase, but live dependency wiring supplies no backend because write-side invalidation is not transactionally coupled to the database changes.",
    failure: "A revoked user continuing to reach a model because a cached authorization decision outlived its underlying database state.",
    tradeoff: "Reading the four gates for each inference request costs database work, but avoids relying on non-transactional cache invalidation for access control.",
    talk: "This stage decides whether this caller may use this exact tenant and deployment. Tenant roles are checked here, after token authentication.",
    questions: [Q.whyReread],
    steps: [
      { label: "Read the current records", meta: "Live wiring bypasses the grant-cache backend", kind: "deterministic", component: "grant-cache" },
      { label: "Run the four gates", meta: "Tenant, membership and role, deployment, exact entitlement — in order, failing at the first", kind: "gate", component: "four-gates" },
      { label: "Build the frozen pass", meta: "Tenant, user, deployment, provider, model, tenant role, entitlement — all resolved", kind: "deterministic" },
      { label: "Pass the context to routing", meta: "The frozen identifiers bind routing to the exact approved entitlement", kind: "deterministic" },
    ],
  },

  /* ========== 05 — routing ========== */
  routing: {
    rail: { index: "05", tag: "Resolve", name: "Route Resolution", desc: "Turn an approved grant into an execution plan" },
    eyebrow: "05 · Route resolution",
    title: "Answer a different question: not may they, but can this grant execute this operation right now.",
    reveal: "The resolver re-reads the approved entitlement and never searches for a substitute.",
    summary:
      "Tenant policy and the exact authorized entitlement are re-read from PostgreSQL without a cache, the tenant's provider allow-list is applied, and the provider and model are looked up in the startup-validated catalog and checked for the requested capability. What comes out is a frozen route with every default already resolved — endpoint, timeout, temperature, output ceiling, secret reference, quota key and a deterministic fingerprint.",
    decisionHead: "Uncached reads for the two facts a revocation changes",
    decisionBody:
      "Authorization reads its four gates from the database on every request because the live grant-cache backend is disabled. Routing re-reads current tenant policy and the exact approved entitlement, then refuses any mismatch rather than substituting a different grant.",
    failure: "A suspended tenant or revoked entitlement continuing to serve because a cached routing decision lagged, and an embed-only model being handed a chat request because nobody checked the capability before dialling out.",
    tradeoff: "Two PostgreSQL reads on every inference request that a fully cached design would avoid, and a resolver that will fail a request rather than fall back to the tenant's default deployment when the named grant is unavailable.",
    talk: "Authorization decides whether the caller has this exact grant; routing checks whether it can execute the requested operation with current tenant policy and provider capabilities. Both checks currently read live database state.",
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
    rail: { index: "06", tag: "Reserve", name: "Capacity Reservation", desc: "Reserve token quota and, for streams, a worker slot" },
    eyebrow: "06 · Capacity reservation",
    title: "Reserve token quota and, for a stream, a worker slot before provider execution.",
    reveal: "The reservation is checked against the route that was authorized. A mismatch is a protocol error, not a redirect.",
    summary:
      "For streaming chat, a per-worker limiter first grants a slot or refuses with 503 and Retry-After. Before any provider call, the service asks a separate token manager for capacity on the exact route, sending an estimate derived from the request and the resolved output ceiling. A rejected or queued token allocation becomes a 429. When the call finishes — completed, failed, cancelled or disconnected — the reservation is finalised with reported usage.",
    decisionHead: "Central accounting, and a client that refuses to be re-routed by it",
    decisionBody:
      "Concurrency and token ceilings belong to a provider endpoint, not to a replica, so the count has to live in one place. But the token manager is an accounting authority, not a routing authority: if the reservation comes back naming a different endpoint than the authorized route, the client raises rather than executing — because accounting for one deployment while calling another is worse than failing.",
    failure: "Replicas each counting their own usage and collectively blowing a provider's ceiling, and a reservation that silently redirects execution to an endpoint the caller was never authorized for.",
    tradeoff: "A synchronous network round trip on the critical path of every inference call, and a hard availability dependency: when the token manager is unreachable the client raises rather than assuming capacity, which is correct and is still a reduction in availability.",
    talk: "This is the one place the service deliberately depends on something outside itself mid-request. The argument for it is that a shared ceiling counted locally is not a ceiling at all.",
    questions: [Q.twoServices, Q.finalizeAsymmetry, Q.quotaObservability],
    steps: [
      { label: "Admit a stream, if requested", meta: "Acquire a per-worker slot before reserving token quota", kind: "gate", component: "capacity-lease" },
      { label: "Estimate and request", meta: "Operation-specific input plus the resolved output ceiling, under a short-lived service token", kind: "deterministic", component: "reservation" },
      { label: "Verify the endpoint", meta: "A reserved endpoint that differs from the authorized route is refused", kind: "gate", component: "endpoint-binding" },
      { label: "Execute under the reservation", meta: "Provider work happens inside the window the reservation opened", kind: "deterministic" },
    ],
  },

  /* ========== 07 — execution ========== */
  execution: {
    rail: { index: "07", tag: "Execute", name: "Provider Execution", desc: "Call the selected provider through one adapter contract" },
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
    rail: { index: "08", tag: "Deliver & Settle", name: "Delivery & Settlement", desc: "Return JSON or SSE and finalize usage on every path" },
    eyebrow: "08 · Delivery and settlement",
    title: "Deliver JSON or SSE, and finalize the reservation on every terminal path.",
    reveal: "Capacity, credential and adapter are all acquired eagerly — so a failure becomes an HTTP error, not a broken stream.",
    summary:
      "Non-streaming chat, embedding and rerank return JSON after token usage is finalized. Streaming chat returns SSE: its worker slot and quota reservation were acquired before provider execution, and the provider stream is prepared before response headers leave. A stateful session owns disconnect, cancellation and completion cleanup, while the SSE layer adds sequence, thread identity and heartbeats.",
    decisionHead: "An explicit iterator, because an async generator cannot clean up what it never started",
    decisionBody:
      "If the client disconnects before the first chunk, a never-started generator has no cleanup hook at all. A real iterator object has an aclose, so the session can close the provider stream, reconcile usage and release the capacity slot on a path where the generator body never ran. Cleanup is shielded from the disconnect cancellation so it completes rather than being cancelled halfway.",
    failure: "A disconnected client leaking a provider connection, a reservation and a concurrency slot at once — and a slow consumer causing unbounded buffering because the source was read faster than it was written.",
    tradeoff: "Backpressure is bought by reading at most one event ahead, which means the source is not being drained while the socket is slow — correct for memory, and it makes the provider stream's own timeouts the thing that governs a stalled client. Post-header failures can only be reported inside the stream.",
    talk: "The design question in streaming is not how to send tokens, it is what happens when the person on the other end closes the tab. Everything here is arranged so that answer is the same as every other ending.",
    questions: [Q.streamNoRetry, Q.finalizeAsymmetry, Q.quotaObservability],
    steps: [
      { label: "Return a JSON response", meta: "Non-streaming chat, embed and rerank finalize usage before returning", kind: "deterministic" },
      { label: "Prepare SSE eagerly", meta: "Streaming chat acquires resources before response headers leave", kind: "gate" },
      { label: "Own the lifecycle", meta: "completed, failed, cancelled, disconnected — each classified, cleanup run once", kind: "deterministic", component: "streaming-session" },
      { label: "Frame and deliver", meta: "Thread id, monotonic sequence, heartbeats, one terminal event", kind: "deterministic", component: "sse-delivery" },
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

/* Stage diagrams replace the ladder when the implementation has been traced. */
const DIAGRAM_STAGES = {
  bootstrap: {
    engine: "reactflow",
    reactFlowMount: "mountBootstrapReactFlow",
    eyebrow: "01 · Application bootstrap — low-level design",
    title: "How one worker becomes ready to serve requests",
    intro: "This traces create_app and its startup function, called lifespan: check settings, build the FastAPI app, load the AI provider config files, build shared connections, then assemble the request-handling service. If anything fails partway through, everything already opened gets closed.",
    label: "Low-level design",
    flowTitle: "Application startup and cleanup",
    rule: "The app will not accept any requests until its startup function finishes. A built-in Python tool called AsyncExitStack keeps a list of cleanup steps and runs them, whether startup failed or the server is shutting down normally.",
    notes: [
      ["One copy per worker process, not per request", "Uvicorn (the server that runs this app) starts a separate worker process for each CPU core and calls create_app once per worker. Everything built here, the database pool, the Redis connection, the HTTP client, belongs to that one worker process. It is not rebuilt for every incoming request."],
      ["Every resource gets a cleanup step", "As soon as something is created, the code immediately tells AsyncExitStack how to close it. For example, Redis registers its own shutdown function before it even tries to connect, so a failed first connection attempt still gets cleaned up correctly."],
      ["Order is enforced in code", "Config files are read and checked first, before any database or network connection opens. When cleanup runs, it happens in the reverse order things were created: the last thing opened is the first thing closed, the same way you would unpack and repack a stack of boxes."],
    ],
    details: {
      SETTINGS: { eyebrow: "Step 1 of 8", title: "Load configuration from environment variables", body: "Every time the server starts a worker, the first thing it does is gather its configuration from environment variables: values set outside the code, by whoever deployed it.", sections: [
        { title: "What it reads", items: [
          "Which environment this is: development, staging or production",
          "The database address, and the address of the fast in-memory store (Redis)",
          "The web address of the separate service that tracks LLM token quotas",
          "The secret key used to sign and check login tokens, and which websites may call this API",
          "How long a login token stays valid, and how much detail goes into the logs",
        ] },
        { title: "What it checks", items: [
          "Settings are checked against each other, not just one by one. A login token cannot be issued for longer than the maximum age the server will later accept, otherwise every sign-in would appear to work and every request after it would fail for no visible reason",
          "In production, unsafe development defaults are refused outright: an unauthenticated back-door login, a placeholder signing key, or a local-only web address left in the allowed list",
        ] },
        { title: "If something is wrong", items: [
          "The worker refuses to start at all, rather than starting in a broken state and failing later on a real request",
        ] },
      ] },
      API: { eyebrow: "Step 2 of 8", title: "Build the FastAPI application", body: "With the configuration in hand, the server builds the application object that will actually receive requests. FastAPI is the web framework it is built on.", sections: [
        { title: "What it sets up", items: [
          "The settings are attached to the application, so every part of it can read them later",
          "Middleware is added: code that runs on every single request, before and after the request itself",
          "The route groups are registered: sign-in, inference (calling an AI model), administrative management, and health checks",
        ] },
        { title: "What the middleware does", items: [
          "Turns an unexpected crash into a clean error response instead of a raw stack trace",
          "Rejects a request whose body is too large, so one oversized upload cannot tie up memory",
          "Controls which other websites may call this API directly from a visitor browser, so a third-party site cannot quietly use someone else's logged-in session",
        ] },
      ] },
      LIFESPAN: { eyebrow: "Step 3 of 8", title: "Run the startup-and-shutdown routine", body: "Most applications need setup before they can start and cleanup when they stop, like a shop unlocking the doors and turning on the lights before opening, then locking up at closing. FastAPI gives the application one routine for both halves, and calls it lifespan.", sections: [
        { title: "How it works", items: [
          "FastAPI calls this one routine twice: once to set everything up before traffic starts, and once to close everything down when the server stops",
          "It begins by opening an empty list of things to undo",
          "Each time a resource is opened in the steps below, a note is added to that list saying how to close it",
        ] },
        { title: "Why it matters", items: [
          "Nothing that gets opened is ever left open and forgotten, whether startup fails halfway through or the server shuts down normally much later",
        ] },
      ] },
      CATALOG: { eyebrow: "Step 4 of 8", title: "Check the AI provider configuration files", body: "Before opening a single connection, the server reads its own configuration files, its instruction manual, and checks them against each other.", sections: [
        { title: "Input: what it reads", items: [
          "One file per AI provider. OpenAI has its own file, Anthropic has its own, and so on. Each describes that provider name, address and login details",
          "One shared file holding the connection limit: how many internet connections the server may use at once",
          "The same shared file retry rule: how many times to retry a call that fails",
          "The same shared file safety list: which providers get a circuit breaker, the same idea as the one in your house. If a provider keeps failing repeatedly, the server stops calling it for a short time, so one broken provider does not slow down every request",
        ] },
        { title: "Validation: what it checks", items: [
          "Can every provider file actually be read? No missing or damaged files",
          "Does every provider named in the safety list have its own provider file? If the list says openai but no OpenAI file exists, that is a mistake",
          "Is the streaming limit reasonable? The number of live streams allowed at once cannot be bigger than the number of available connections",
        ] },
        { title: "Output: what happens", items: [
          "All checks pass: the server starts normally",
          "Any check fails: the server refuses to start and reports what went wrong",
          "Like a pilot pre-flight checklist: confirm the paperwork, confirm every safety feature listed actually exists on the plane, confirm the plane is not overbooked. If anything is off, the plane stays on the ground",
        ] },
      ] },
      RESOURCES: { eyebrow: "Step 5 of 8", title: "Open the connections every request will share", body: "Now that the configuration files have passed their checks, the server connects to everything it depends on, using the addresses it read in step 1.", sections: [
        { title: "Five things it builds, once", items: [
          "Database pool: ready-to-use database connections, so no request has to open its own",
          "Redis: fast temporary storage, used here to lock out accounts after too many failed sign-in attempts",
          "AI network client: the one connection used to call OpenAI, Anthropic and the other providers",
          "API-key store: the safe place holding each provider credentials",
          "Quota service client: tracks how many AI tokens each customer has used",
        ] },
        { title: "What has to work", items: [
          "Redis and the database must be reachable, and the secret store must be properly set up, or the server refuses to start",
        ] },
        { title: "Why it is built this way", items: [
          "Like a kitchen plugging in its oven, fridge, grill, dishwasher and card machine before opening for the day. If one will not turn on, the restaurant does not open. Every order afterward uses the same five appliances",
        ] },
      ] },
      WIRE: { eyebrow: "Step 6 of 8", title: "Set up routing, failure protection and connection reuse", body: "This step assembles the decision-making that sits between an incoming request and the connections built in step 5.", sections: [
        { title: "What it uses", items: [
          "The five connections built in step 5, and the settings validated in step 4",
        ] },
        { title: "What it builds, once per worker", items: [
          "Reads from the database which AI providers (OpenAI, Anthropic, and so on), models and deployments this customer (a tenant) is allowed to use",
          "Picks the exact provider, model and network address to send this request to, based on that",
          "Keeps one failure counter per AI provider. After repeated failures, it stops sending that provider more requests for a short time",
          "Keeps a ready-to-use connection, including its API key, for each provider and model already used, so it is not rebuilt per request",
          "Caps how many streaming requests this worker processes at once. Once the cap is hit, a new request gets an immediate try again later response instead of waiting",
        ] },
        { title: "What happens on a failure", items: [
          "Not allowed to use that provider or model: rejected immediately, before any network call",
          "That provider has failed too many times recently: rejected immediately, without attempting the call",
          "Too many streaming requests already running: rejected immediately with a retry hint, instead of being queued",
        ] },
      ] },
      READY: { eyebrow: "Step 7 of 8", title: "Start serving requests", body: "Once every connection and decision step above exists, startup pauses here and the server begins accepting real traffic.", sections: [
        { title: "What this means", items: [
          "Every request from this point forward reuses the same database pool, Redis connection and network clients built during startup",
          "No individual request ever opens a brand new connection of its own, which would be far too slow to do on every request",
        ] },
      ] },
      CLOSE: { eyebrow: "Step 8 of 8", title: "Close resources in reverse order", body: "This runs either when startup fails partway through, or when the server is shutting down normally after serving traffic.", sections: [
        { title: "What happens", items: [
          "Every note added to the list from step 3 is now used, in the reverse order it was added: the most recently opened thing is the first one closed",
          "Cached provider connections are cleared first, because they hold API keys in memory, before the network client and API-key store underneath them are closed",
        ] },
        { title: "Why reverse order", items: [
          "Nothing is ever closed while something else still depends on it, and nothing is left holding a credential after the thing that needed it is already gone",
        ] },
      ] },
    },
  },
  admission: {
    engine: "reactflow",
    reactFlowMount: "mountAdmissionReactFlow",
    eyebrow: "02 · Receive and validate — low-level design",
    title: "How an HTTP request reaches a validated route",
    intro: "Follow one request through the ASGI boundary: give it a safe request ID, apply response and error wrappers, bound the body, then enter FastAPI route parsing and dependency resolution.",
    label: "Low-level design",
    flowTitle: "Receive, bound and validate a request",
    rule: "A rejected request keeps its safe X-Request-ID. The body limit runs before FastAPI parses the body; validation errors never echo rejected input values.",
    notes: [
      ["Actual wrapper order", "RequestContext is outermost, followed by CORS, the unhandled-error safety net, and RequestBodyLimit. FastAPI routing and validation run inside them."],
      ["Body limits have two checks", "Content-Length can be rejected immediately. Requests without a reliable declared size are counted as chunks arrive."],
      ["After a response starts", "An unexpected error cannot become a fresh JSON response after headers are sent. The safety net re-raises it rather than mixing formats."],
      ["FastAPI dependencies", "Declared headers and other dependency parameters are validated during dependency resolution. The diagram does not claim every validation check finishes before authentication."],
    ],
    details: {
      ARRIVE: { eyebrow: "Input", title: "Receive an HTTP request", body: "Every request starts here, at the boundary between the web server (Uvicorn, from the bootstrap diagram) and this application. The two communicate through a standard interface called ASGI. It hands the application a description of the incoming request, for example: the method (POST), the path (/api/v1/llm/chat), and its headers (Authorization for who's calling, X-Tenant-ID and X-Deployment-Key for which customer and deployment, X-Request-ID for tracking this one call). It also gives the application a way to read the request's body as it arrives rather than only once it's fully received, and a way to send the response back the same way, piece by piece. That piece-by-piece sending is what lets a response be streamed out later in this flow, instead of only ever being sent as one finished block.", sections: [
        { title: "What it receives", items: [
          "A description of this one request: its method (e.g. POST), path (e.g. /api/v1/llm/chat) and headers (e.g. Authorization, X-Tenant-ID, X-Request-ID)",
          "A way to read the request body as it arrives, instead of only after it's fully received",
          "A way to send the response back, which later steps use to stream output",
        ] },
        { title: "What it does not do", items: [
          "It does not touch anything that isn't an ordinary web request. A different kind of connection, such as a live two-way socket, is passed straight through untouched",
        ] },
      ] },
      ID: { eyebrow: "Correlation", title: "Keep or create a safe request ID", body: "Every request gets one tracking number, used to follow that specific request through the logs, which matters most when something goes wrong and you need to find exactly what happened for that one call. If the caller already sent a tracking number in a header called X-Request-ID, the server checks that it's actually safe to reuse: only letters, digits, and a handful of harmless punctuation marks like - _ . : and /, nothing that could break a log line or be used to inject something unexpected. A value like checkout-2024-05-01:00042 passes; something containing a space, a quote, or a stray symbol does not. If the caller didn't send one, or sent something that fails that check, the server makes up a new one itself: a UUID, a long, effectively-unique code it generates on the spot, something like 3b2c9f10-4e21-4b8a-9c3d-1a2b3c4d5e6f.", sections: [
        { title: "What gets checked", items: [
          "Accepted: letters, digits, and the characters - _ . : /, for example checkout-2024-05-01:00042",
          "Rejected: anything with a space, a quote, or any other character outside that list, a new ID is generated instead",
        ] },
        { title: "What happens with it", items: [
          "It's attached to every log line written while this request is being handled",
          "It's sent back to the caller in the response, so they can match their request to what the server logged for it",
          "A rejected caller-supplied value is logged as a warning, but the request still proceeds with the newly generated ID instead of being refused",
        ] },
      ] },
      WRAP: { eyebrow: "Response boundary", title: "Apply CORS and the error safety net", body: "Two protections wrap around every route here. The first is CORS, a browser rule controlling which other websites' JavaScript is allowed to call this API directly from someone's browser. Without it, any website could embed a script that quietly calls this API using a visitor's already-logged-in session. This service only allows specific, configured addresses to do that, for example http://localhost:3000 in development; a production deployment is not allowed to leave this wide open to every site (a wildcard * is rejected at startup). The second is a safety net for anything that crashes unexpectedly, a bug nothing else here was built to catch. It turns that crash into a clean, generic error response instead of letting a raw Python error leak out to the caller.", sections: [
        { title: "What CORS controls", items: [
          "Which website addresses may call this API from a browser; anything not on that list is refused by the browser itself, before the request even reaches here",
          "The allowed addresses are configured per environment, and a production deployment can't leave this set to allow everyone",
        ] },
        { title: "What the safety net does", items: [
          "Catches any crash that wasn't already turned into a proper error response elsewhere",
          "Logs the method and path of the failing request, then returns a clean, generic error instead of leaking internal detail",
        ] },
      ] },
      SIZE: { eyebrow: "Body limit", title: "Check declared and received body size", body: "Every request body (the data being sent, like a chat message) has a size limit, 10,485,760 bytes by default (10 MB, roughly a few photos' worth of data, or several million characters of text). There are two different checks, because not every request says upfront how big it is. If the caller sends a Content-Length header, declaring the size before sending any data, the server checks that number immediately: if it's not a valid non-negative number, or if it's already over the limit, the request is rejected before a single byte of the body is read. If there's no declared size (a chunked upload, where data streams in without saying how much is coming), the server counts bytes as they arrive and cuts the request off the moment the same limit is crossed, so it can't silently read an unbounded amount of data into memory.", sections: [
        { title: "Checked upfront (Content-Length header)", items: [
          "Not a valid non-negative number: rejected immediately, 400",
          "Already bigger than 10,485,760 bytes: rejected immediately, 413",
        ] },
        { title: "Checked as it streams (no declared size)", items: [
          "Bytes are counted as they arrive; once the running total crosses 10,485,760, the request is cut off, 413",
          "The bytes already received before the cutoff were still read off the network, this limits how much memory is held onto, not how much bandwidth is spent",
        ] },
      ] },
      PARSE: { eyebrow: "Framework validation", title: "Match the route and validate input", body: "Every endpoint in this app expects certain information, almost like a form with required boxes to fill in. A chat request needs a messages field, for example. Before any of that endpoint's real code runs, two things happen. First, the request is matched to the right endpoint based on its method and path, a POST to /api/v1/llm/chat goes to the chat handler, nowhere else. Second, everything that handler expects gets checked: fields in the body (like messages), and anything required in the headers (like X-Tenant-ID). If something is missing, or in the wrong format, the request stops right here. It never reaches the actual handler code.", sections: [
        { title: "What gets checked", items: [
          "Fields in the request body, like messages for a chat request",
          "Required headers, like X-Tenant-ID",
          "This is only checking \"is the request filled out correctly\", not whether the caller is allowed to do this. That comes later",
        ] },
      ] },
      NEXT: { eyebrow: "Handoff", title: "Continue through route dependencies", body: "A request that makes it this far has the right shape, but nobody has checked who's making it yet, or whether they're allowed to. That happens next, before the actual endpoint code (like the chat handler) runs. First, the caller's identity is checked from the Authorization header. Then, it's checked whether that caller is allowed to use the specific customer account and deployment named in the request. Only after both of those pass does the real work start.", sections: [
        { title: "What happens next, in order", items: [
          "The caller's identity is verified",
          "Whether that caller may use this customer account and deployment is checked",
          "The exact AI provider and model to call is worked out",
        ] },
        { title: "What this step is not", items: [
          "It's a handoff point, not where the checks themselves happen. The identity check is covered in the next diagram (authentication); the permission check comes after that (authorization)",
        ] },
      ] },
      BODY_ERROR: { eyebrow: "Early exit", title: "Return a body-size error", body: "Both rejection paths from the previous step land here, and both return the same stable shape, so a caller can handle them the same way: a JSON body with a human-readable detail, a machine-readable error_code, and the tracking ID (from step 2) for matching against the logs. Nothing about the actual request content is echoed back.", sections: [
        { title: "Example responses", items: [
          "Malformed length (400): { \"detail\": \"Content-Length must be a non-negative integer.\", \"error_code\": \"INVALID_CONTENT_LENGTH\", \"request_id\": \"...\" }",
          "Too large (413): { \"detail\": \"Request body exceeds the 10485760-byte limit.\", \"error_code\": \"REQUEST_BODY_TOO_LARGE\", \"request_id\": \"...\" }",
        ] },
      ] },
      VALIDATION_ERROR: { eyebrow: "Early exit", title: "Return safe validation errors", body: "When a request fails that check, the error says exactly which field was wrong and why, but never repeats back the actual value that was sent. That matters because the bad value could be something sensitive, like a badly formatted API key, and printing it back in an error message would leak it into logs and error screens.", sections: [
        { title: "Example response", items: [
          "{ \"detail\": \"Request validation failed.\", \"error_code\": \"REQUEST_VALIDATION_ERROR\", \"request_id\": \"...\", \"errors\": [{ \"location\": [\"body\", \"messages\"], \"message\": \"Field required\", \"type\": \"missing\" }] }",
        ] },
        { title: "What's included vs. left out", items: [
          "Included: where the problem is, and what's wrong",
          "Left out, on purpose: the actual value that was submitted",
        ] },
      ] },
      UNEXPECTED: { eyebrow: "Safety net", title: "Handle an unexpected failure", body: "This only runs when something crashes that nothing upstream was prepared for. What happens next depends on one thing: has any part of the response already been sent to the caller? If not, the caller gets a plain, predictable error instead of a raw crash. If the response had already started, for example mid-stream, there's no way to take that back and swap in an error page, so the connection is simply closed instead of sending something broken.", sections: [
        { title: "Before any response has been sent", items: [
          "Returns a generic 500 response that looks the same no matter what actually broke: {\"detail\": \"An unexpected error occurred.\", \"error_code\": \"INTERNAL_SERVER_ERROR\", \"request_id\": \"...\"}",
          "The request's tracking ID (from step 2) is included, so the caller and the logs can be matched up afterward",
        ] },
        { title: "After a response has already started", items: [
          "It's too late to send a clean error: the caller is already receiving the first part of a JSON or streamed reply",
          "The connection is closed instead, rather than mixing a broken partial reply with an error page",
        ] },
      ] },
    },
  },
  identity: {
    engine: "reactflow",
    reactFlowMount: "mountAuthenticationReactFlow",
    eyebrow: "03 · Authentication — low-level design",
    title: "How a bearer token becomes a trusted caller",
    intro: "On an inference request, FastAPI resolves get_current_user: extract a bearer token, verify its signature and claims, then return a frozen identity. Missing or unusable tokens stop with 401.",
    label: "Low-level design",
    flowTitle: "Bearer-token verification",
    rule: "A valid token proves caller identity. It does not establish tenant membership or permission to use a deployment; authorization checks those next.",
    notes: [
      ["No database lookup here", "get_current_user validates the signed token and its contents. It does not read the user row, tenant membership or a server-side session."],
      ["Complete token contract", "The validator checks signature, configured algorithm, issuer, audience and time claims, then the required claims, access-token kind, platform role, bounded lifetime and UUID identifiers."],
      ["Role guards are separate", "Some protected routes add a platform RoleGuard after authentication. Inference uses get_current_user directly; tenant-role eligibility is checked in the following authorization phase."],
    ],
    details: {
      DEPENDENCY: { eyebrow: "Route dependency", title: "Ask for the current user", body: "Think of a building with a front desk. Before anyone reaches a specific office, the front desk checks their ID automatically, for everyone, before anyone gets past the lobby. The office itself never has to check IDs. In this service, the \"office\" is the chat endpoint, and the \"front desk\" is a check built into FastAPI, the web framework this app is built on. The endpoint lists what it needs before it can run, here, a verified identity, and FastAPI runs that check first, automatically, before the endpoint's own code ever executes. The endpoint doesn't call the check itself; it simply can't run without the check having already passed. For example, a request arrives at /api/v1/llm/chat with no valid token at all. The chat endpoint's own code never starts running, not even one line of it. The request is stopped at the front desk (the identity check) and handed straight to a 401 response, the same way a visitor without ID is stopped in the lobby and never reaches the office upstairs.", sections: [
        { title: "Why it works this way", items: [
          "If every endpoint had to remember to run this check itself, one could forget, and let a request through unchecked. Declaring it up front makes that mistake impossible",
        ] },
      ] },
      BEARER: { eyebrow: "Header", title: "Extract a Bearer token", body: "Think of a cinema ticket. Whoever is physically holding it can use it to get in, the usher at the door doesn't ask who originally bought it. Showing the ticket is enough. The caller sends that \"ticket\" in the Authorization header, with the word Bearer in front of it. This step's only job is to read that one header and pull the ticket (the token) out of it. It doesn't check whether the ticket is real, that's the next step. If the header isn't there at all, or isn't in that exact Bearer <token> shape, this step hands back nothing rather than raising an error itself. A real request includes Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIi.... If a request instead sends a different scheme, like Authorization: Basic dXNlcjpwYXNz, or leaves the header out entirely, nothing is extracted.", sections: [
        { title: "What it looks for", items: [
          "The Authorization header, in the form Bearer <token>",
        ] },
        { title: "What happens if it's missing or wrong", items: [
          "The request skips straight to the \"No Bearer token\" box on the right, bypassing the verification steps below entirely, so every missing-or-wrong-shape case gets exactly the same response, including the standard WWW-Authenticate: Bearer header that tells the client what kind of credential this service expects",
        ] },
      ] },
      SIGNATURE: { eyebrow: "Cryptographic check", title: "Verify the signed JWT", body: "Think of a wax seal on a letter. Anyone who has the letter can open it and read every word, nothing is hidden. But the moment someone changes even one word and tries to reseal it, the seal no longer matches, and anyone checking it can tell it's been tampered with. The token is signed with a secret key only this service knows. This step checks three things: the signature is genuine (calculated with the correct key and algorithm, proving nothing was altered), the token's issuer and audience match what this service expects (proving the token was actually meant for this service, not borrowed from somewhere else), and the timing is valid, not expired, and not used before it's allowed to be (with a brief allowance for clock differences).", sections: [
        { title: "Example: a token that passes", code: "{\n  \"sub\": \"3b2c9f10-4e21-4b8a-9c3d-1a2b3c4d5e6f\",\n  \"role\": \"developer\",\n  \"type\": \"access\",\n  \"iat\": 1717000000,\n  \"nbf\": 1717000000,\n  \"exp\": 1717003600,\n  \"iss\": \"llm-provider-service\",\n  \"aud\": \"llm-provider-service\",\n  \"jti\": \"a1b2c3d4-5e6f-7890-abcd-ef1234567890\"\n}" },
        { title: "Example: a token that is genuinely signed, but still rejected", code: "{\n  \"sub\": \"3b2c9f10-4e21-4b8a-9c3d-1a2b3c4d5e6f\",\n  \"role\": \"developer\",\n  \"type\": \"access\",\n  \"iat\": 1717000000,\n  \"nbf\": 1717000000,\n  \"exp\": 1717003600,\n  \"iss\": \"llm-provider-service\",\n  \"aud\": \"admin-dashboard\",\n  \"jti\": \"a1b2c3d4-5e6f-7890-abcd-ef1234567890\"\n}", body: "Rejected because its aud doesn't match this service, even though nobody tampered with it." },
      ] },
      CONTRACT: { eyebrow: "Claim checks", title: "Check the access-token contract", body: "Think of a passport. Immigration doesn't just check that the signature page is genuine. They also check that every required field is actually filled in: a name, a photo, a date of birth, an expiry date. A genuinely signed passport missing its photo page is still useless. A JWT is one string made of three dot-separated parts. The middle part decodes into a plain dictionary called its claims, nine required keys, each with its own value. This step checks that dictionary all at once: every required key is present, and every value actually makes sense, so no caller can check a token halfway and skip the rest.", sections: [
        { title: "Example: a decoded token", code: "{\n  \"sub\": \"3b2c9f10-4e21-4b8a-9c3d-1a2b3c4d5e6f\",\n  \"role\": \"developer\",\n  \"type\": \"access\",\n  \"iat\": 1717000000,\n  \"nbf\": 1717000000,\n  \"exp\": 1717003600,\n  \"iss\": \"llm-provider-service\",\n  \"aud\": \"llm-provider-service\",\n  \"jti\": \"a1b2c3d4-5e6f-7890-abcd-ef1234567890\"\n}" },
        { title: "What each required key means", items: [
          "sub: who this token belongs to, a user ID",
          "role: that user's platform role, for example developer",
          "type: what kind of token this is, must be access",
          "iat / nbf / exp: when it was issued, when it becomes valid, and when it expires, each a timestamp",
          "iss / aud: which service issued it, and which service it's meant for",
          "jti: a unique ID for this one token",
        ] },
        { title: "What else gets checked, beyond just being present", items: [
          "type must specifically equal access. Other kinds of token exist in this system and are refused here",
          "role must be one this service actually recognizes, not just any string",
          "The gap between iat and exp must be positive and no longer than the maximum lifetime this service allows",
        ] },
      ] },
      IDENTITY: { eyebrow: "Output", title: "Return a frozen identity", body: "Think of a laminated visitor badge printed at a building's front desk. Once it's printed and laminated, nobody, not the visitor, not anyone else, can scribble on it and change what it says. It's handed over exactly as issued, and whoever looks at it later trusts it completely because it can't have been altered since. Once every check in this diagram has passed, this step produces one small, frozen record describing the caller. Frozen means nothing later in the request can modify it, it's built once, here, and carried forward as-is. Notably, nothing in this entire diagram touched a database: everything was proven from the token itself.", sections: [
        { title: "Example", code: "{\n  \"user_id\": \"3b2c9f10-4e21-4b8a-9c3d-1a2b3c4d5e6f\",\n  \"role\": \"developer\",\n  \"token_id\": \"a1b2c3d4-5e6f-7890-abcd-ef1234567890\",\n  \"issued_at\": \"2026-06-01T10:00:00Z\",\n  \"expires_at\": \"2026-06-01T11:00:00Z\"\n}" },
        { title: "What this is not", items: [
          "This says who is calling, not what they're allowed to do. Whether this caller may use a particular customer account and model is decided in the next diagram (authorization)",
        ] },
      ] },
      MISSING: { eyebrow: "Early exit", title: "No Bearer token: 401", body: "No usable credentials were found in the Authorization header, so there is nothing to verify. The response is 401 with the detail \"Authorization token is required.\" and a WWW-Authenticate: Bearer header, which is the standard way of telling a client what kind of credential to send." },
      INVALID_JWT: { eyebrow: "Early exit", title: "JWT verification failed: 401", body: "A token was present, but it did not survive the signature and timing check: wrong signature, altered contents, or expired. The response is 401 with the detail \"Token is invalid or has expired.\" The logs record what type of failure it was, never the token itself, because the token is a working credential for anyone who reads it." },
      BAD_CLAIMS: { eyebrow: "Early exit", title: "Token contents unusable: 401", body: "The signature was genuine, so nobody tampered with this token, but its contents still don't pass the checks from the previous step. Any one of these returns 401 with the detail \"Token format is invalid.\"", sections: [
        { title: "Example failures", items: [
          "A required key is missing entirely, say the decoded token has sub, role, type... but no jti",
          "type is something other than \"access\", for example \"refresh\"",
          "role is a string this service doesn't recognize, like \"superadmin\" instead of developer/operator/admin/owner",
          "sub or jti isn't a properly formed ID",
          "The gap between iat and exp is longer than this service allows, for example a token valid for 30 days when the maximum is 1 hour",
        ] },
        { title: "Why 401 and not 500", items: [
          "401 means \"you, the caller, sent something that isn't acceptable.\" 500 means \"something broke inside this server.\" A malformed token is the caller's problem, a bad or expired token they're holding, not a bug in this service. So it's handled the same way as \"wrong password\" would be: a normal, expected rejection, not treated as a crash",
        ] },
      ] },
    },
  },
  authorization: {
    engine: "reactflow",
    reactFlowMount: "mountAuthorizationReactFlow",
    eyebrow: "04 · Inference authorization — low-level design",
    title: "How a caller earns one exact inference grant",
    intro: "Use the authenticated caller plus X-Tenant-ID and X-Deployment-Key to run four ordered database checks. The first failed check stops the request; all four passing produces a frozen access context for routing.",
    label: "Low-level design",
    flowTitle: "Four source-of-truth gates",
    rule: "Each gate runs only after the previous one passes. The live grant-cache backend is disabled, so this authorization reads PostgreSQL on every request.",
    notes: [
      ["Inputs", "The API dependency receives the tenant UUID and deployment key from headers and the already authenticated AuthTokenPayload."],
      ["Current cache behavior", "get_inference_authorization_cache supplies backend=None. find_grant returns no cached context, so authorize_inference runs the four database gates and does not store a grant."],
      ["Boundary to routing", "The output names the exact authorized entitlement and deployment. Route resolution is a later phase and re-reads current tenant policy and entitlement state."],
    ],
    details: {
      INPUT: { eyebrow: "Input", title: "Receive caller and route identifiers", body: "require_inference_access receives X-Tenant-ID, X-Deployment-Key and the authenticated AuthTokenPayload, then calls InferenceAuthorizationService. Header shape validation is performed by FastAPI. Source: app/api/inference_dependencies.py." },
      TENANT: { eyebrow: "Gate 1", title: "Is the tenant allowed to receive traffic?", body: "TenantPersistence reads the tenant by ID. A missing tenant fails; status must be active or trial. Source: app/auth/authorization/tenant_inference_auth.py:_authorize_from_source_of_truth." },
      MEMBER: { eyebrow: "Gate 2", title: "Is this caller an eligible tenant member?", body: "TenantMembershipPersistence reads the record for this tenant and authenticated user. Membership must be active and tenant role must be developer, operator, admin or owner; viewer is excluded. This is a tenant role, not the platform role in the JWT. Source: app/auth/authorization/tenant_inference_auth.py and app/schemas/role_hierarchy.py." },
      DEPLOY: { eyebrow: "Gate 3", title: "Is the named deployment active?", body: "TenantDeploymentPersistence looks up the deployment by tenant ID and deployment key. It must exist and have active status. Its provider_id and model_id are then taken from the deployment row for the exact entitlement check. Source: app/auth/authorization/tenant_inference_auth.py." },
      ENTITLE: { eyebrow: "Gate 4", title: "Is there an exact active entitlement?", body: "UserEntitlementPersistence requires an active entitlement matching tenant, user, deployment key, provider ID and model ID. No other grant is substituted. Source: app/auth/authorization/tenant_inference_auth.py." },
      CONTEXT: { eyebrow: "Output", title: "Build a frozen access context", body: "InferenceAccessContext contains tenant, user, deployment, provider, model, tenant role and entitlement identifiers. It carries no credential or secret. Source: app/auth/authorization/tenant_inference_auth.py and app/schemas/auth_schema.py." },
      ROUTE: { eyebrow: "Handoff", title: "Pass the context to route resolution", body: "require_route receives the approved context, then asks InferenceRouteResolver to resolve the requested operation using that exact entitlement ID. This is the next phase, not another authorization gate. Source: app/api/inference_dependencies.py." },
      TENANT_FAIL: { eyebrow: "Gate 1 exit", title: "Tenant missing or suspended", body: "A missing tenant raises TenantNotFoundError (404); a tenant outside active or trial raises TenantSuspendedError (403). The API translates these typed errors. Source: app/auth/authorization/tenant_inference_auth.py and app/api/exception_handlers.py." },
      MEMBER_FAIL: { eyebrow: "Gate 2 exit", title: "Membership or role denied", body: "No active membership, or a tenant role outside the inference roles, raises TenantAccessDeniedError (403). Later gates do not run. Source: app/auth/authorization/tenant_inference_auth.py and app/api/exception_handlers.py." },
      DEPLOY_FAIL: { eyebrow: "Gate 3 exit", title: "Deployment missing or inactive", body: "A missing deployment raises DeploymentNotFoundError (404); a non-active deployment raises DeploymentInactiveError (422). The entitlement is not checked afterward. Source: app/auth/authorization/tenant_inference_auth.py and app/api/exception_handlers.py." },
      ENTITLE_FAIL: { eyebrow: "Gate 4 exit", title: "No active exact entitlement", body: "If the precise tenant/user/deployment/provider/model entitlement is unavailable, TenantAccessDeniedError becomes 403. No access context is created. Source: app/auth/authorization/tenant_inference_auth.py and app/api/exception_handlers.py." },
    },
  },
  routing: {
    engine: "reactflow",
    reactFlowMount: "mountRoutingReactFlow",
    eyebrow: "05 · Route resolution — low-level design",
    title: "How one approved grant becomes an execution plan",
    intro: "The resolver rechecks live tenant policy and the exact authorized entitlement, verifies provider and model policy for chat, embed or rerank, then freezes every setting execution needs into one route.",
    label: "Low-level design",
    flowTitle: "Exact grant → policy and catalog → immutable route",
    rule: "Routing never searches for a replacement entitlement. If the approved record or a required catalog entry is unavailable, this request fails before reservation or provider execution.",
    notes: [
      ["Fresh database reads", "The routing reader queries PostgreSQL for current tenant policy and the exact entitlement. Both rows pass through typed mappers before policy uses them."],
      ["Catalog is memory-resident", "Provider YAML was validated at startup. Per-request provider lookup uses the loaded catalog; an absent provider or model is operator configuration drift, not a client-chosen model error."],
      ["Plan, not a credential", "ResolvedRoute carries an endpoint, secret reference, effective defaults, quota key and fingerprint. It does not read or contain the plaintext provider key."],
      ["Only one capability gate", "The model is checked for the requested operation: chat, embed or rerank. This resolver does not independently check streaming, tool use or vision capabilities."],
    ],
    details: {
      INPUT: { eyebrow: "Input", title: "Build a resolution request", body: "require_route makes a frozen ResolutionRequest from the authorized tenant ID, user ID, deployment key and entitlement ID, plus the endpoint's chat, embed or rerank operation. Source: app/api/inference_dependencies.py and app/inference_routing/models.py." },
      TENANT_READ: { eyebrow: "Live read", title: "Read current tenant policy", body: "PostgresInferenceRoutingConfigReader calls TenantPersistence.get_tenant_config_for_routing and maps the SQL row into TenantConfig. This read is uncached. Source: app/adapters/inference_routing/postgres_config_reader.py and routing_config_mappers.py." },
      TENANT_GATE: { eyebrow: "Gate", title: "Require an active tenant", body: "The resolver refuses a missing tenant and checks TenantConfig.is_active. Active and trial are allowed; other statuses stop the request. The policy object also carries the provider allow-list used later. Source: app/inference_routing/route_resolution.py and app/core/settings/models/tenant_config.py." },
      ENTITLE_READ: { eyebrow: "Live read", title: "Re-read the exact approved entitlement", body: "The SQL reader binds tenant ID, user ID, deployment key and the authorization-approved entitlement ID. Its joined projection supplies provider and model names, endpoint and secret reference; the mapper validates these into UserEntitlementConfig. Source: app/database/user_entitlements.py and app/adapters/inference_routing/routing_config_mappers.py." },
      ENTITLE_GATE: { eyebrow: "Gate", title: "Require the same active entitlement", body: "A missing or inactive record raises AuthorizedEntitlementUnavailableError. The resolver also compares the returned entitlement ID with the approved ID and treats any mismatch as a configuration error. It never substitutes a default or another user's grant. Source: app/inference_routing/route_resolution.py." },
      ALLOW: { eyebrow: "Tenant policy", title: "Apply the provider allow-list", body: "TenantConfig.allows_provider checks the provider named by the entitlement. A null allow-list permits all providers; a configured set must contain that provider. A denial stops with ProviderNotAllowedError. Source: app/core/settings/models/tenant_config.py and app/inference_routing/route_resolution.py." },
      PROVIDER: { eyebrow: "Catalog", title: "Find the provider configuration", body: "The resolver looks up the entitlement's provider name in ConfigLoader's startup-loaded catalog. The request path does not read YAML. A missing entry becomes RoutingCatalogDriftError because the client did not select the provider. Source: app/inference_routing/route_resolution.py and app/core/settings/loader.py." },
      MODEL: { eyebrow: "Catalog", title: "Find this provider's model", body: "provider.get_model_spec looks up the model name from the entitlement within that provider's catalog. If it is absent, the resolver raises RoutingCatalogDriftError rather than switching models. Source: app/inference_routing/route_resolution.py." },
      CAPABILITY: { eyebrow: "Gate", title: "Check the requested operation", body: "The selected model must support ModelCapability for this endpoint's operation: chat, embed or rerank. A model without that capability raises OperationNotSupportedError. Source: app/inference_routing/route_resolution.py and app/core/settings/models/model_config.py." },
      BUILD: { eyebrow: "Construction", title: "Resolve execution settings", body: "build_entitlement_route copies endpoint, region, secret reference and extra config from the entitlement; timeout, temperature and default headers from provider config; and max output tokens from the model spec. quota_key is the entitlement ID. It does not fetch a plaintext credential. Source: app/inference_routing/route_builder.py." },
      FINGERPRINT: { eyebrow: "Identity", title: "Compute the route fingerprint", body: "The builder serializes deployment key, full entitlement, provider config and model spec as sorted JSON, then hashes that with SHA-256. The provider registry later uses this value to distinguish cached adapter configurations. Source: app/inference_routing/route_builder.py." },
      OUTPUT: { eyebrow: "Output", title: "Return the frozen ResolvedRoute", body: "The Pydantic ResolvedRoute is immutable and contains the complete plan for reservation and provider execution: exact provider/model, endpoint, secret reference, effective defaults, quota key and route fingerprint. Source: app/inference_routing/models.py and route_builder.py." },
      TENANT_FAIL: { eyebrow: "Stop", title: "Tenant unavailable", body: "Missing tenant becomes 404; a tenant that is not active or trial becomes 403. The resolver stops before the entitlement read. Source: app/inference_routing/route_resolution.py and app/api/exception_handlers.py." },
      ENTITLE_FAIL: { eyebrow: "Stop", title: "Approved grant unavailable", body: "A missing or inactive exact entitlement becomes 403. An unexpected returned entitlement ID is a configuration error and fails as a server error, never a fallback. Source: app/inference_routing/route_resolution.py and app/api/exception_handlers.py." },
      POLICY_FAIL: { eyebrow: "Stop", title: "Provider forbidden for this tenant", body: "The provider named in the entitlement is absent from the tenant's configured allow-list. ProviderNotAllowedError maps to 403. Source: app/inference_routing/route_resolution.py and app/api/exception_handlers.py." },
      PROVIDER_FAIL: { eyebrow: "Stop", title: "Provider missing from catalog", body: "A database-backed entitlement names a provider absent from the startup-loaded catalog. RoutingCatalogDriftError is treated as a server configuration failure, not a request validation error. Source: app/inference_routing/route_resolution.py and app/inference_routing/exceptions.py." },
      MODEL_FAIL: { eyebrow: "Stop", title: "Model missing from provider", body: "The provider exists but does not contain the entitlement's model name. This is also RoutingCatalogDriftError; the resolver does not choose a substitute model. Source: app/inference_routing/route_resolution.py." },
      CAPABILITY_FAIL: { eyebrow: "Stop", title: "Operation unsupported", body: "The selected model lacks the operation required by the endpoint. OperationNotSupportedError maps to 422. Source: app/inference_routing/route_resolution.py and app/api/exception_handlers.py." },
    },
  },
  quota: {
    engine: "reactflow",
    reactFlowMount: "mountReservationReactFlow",
    eyebrow: "06 · Capacity reservation — low-level design",
    title: "How the route gets capacity before provider work",
    intro: "Streaming chat first claims one local worker slot. Every chat, embedding and rerank request then asks the token manager to reserve capacity for the already resolved route; only an acquired, endpoint-matched response can continue.",
    label: "Low-level design",
    flowTitle: "Local stream slot → shared token reservation → execution handoff",
    rule: "No provider call starts before token reservation succeeds. A stream slot is released when reservation fails; successful reservations are finalized after provider execution.",
    notes: [
      ["Two different limits", "WorkerStreamCapacityLimiter counts open streams inside one process. TokenManagerClient coordinates token capacity with the separate token-manager service for every operation."],
      ["Failure translation", "A full stream worker returns 503; refused or waiting token capacity returns 429; manager outages return 503; malformed or mismatched manager responses are protocol errors mapped to 502."],
      ["Handoff and settlement", "A successful acquire returns a TokenReservation. Non-streaming service methods finalize it after execution; a StreamingInferenceSession owns stream finalization and worker-slot release."],
    ],
    details: {
      INPUT: { eyebrow: "Input", title: "Receive the resolved route and request", body: "InferenceService receives ResolvedRoute, user ID and a chat, embed or rerank request. The route fixes provider, model, endpoint, quota key, fingerprint and model token ceiling before any reservation request. Source: app/services/inference.py and app/inference_routing/models.py." },
      CHAT_LIMIT: { eyebrow: "Local check", title: "Check chat output-token limit", body: "For chat only, an explicit request.max_tokens above the resolved model's effective_max_tokens raises InvalidRequestError before a stream slot or token reservation is acquired. Embed and rerank skip this check. Source: app/services/inference.py:_validate_chat_token_limit." },
      STREAM_SLOT: { eyebrow: "Stream only", title: "Claim a worker stream slot if needed", body: "prepare_stream_chat calls WorkerStreamCapacityLimiter.acquire before the token manager call. It increments a process-local counter under a lock or fails fast with StreamCapacityExceededError. Non-streaming chat, embed and rerank do not acquire this slot. Source: app/services/inference.py and app/streaming/stream_capacity.py." },
      PAYLOAD: { eyebrow: "Request contract", title: "Build the token-manager request", body: "TokenManagerClient sends provider, model, operation-specific input, requested completion tokens, deployment name, request ID, thread ID for chat, route fingerprint and quota key. Chat uses request max_tokens or the route ceiling; embed and rerank request zero completion tokens. Source: app/clients/token_manager_client.py:acquire_reservation." },
      SEND: { eyebrow: "Shared service", title: "POST the acquire request", body: "The process-scoped httpx client POSTs /api/v1/tokens/acquire with a short-lived signed service token, X-Request-ID and X-Service-ID. Transport timeouts and connection errors become TokenManagerUnavailableError. Source: app/clients/token_manager_client.py:_request and _build_service_token." },
      STATUS: { eyebrow: "Response gate", title: "Require an acquired allocation", body: "HTTP 400, 409 or 429 is treated as capacity refusal. Other service failures are translated. A success-shaped JSON response must be an object with allocation_status=ACQUIRED; a waiting allocation is rejected, not executed. A positive Retry-After header is preserved when present. Source: app/clients/token_manager_client.py:acquire_reservation." },
      BIND: { eyebrow: "Route binding", title: "Verify the reserved endpoint", body: "The response's api_endpoint_url is required and compared with ResolvedRoute.api_endpoint_url after trimming trailing slashes. A different endpoint is a TokenManagerProtocolError: accounting cannot redirect the provider call. Source: app/clients/token_manager_client.py:acquire_reservation." },
      RESERVATION: { eyebrow: "Output", title: "Build a typed TokenReservation", body: "The client requires a non-empty token_request_id and integer token_count, parses optional expires_at, and returns an immutable TokenReservation with request, user, tenant and endpoint identity. Source: app/clients/token_manager_client.py:TokenReservation and acquire_reservation." },
      HANDOFF: { eyebrow: "Handoff", title: "Execute under the reservation", body: "InferenceService obtains the provider adapter only after acquire_reservation returns. Non-streaming calls later finalize completed, failed or cancelled outcomes; the streaming session owns cleanup and slot release through completion, failure or disconnect. Source: app/services/inference.py and app/services/streaming_session.py." },
      LIMIT_FAIL: { eyebrow: "Stop", title: "Chat token request exceeds model ceiling", body: "InvalidRequestError is raised before any capacity is acquired and maps to 422. Source: app/services/inference.py and app/api/exception_handlers.py." },
      SLOT_FAIL: { eyebrow: "Stop", title: "Worker stream slots are full", body: "The per-worker limiter refuses immediately with 503 and Retry-After. It does not queue an open streaming socket. Source: app/streaming/stream_capacity.py and app/core/exceptions/streaming.py." },
      MANAGER_FAIL: { eyebrow: "Stop", title: "Token manager is unavailable", body: "A timeout, connection error or 5xx response becomes TokenManagerUnavailableError, mapped to 503. If a stream slot was acquired first, prepare_stream_chat releases it on this failure. Source: app/clients/token_manager_client.py and app/services/inference.py." },
      REJECTED: { eyebrow: "Stop", title: "Capacity refused or still waiting", body: "Manager responses 400, 409 or 429, or an allocation_status other than ACQUIRED, become TokenReservationRejectedError mapped to 429. A positive Retry-After is forwarded when supplied. A held stream slot is released. Source: app/clients/token_manager_client.py and app/services/inference.py." },
      PROTOCOL_FAIL: { eyebrow: "Stop", title: "Manager response breaks the contract", body: "Invalid JSON, missing required fields, service-auth rejection, or a different reserved endpoint becomes TokenManagerProtocolError, mapped to 502. No provider call is made. The stream slot is released if held. Source: app/clients/token_manager_client.py and app/services/inference.py." },
    },
  },
  execution: {
    engine: "reactflow",
    reactFlowMount: "mountExecutionReactFlow",
    eyebrow: "07 · Provider execution — low-level design",
    title: "How one resolved route reaches its provider",
    intro: "After capacity is reserved, InferenceService asks the registry for the adapter tied to this route. The adapter translates one of four operations into a vendor call, protects it with a circuit breaker, and returns the internal response shape.",
    label: "Low-level design",
    flowTitle: "Adapter lookup → guarded provider call → typed result",
    rule: "The provider and endpoint come from ResolvedRoute. Adapter construction never chooses another provider, and an open circuit makes no upstream call.",
    notes: [
      ["A bounded adapter cache", "ProviderRegistry keys by route fingerprint, shares concurrent first builds, and retains adapters under both TTL and LRU limits. An invalidated route is rebuilt on the next call."],
      ["Five built-in adapters", "The audited implementation map contains OpenAI, Anthropic, vLLM, Azure OpenAI and Bedrock. REST adapters borrow one pooled httpx client; Bedrock gets an AWS SDK session."],
      ["Streaming is consumed later", "For stream chat, InferenceService creates a StreamingInferenceSession around the provider generator. The generator's breaker-protected producer starts when the session consumes it; delivery and settlement are the next phase."],
    ],
    details: {
      INPUT: { eyebrow: "Handoff", title: "Receive an acquired reservation", body: "InferenceService calls ProviderRegistry.get_provider only after TokenManagerClient.acquire_reservation returns. The ResolvedRoute still fixes provider, model, endpoint, tenant, credential reference and fingerprint. Source: app/services/inference.py and app/inference_routing/models.py." },
      CACHE: { eyebrow: "Registry", title: "Reuse or build the route's adapter", body: "ProviderRegistry looks up route_fingerprint in its TTL- and size-bounded LRU cache. On a miss, one asyncio task builds the adapter; concurrent callers await the same shielded task. A cache hit skips dependency construction and secret lookup. Source: app/providers/registry.py:get_provider and _build_and_cache." },
      CLASS: { eyebrow: "Implementation", title: "Select an audited adapter class", body: "The route's ProviderImplementation enum is looked up in a fixed map of OpenAI, Anthropic, vLLM, Azure OpenAI and Bedrock classes. An unregistered implementation raises ConfigurationError; no import path is taken from a request. Source: app/providers/registry.py:_resolve_implementation_class." },
      TRANSPORT: { eyebrow: "Connection", title: "Borrow the matching transport", body: "ProviderTransportFactory returns its shared, pooled httpx.AsyncClient for REST_API, or creates an aioboto3 Session for AWS_SDK. Adapters borrow the transport; the application owns closing the shared REST client. gRPC is a placeholder that raises NotImplementedError. Source: app/adapters/provider_transport/transport_factory.py." },
      BREAKER: { eyebrow: "Resilience", title: "Get the provider's local circuit breaker", body: "ProviderCircuitBreakerRegistry normalizes the provider name and returns one in-memory breaker per provider, creating it under a lock on first use. Policy supplies failure threshold and reset interval. Source: app/adapters/provider_transport/circuit_breaker_registry.py." },
      SECRET: { eyebrow: "Credential", title: "Resolve a credential only when required", body: "For AWS_SIGV4 or NONE, the registry skips the secret store. Other auth modes read context.secret_reference with context.tenant_id; missing, invalid and denied secrets become typed errors. The value is wrapped in SecretStr on the cached adapter. Source: app/providers/registry.py:_read_api_key." },
      ADAPTER: { eyebrow: "Construction", title: "Create and cache the adapter", body: "The registry constructs the selected class with the unchanged ResolvedRoute, borrowed transport, breaker and optional SecretStr. It publishes a TTL entry and evicts the least recently used entry above the configured cap. Source: app/providers/registry.py:_build_provider and _build_and_cache." },
      OPERATION: { eyebrow: "Dispatch", title: "Call the requested operation", body: "InferenceService calls generate for non-stream chat, embed for embeddings, rerank for ranking, or obtains stream_generate for streaming chat. It does not branch on vendor. Each concrete adapter implements the vendor-specific methods behind BaseProvider. Source: app/services/inference.py and app/providers/base_provider.py." },
      GUARD: { eyebrow: "Circuit", title: "Run through the breaker", body: "BaseProvider's non-streaming methods call aiobreaker.call_async around the adapter method. Streaming instead uses CircuitBreakerStream: a guarded producer consumes the async generator and passes chunks through a one-item queue, preserving backpressure. The stream runs when iterated, not when its generator is created. Source: app/providers/base_provider.py and app/providers/circuit_breaker_stream.py." },
      VENDOR: { eyebrow: "Adapter", title: "Translate and call the upstream API", body: "The concrete adapter builds its vendor payload, authentication headers and endpoint request, then calls the shared REST client or Bedrock SDK. OpenAI, Anthropic, vLLM, Azure OpenAI and Bedrock each parse their own response or stream events into internal schema models. Unsupported adapter operations raise a typed ProviderError. Source: app/providers/direct/, app/providers/cloud/ and app/schemas/responses_schema.py." },
      OUTPUT: { eyebrow: "Handoff", title: "Return typed data or stream chunks", body: "Non-streaming adapters return ChatResponse, EmbedResponse or RerankResponse to InferenceService. Streaming yields ChatStreamChunk through the guarded generator to StreamingInferenceSession. Usage finalization and SSE delivery occur after this provider boundary. Source: app/providers/base_provider.py, app/services/inference.py and app/services/streaming_session.py." },
      BUILD_FAIL: { eyebrow: "Stop", title: "Adapter construction fails", body: "An unknown implementation, unsupported transport or missing optional SDK stops construction. The failed build task is removed from the registry's inflight map; the service does not fall back to another provider. Source: app/providers/registry.py and app/adapters/provider_transport/transport_factory.py." },
      SECRET_FAIL: { eyebrow: "Stop", title: "Credential cannot be read", body: "A secret store KeyError, ValueError or PermissionError becomes SecretReferenceNotFoundError, InvalidSecretValueError or SecretAccessDeniedError. No adapter is cached for the failed build. Source: app/providers/registry.py:_read_api_key." },
      OPEN: { eyebrow: "Stop", title: "The provider circuit is open", body: "An already-open breaker refuses the call without upstream I/O and becomes ProviderCircuitOpenError with a reset hint. If the current call trips the breaker, BaseProvider preserves the underlying classified cause. Source: app/providers/base_provider.py:_circuit_open_error." },
      FAILURE: { eyebrow: "Stop", title: "Normalize an upstream failure", body: "Known domain errors pass through. Raw httpx or botocore failures are classified into provider timeout, unavailable, credential, rate-limit, validation or internal errors; unexpected exceptions become ProviderInternalError. The service then finalizes the acquired reservation as failed or cancelled. Source: app/providers/base_provider.py, app/providers/http_errors.py and app/services/inference.py." },
    },
  },
  delivery: {
    engine: "reactflow",
    reactFlowMount: "mountDeliveryReactFlow",
    eyebrow: "08 · Delivery and settlement — low-level design",
    title: "How results reach the caller and capacity is returned",
    intro: "A completed non-streaming provider call reconciles usage before JSON is returned. Streaming chat delivers SSE as chunks arrive; its stateful session closes the provider, finalizes the token reservation and releases the worker slot on every terminal path.",
    label: "Low-level design",
    flowTitle: "JSON settlement on the left · SSE lifecycle on the right",
    rule: "JSON success waits for accounting. An active stream sends terminal information as SSE events, while disconnects close owned resources without sending another event.",
    notes: [
      ["One local cleanup task", "StreamingInferenceSession creates one cleanup task for completion, failure, timeout or disconnect. Its provider-close, token finalization and slot-release steps run in that order."],
      ["Bounded streaming", "SSEStreamDelivery keeps at most one source read pending and emits comment heartbeats while waiting. A fixed session deadline prevents a trickling provider stream from holding a slot indefinitely."],
      ["Accounting contract", "TokenManagerClient sends PUT /api/v1/tokens/release with reservation ID, terminal status and observed usage. A 404 is accepted as already finalized; other service failures are raised or logged according to the path."],
    ],
    details: {
      START: { eyebrow: "Input", title: "Continue under an acquired reservation", body: "InferenceService has an acquired TokenReservation. Non-streaming methods await the provider result; prepare_stream_chat instead creates a StreamingInferenceSession with a provider iterator and worker lease. The provider stream has not been consumed yet. Source: app/services/inference.py." },
      N_RESULT: { eyebrow: "JSON", title: "Use the completed provider response", body: "execute_chat, execute_embed and execute_rerank receive ChatResponse, EmbedResponse or RerankResponse from the adapter. The router does not return that object yet; the service still has to finalize its token reservation. Source: app/services/inference.py." },
      N_USAGE: { eyebrow: "Accounting", title: "Read non-streaming usage", body: "Chat reports provider prompt and completion counts when usage exists. Embed reports prompt count and zero completion tokens when usage exists. Rerank forwards usage counts if the adapter supplied them; otherwise both remain null. Source: app/services/inference.py:execute_chat, execute_embed and execute_rerank." },
      N_FINALIZE: { eyebrow: "Accounting", title: "Commit completed usage before success", body: "InferenceService calls _finalize with status=completed and observed counts. It creates a cleanup task, waits through caller cancellation, and bounds the PUT /api/v1/tokens/release with a timeout. A finalization error propagates, so the API does not claim JSON success when accounting is uncommitted. Source: app/services/inference.py:_finalize and _commit_finalization; app/clients/token_manager_client.py." },
      N_JSON: { eyebrow: "Delivery", title: "Return one JSON response", body: "Only after completed finalization succeeds does InferenceService return the typed response to the /chat, /embed or /rerank router. FastAPI serializes it as JSON. Source: app/services/inference.py and app/api/llm_inference_router.py." },
      N_ERROR: { eyebrow: "Failure", title: "On provider failure or cancellation", body: "The non-streaming service finalizes status=failed or cancelled while preserving the original provider/cancellation exception. A finalization failure on this error path is logged rather than replacing the original cause. Source: app/services/inference.py:_finalize_preserving_original." },
      S_SESSION: { eyebrow: "Stream", title: "Own the provider iterator and lease", body: "StreamingInferenceSession stores the provider iterator, token finalizer and StreamCapacityLease. It is a real async iterator with aclose, so it can clean up even if no provider chunk was ever read. Source: app/services/streaming_session.py." },
      S_RESPONSE: { eyebrow: "HTTP", title: "Create the managed SSE response", body: "The /chat router wraps adapt_chat_chunks in SSEStreamDelivery and passes both its SSE iterator and the session source to ManagedStreamingResponse. The response uses text/event-stream plus no-cache and X-Accel-Buffering: no headers. Its ASGI finally closes the owned source even if body iteration never starts. Source: app/api/llm_inference_router.py and app/streaming/managed_response.py." },
      S_READ: { eyebrow: "Backpressure", title: "Read one chunk at a time", body: "SSEStreamDelivery keeps at most one pending source read. StreamingInferenceSession applies one fixed monotonic deadline to each anext and observes usage snapshots from chunks. StreamUsageAccumulator takes the maximum cumulative prompt and completion counts, avoiding double-counting repeated trailers. Source: app/streaming/sse_delivery.py, app/services/streaming_session.py and app/services/stream_usage.py." },
      S_EVENT: { eyebrow: "Delivery", title: "Frame text, metadata and heartbeats", body: "adapt_chat_chunks maps text-bearing chunks to text_delta and other chunks to stream_metadata, excluding raw_chunk. SSEStreamDelivery adds thread_id, monotonically increasing sequence and optional request_id. While the source is quiet, it emits a heartbeat comment without cancelling the read. Source: app/streaming/chat_chunk_adapter.py and app/streaming/sse_delivery.py." },
      S_TERMINAL: { eyebrow: "Lifecycle", title: "Classify the stream ending", body: "End of provider iteration is completed. Provider exception or fixed-duration timeout is failed. Cancellation during a read and aclose on early consumer closure are disconnected. The session's _finish creates at most one cleanup task, even if several close paths race. Source: app/services/streaming_session.py:__anext__, aclose and _finish." },
      S_CLOSE: { eyebrow: "Cleanup 1", title: "Close the provider iterator", body: "The session first calls the provider iterator's aclose when available, under a cleanup timeout. A close timeout or error is logged, and the finally path still advances to accounting and slot release. Source: app/services/streaming_session.py:_cleanup and _close_provider." },
      S_FINALIZE: { eyebrow: "Cleanup 2", title: "Finalize the token reservation", body: "The session calls its finalizer with completed, failed or disconnected status and the accumulated usage, under a timeout. TokenManagerClient sends PUT /api/v1/tokens/release; HTTP 404 is treated as already finalized. Failure on a completed stream is raised so the connected client cannot receive a false success event. Source: app/services/streaming_session.py, app/services/inference.py and app/clients/token_manager_client.py." },
      S_RELEASE: { eyebrow: "Cleanup 3", title: "Return the worker stream slot", body: "The session releases its StreamCapacityLease in a finally block even when token finalization fails. The lease uses a lock and released flag so competing paths cannot decrement the per-worker active count twice. Source: app/services/streaming_session.py:_finalize_and_release and app/streaming/stream_capacity.py." },
      S_END: { eyebrow: "Terminal delivery", title: "Send final event only if connected", body: "After source cleanup, SSEStreamDelivery emits one complete event with status=completed on success. A post-header failure becomes a safe error event followed by complete with status=failed. On cancellation or disconnect it sends no further event. ManagedStreamingResponse still closes the session in its ASGI finally. Source: app/streaming/sse_delivery.py and app/streaming/managed_response.py." },
    },
  },
};
window.DIAGRAM_STAGES = DIAGRAM_STAGES;
