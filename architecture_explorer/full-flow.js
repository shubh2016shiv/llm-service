/* ============================================================
   Full HLD: horizontal, explorable React Flow canvas
   ============================================================
   Four request shapes, stacked as swimlanes top to bottom — because
   "the request" this service handles is not one thing. A GET health
   check, a POST sign-in, a POST chat completion and a PATCH to a
   tenant's deployment take four genuinely different paths through
   the infrastructure, and showing only the busiest of the four (as
   an earlier version of this canvas did) implied every request looks
   like a chat completion. It doesn't.

   Lane A — Sign In            POST /auth/sign-in, /guest-session
   Lanes 01–06 — Run Inference POST /llm/chat, /embed, /rerank
   Lane C — Manage The Platform GET/POST/PATCH/DELETE across
                                /tenants, /users, /providers,
                                /deployments, /entitlements
   Lane D — Health Check       GET /health, /health/ready

   Run Inference is the one lane complex enough to need six internal
   sub-phases of its own (Request In, Access Control, Resolve
   Capacity Check, AI Provider Call, Response & Wrap-Up). Sign In is
   the other lane that branches: two unauthenticated entry points join
   just before one in-process token issuer, and each decision that can
   stop the request has its refusal drawn. Manage and Health stay one
   row each.

   Infrastructure is not a separate legend below the diagram — it is
   inline, at the exact step where a request actually crosses a
   process boundary. Those steps carry a dashed border and a small
   "→ System Name" chip (see .hld-node--hop / .hld-hop-chip in
   explorer.css) so a reader can tell, at a glance, which boxes are
   this service's own logic and which ones are a network hop to
   PostgreSQL, Redis, Vault, the sibling Token Manager service, or
   the AI provider itself.

   A small precondition note floats above all four lanes: the process
   already has its database pool, cache connection, secret client and
   provider catalog built before any of the four request shapes below
   arrives. It has no outgoing edges — it applies to all four lanes
   equally, not to one more than another.

   Nodes never navigate away: hover and keyboard focus reveal a
   compact bubble instead.

   Phase keys reuse the stylesheet's colour families for the six
   Run Inference sub-phases (inputs / knowledge / context / plan /
   execute / post), plus three added in explorer.css for the other
   three lanes (boot, authn, manage, health).
   ============================================================ */

import React from "react";
import { createRoot } from "react-dom/client";
import { ReactFlow, Background, Controls, Handle, Position, MarkerType, BaseEdge } from "@xyflow/react";

const h = React.createElement;

/* Six real phases. No "boot" column here — startup is a precondition
   note floating above phase one, not a step a request takes. */
const PHASES = {
  /* Four request shapes, stacked as swimlanes top to bottom. Sign In is
     the one lane with two entry points and three ways to be refused —
     everything else is one straight line. Run Inference is the lane
     with six internal sub-phases — most requests are simpler than a
     chat call, and sign-in is simpler still, but it still branches. */

  authn:  { index: "A", title: "Sign In", note: "HOW DOES A CALLER GET A TOKEN? Every other lane below needs a token already. This is the only lane that creates one. Two separate doors lead to it.", x: 0, y: 170, w: 2160, h: 900 },

  inputs:    { index: "01", title: "Request In", note: "WHO ARE YOU? Prove the sign-in token. Tenant and deployment arrive as headers — a request, not proof.", x: 0,    y: 1110, w: 300, h: 850 },
  knowledge: { index: "02", title: "Access Control", note: "MAY YOU? Yes/no for this user + tenant + deployment. May be remembered briefly. Both exits meet at Approved.", x: 340,  y: 1110, w: 480, h: 1010 },
  context:   { index: "03", title: "Resolve Deployment", note: "HOW DO WE RUN IT? Fresh config — provider, model, endpoint, settings. Never served from the permission cache.", x: 840,  y: 1110, w: 300, h: 920 },
  plan:      { index: "04", title: "Capacity Check", note: "IS THERE ROOM? 4a streaming only: local slot on this server. 4b every inference: shared LLM-token reservation across the fleet.", x: 1160, y: 1110, w: 540, h: 850 },
  execute:   { index: "05", title: "AI Provider Call", note: "RUN IT. Fetch the API key from Vault, then call the vendor named in the Execution Plan.", x: 1720, y: 1110, w: 420, h: 720 },
  post:      { index: "06", title: "Response & Wrap-Up", note: "Send the answer. Always release the stream slot (if held) and settle LLM-token usage — even mid-stream failure.", x: 2160, y: 1110, w: 560, h: 920 },

  manage: { index: "C", title: "Manage The Platform", note: "GET · POST · PATCH · DELETE across tenants, users, providers, models, deployments and entitlements", x: 0, y: 2120, w: 2780, h: 280 },

  health: { index: "D", title: "Health Check", note: "GET /health · GET /health/ready — no identity required; polled by the load balancer", x: 0, y: 2440, w: 1180, h: 260 },
};

/* `hop` marks a step that leaves this process for another system —
   rendered with a dashed border and a "→ System" chip by StepCard.
   Everything without `hop` is this service's own in-process logic. */
const LAYOUT = {
  START:   { phase: "boot", icon: "repository", tag: "BEFORE ANY REQUEST", x: 40,   y: 20,  w: 280 },

  /* Lane A — Sign In. One row for the password path; a short branch
     below it for the guest path, joining back in just before the token
     is issued. Every box a first-time reader sees uses a plain verb —
     "check", "read", "issue" — never a metaphor that has to be decoded
     before the mechanism can be read. */
  ENTRY:   { phase: "authn", icon: "description", tag: "POST /auth/sign-in", x: 28,   y: 300, w: 230 },
  LIMIT:   { phase: "authn", icon: "queue", tag: "FAILED ATTEMPTS ONLY", x: 500,  y: 300, w: 230, gate: true, hop: "Redis" },
  READ:    { phase: "authn", icon: "database", tag: "PASSWORD HASH + ROLE", x: 915,  y: 300, w: 230, hop: "PostgreSQL" },
  VERIFY:  { phase: "authn", icon: "decision", tag: "SAME ERROR EITHER WAY", x: 1200, y: 300, w: 240, gate: true },
  ISSUE:   { phase: "authn", icon: "guard", tag: "SIGNED, TIME-BOUNDED", x: 1625, y: 300, w: 220 },
  TOKENOUT:{ phase: "authn", icon: "accept", tag: "200 OK", x: 1900, y: 300, w: 200 },

  GUEST:   { phase: "authn", icon: "description", tag: "POST /auth/guest-session", x: 28,  y: 600, w: 230, gate: true },
  STOP403: { phase: "authn", icon: "gap", tag: "403", x: 28,  y: 830, w: 230, blocked: true },
  STOP429: { phase: "authn", icon: "gap", tag: "429", x: 500, y: 830, w: 230, blocked: true },
  STOP401: { phase: "authn", icon: "gap", tag: "401", x: 1200, y: 830, w: 240, blocked: true },

  /* Lane 01 — Request In. Two questions, in order, both visible:
     what the client was allowed to send, then whether the bearer
     token is genuine. A bad token stops in this column. */
  REQIN:   { phase: "inputs", icon: "description", tag: "ONE ENTRY POINT", x: 22, y: 1260, w: 256 },
  IDENT:   { phase: "inputs", icon: "guard", tag: "AUTHENTICATION", x: 22, y: 1545, w: 256, gate: true },
  DENY401: { phase: "inputs", icon: "gap", tag: "401", x: 22, y: 1820, w: 256, blocked: true },

  AUTHCTX: { phase: "knowledge", icon: "decision", tag: "THE AUTHORIZATION KEY", x: 360, y: 1235, w: 300, gate: true },
  CACHE:   { phase: "knowledge", icon: "queue", tag: "YES ONLY · BRIEF", x: 360,  y: 1420, w: 300, hop: "Redis" },
  GATES:   { phase: "knowledge", icon: "database", tag: "MAY YOU? STOP AT FIRST NO", x: 360,  y: 1620, w: 300, gate: true, hop: "PostgreSQL" },
  APPROVED:{ phase: "knowledge", icon: "accept", tag: "PHASE 2 EXIT", x: 675, y: 1520, w: 140, gate: true },
  DENYACL: { phase: "knowledge", icon: "gap", tag: "403 · 404 · 422", x: 360, y: 1970, w: 300, blocked: true },

  LIVE:    { phase: "context", icon: "database", tag: "HOW? ALWAYS FRESH", x: 860,  y: 1280, w: 260, hop: "PostgreSQL" },
  PLANBOX: { phase: "context", icon: "assembly", tag: "CAPABILITY CHECK", x: 860,  y: 1535, w: 260, gate: true },
  DENY422: { phase: "context", icon: "gap", tag: "422", x: 860, y: 1780, w: 260, blocked: true },

  SLOT:    { phase: "plan", icon: "queue", tag: "4a · STREAMING ONLY", x: 1195, y: 1280, w: 250 },
  DENYSLOT:{ phase: "plan", icon: "gap", tag: "503", x: 1480, y: 1280, w: 200, blocked: true },
  RESV:    { phase: "plan", icon: "external", tag: "4b · EVERY INFERENCE", x: 1195, y: 1580, w: 250, gate: true, hop: "Capacity Manager" },
  DENYRESV:{ phase: "plan", icon: "gap", tag: "429", x: 1480, y: 1580, w: 200, blocked: true },

  CRED:    { phase: "execute", icon: "guard", tag: "API KEY", x: 1745, y: 1280, w: 240, hop: "Vault" },
  DENYVAULT:{ phase: "execute", icon: "gap", tag: "503", x: 2000, y: 1280, w: 130, blocked: true },
  CALLBOX: { phase: "execute", icon: "logic", tag: "OPENAI · ANTHROPIC · …", x: 1745, y: 1555, w: 280, gate: true, hop: "AI Provider" },

  SEND:    { phase: "post", icon: "runtime", tag: "CLIENT CONTRACT", x: 2185, y: 1280, w: 280 },
  MIDSTREAM:{ phase: "post", icon: "gap", tag: "AFTER 200 SENT", x: 2490, y: 1280, w: 200, blocked: true },
  DONE:    { phase: "post", icon: "accept", tag: "ALWAYS ONCE", x: 2185, y: 1555, w: 280, gate: true, hop: "Capacity Manager" },
  GAP:     { phase: "post", icon: "gap", tag: "KNOWN LIMITATION", x: 2185, y: 1800, w: 280, blocked: true },

  /* Lane C — Manage The Platform. A different identity check (the same
     bearer-token verification the Inference lane uses) feeds a different
     authorization question, then a linear write path with one optional
     branch: only deployment and entitlement writes touch Vault. */
  MREQ:    { phase: "manage", icon: "description", tag: "GET · POST · PATCH · DELETE", x: 35,   y: 2230, w: 250 },
  MIDENT:  { phase: "manage", icon: "guard", tag: "SAME BEARER TOKEN CHECK", x: 345,  y: 2230, w: 230 },
  MSCOPE:  { phase: "manage", icon: "decision", tag: "PLATFORM ROLE OR TENANT ADMIN", x: 635,  y: 2230, w: 260, gate: true },
  MREF:    { phase: "manage", icon: "database", tag: "DOES THE ID EXIST?", x: 1080, y: 2230, w: 250, hop: "PostgreSQL" },
  MWRITE:  { phase: "manage", icon: "database", tag: "CREATE · UPDATE · DELETE", x: 1390, y: 2230, w: 250, gate: true, hop: "PostgreSQL" },
  MSECRET: { phase: "manage", icon: "guard", tag: "ONLY IF A KEY WAS INCLUDED", x: 1840, y: 2230, w: 250, hop: "Vault" },
  MCACHE:  { phase: "manage", icon: "queue", tag: "SO INFERENCE SEES IT NEXT TIME", x: 2150, y: 2230, w: 250, hop: "Redis" },
  MDONE:   { phase: "manage", icon: "accept", tag: "200 · 201 · 204", x: 2460, y: 2230, w: 170 },

  /* Lane D — Health Check. No identity, no cache, no provider — the
     shortest lane on the canvas by design. */
  HREQ:    { phase: "health", icon: "description", tag: "GET /health · GET /health/ready", x: 35,  y: 2530, w: 250 },
  HPG:     { phase: "health", icon: "database", tag: "READINESS ONLY", x: 325, y: 2530, w: 250, hop: "PostgreSQL" },
  HREDIS:  { phase: "health", icon: "queue", tag: "READINESS ONLY", x: 615, y: 2530, w: 250, hop: "Redis" },
  HDONE:   { phase: "health", icon: "accept", tag: "200 READY · 503 DEGRADED", x: 905, y: 2530, w: 210 },
};

const DETAILS = {
  START: {
    title: "The Service Is Already Running",
    sub: "Built once, before the first request — not a step any request waits on",
    paragraphs: [
      "By the time a request arrives, the database connection pool, the Redis connection, the shared HTTP client every provider adapter borrows, the Vault client, the token-manager client and the full provider catalog are already built and validated. None of that happens per request.",
      "This matters for reading the rest of the diagram: nowhere below does the request 'open a connection' — it always borrows one that already exists. If any of this had failed to build, the process would have refused to start at all rather than serving traffic in a half-ready state.",
    ],
  },

  REQIN: {
    title: "1 · Request Arrives",
    sub: "Sign-in token + tenant + deployment name. Deployment = named model setup, not a code release.",
    paragraphs: [
      "Three headers do most of the work. The Authorization bearer is a sign-in token from Lane A — proof of who the caller is. X-Tenant-ID names the customer organisation. X-Deployment-ID names one saved AI configuration belonging to that tenant (provider + model + endpoint + settings) — customer-support-gpt, invoice-summariser — never a software release.",
      "The token proves the person. The two names are only what they are asking for. All three routes share this door; whether a deployment can chat but not embed is decided later, in Resolve Deployment.",
    ],
  },
  IDENT: {
    title: "2 · Is The Sign-In Token Valid?",
    sub: "Checked locally against a signing key this server already holds — no database, no network call. Not the LLM-token budget — that is Capacity Check.",
    paragraphs: [
      "Why not put the tenant on the token itself: a membership row links one user to one tenant, and the same user can hold a separate membership row in another tenant. Baking one tenant into the token would either force a fresh sign-in on every switch or let one token silently act for every tenant the user belongs to. Naming the tenant per request keeps the blast radius of one leaked token to what that single request asked for.",
      "Authentication answers WHO ARE YOU. Authorization, in the next column, answers MAY YOU USE THIS. Signature, expiry, issuer and audience are checked here, in memory. A bad token ends at 401 — sign in again in Lane A. A good one proves the person and nothing about which tenant they may act for.",
      "The platform role on the token is not the Access Control decision below. That decision uses the caller's membership role inside the requested tenant.",
    ],
  },
  DENY401: {
    title: "Sign-In Token Rejected",
    sub: "401 — sign in again (Lane A). Tenant and deployment are never looked up.",
    paragraphs: [
      "No Authorization header, a bad signature, an expired token, or a token that is not an access token all stop here with 401. Access Control does not run.",
    ],
  },

  AUTHCTX: {
    title: "3 · Authorization Key\nUser + Tenant + Deployment",
    sub: "User from the sign-in token. Tenant and deployment from headers — asked for, not proven.",
    paragraphs: [
      "Those three values together are the authorization question for every inference call. Redis and PostgreSQL both work on exactly that triple. Chat, embed, and rerank ask the same question — the operation is not part of it. The operation is checked one phase later, in Resolve Deployment.",
      "X-Tenant-ID means \"I want to work as tenant T\", never \"I belong to tenant T\". Only the user id on the token is proven. Naming a tenant you have nothing to do with is allowed and achieves nothing — membership is required below or the request is 403.",
    ],
  },
  CACHE: {
    title: "4 · Recent Approval Cached?",
    sub: "Yes-only, brief. A hit still exits through Approved — config is never cached here.",
    paragraphs: [
      "Why a cache at all: the four database checks below are the same for every request this user makes against this deployment, and they rarely change between one request and the next. A hit replaces four lookups with one.",
      "What is stored is only approved identifiers — which tenant, which user, which deployment, which permission record. Not the provider, not the endpoint, not the settings, and never a credential. Those are exactly what the next phase still has to read.",
      "A management change advances a version seal. The next read sees a mismatched seal and throws the stored yes away — it does not wait for TTL. Only successful checks are cached; a refusal is always re-checked from scratch.",
    ],
  },
  GATES: {
    title: "5 · Four Checks, Stop At First No",
    sub: "1 → 404/403 · 2 → 403 · 3 → 404/422 · 4 → 403. Pass and cache-hit both meet at Approved.",
    paragraphs: [
      "Order is cheapest first: prove the tenant exists before reading membership; prove membership before reading the deployment; prove the deployment before looking up the permission that names its provider and model.",
      "Checks 2 and 4 are a hierarchy, not a repeat. Check 2 is coarse: does this user belong to this tenant with a role allowed to call AI. Check 4 is specific: is this user permitted to use THIS deployment.",
      "404 means the named thing does not exist for this tenant. 403 means it exists and you may not use it. 422 for an inactive deployment is deliberate — a real thing the caller may have permission for, in a state that cannot serve traffic.",
    ],
  },
  APPROVED: {
    title: "Access Approved",
    sub: "Phase 2 exit. Cache hit and Postgres pass meet here before Resolve Deployment.",
    paragraphs: [
      "MAY YOU is settled. Both paths through Access Control — a fresh Postgres pass and a Redis cache hit — join at this gate so Resolve Deployment never looks like a skipped unfinished Phase 2.",
      "What leaves here is identifiers and a yes. Nothing needed to actually call a model leaves with it — that is Phase 3.",
    ],
  },
  DENYACL: {
    title: "Access Denied",
    sub: "Stops at the first failed check. No lesser permission is substituted.",
    paragraphs: [
      "No such tenant → 404. Tenant suspended → 403. Not a member, or a role without AI access → 403. No such deployment → 404. Deployment switched off → 422. No permission for that deployment → 403.",
    ],
  },

  LIVE: {
    title: "6 · Read Deployment Config",
    sub: "HOW — always fresh from Postgres. Never served from the permission cache.",
    paragraphs: [
      "Access Control answered may they. This read is where the provider name, model name, endpoint URL, region, settings and secret reference come from. Skipping it is not an option.",
      "Secret reference = where to find the credential, not the credential itself. The real key is fetched later from Vault.",
      "The same query re-checks that the tenant and permission are still active — why a cache hit above never skips this read.",
    ],
  },
  PLANBOX: {
    title: "7 · Does It Support This API?",
    sub: "Wrong capability → 422. Else freeze the Execution Plan in memory.",
    paragraphs: [
      "The Execution Plan is one frozen in-memory object: tenant, deployment, provider, model, endpoint, secret pointer, timeout, temperature, and the route fingerprint. Read-only so no later step re-decides what step 6 resolved.",
      "Access Control never looked at which of the three routes was called — so a cached yes still reaches this box, and this box can still say no with a 422. The tenant's allowed-provider list is applied here too (403 if excluded).",
    ],
  },
  DENY422: {
    title: "Capability Mismatch",
    sub: "422 — this deployment cannot do chat, embed, or rerank as called.",
    paragraphs: [
      "Asking an embedding-only deployment to chat, or a chat deployment to embed, stops here. Nothing was reserved and Vault was never contacted.",
    ],
  },

  SLOT: {
    title: "8a · Local Stream Slot",
    sub: "Streaming chat only. In-memory on this worker. Embed, rerank, and non-stream chat skip this box.",
    paragraphs: [
      "This protects one process from too many long-lived SSE connections — not money, and not every inference request. Non-streaming chat, embed, and rerank go straight to the shared reservation below.",
      "Default limit is a configured concurrent-stream count on this worker. Full → 503 with Retry-After. No queue. No network call.",
    ],
  },
  DENYSLOT: {
    title: "This Worker Is Full",
    sub: "503 — try again shortly. Only streaming chat can land here.",
    paragraphs: [
      "The next attempt can land on a less busy replica. If the shared reservation below fails later, the local stream slot taken above is released before the error returns.",
    ],
  },
  RESV: {
    title: "8b · Shared LLM-Token Reservation",
    sub: "Every inference. Capacity Manager sets aside estimated LLM tokens — not the sign-in token, not USD.",
    paragraphs: [
      "Vendors bill by LLM tokens (text in and out) — a different thing from the sign-in token in Lane A. Every replica may be calling the same vendor, so only a shared Capacity Manager can know the running total.",
      "It sets aside an estimate for this tenant · deployment · provider · model before any real tokens are spent. Unavailable → 429 with Retry-After. If a local stream slot was taken above, it is released at the same moment.",
    ],
  },
  DENYRESV: {
    title: "Shared Reservation Unavailable",
    sub: "429 — try again shortly. Any local stream slot is already released.",
    paragraphs: [
      "The AI vendor is never called. Nothing stays held: the stream slot (if any) is released, and no LLM-token estimate stays set aside.",
    ],
  },

  CRED: {
    title: "9 · Fetch API Key From Vault",
    sub: "Step 6 stored WHERE the key lives. The value is read here, seconds before the call.",
    paragraphs: [
      "The Execution Plan names a secret location, not the secret. Only now is the real value fetched — and only for providers that need one. This identity can only read; a separate admin identity is the only writer.",
    ],
  },
  DENYVAULT: {
    title: "Vault Unavailable",
    sub: "503 — no provider call. Reservation and stream slot are released on the way out.",
    paragraphs: [
      "Without a credential the outbound call cannot start. Cleanup still runs so capacity is not left held.",
    ],
  },
  CALLBOX: {
    title: "10 · Call The Model Provider",
    sub: "→ OpenAI, Anthropic, Bedrock, … — one internal contract for every vendor.",
    paragraphs: [
      "This is the step that leaves the company's infrastructure. Every vendor dialect is translated behind one shared contract. A circuit breaker refuses further calls to a repeatedly failing provider for a cooling-off window.",
    ],
  },

  SEND: {
    title: "11 · Send The Answer",
    sub: "Stream (SSE) or one JSON body. After the first byte, HTTP status is already 200.",
    paragraphs: [
      "Streaming: pieces reach the caller as the provider produces them. Non-streaming: one complete reply. Everything upstream was resolved first so early failures could still be clean HTTP errors.",
      "Once headers and the first chunk are sent, a provider failure cannot change the status code — that is the mid-stream box below.",
    ],
  },
  MIDSTREAM: {
    title: "Mid-Stream Failure",
    sub: "Keep chunks already received. SSE error + complete if still connected. Cleanup always runs.",
    paragraphs: [
      "HTTP status stays 200 — headers were already sent. If the client is still connected it receives an SSE error event, then complete with status failed. If it disconnected, it gets no final event; cleanup still records disconnected.",
      "Usage reconciliation reports the largest cumulative LLM-token counts observed before failure. If the provider had not emitted usage yet, exact partial usage is unknown and the gateway passes none — it cannot prove what the provider billed.",
    ],
  },
  DONE: {
    title: "12 · Release Slot · Settle Usage",
    sub: "Always once. Release stream slot if held; finalize the LLM-token reservation with observed counts.",
    paragraphs: [
      "The local stream slot (streaming chat only) returns to this worker. Capacity Manager is told real prompt and completion tokens when known, and the reservation closes as completed, failed, cancelled, or disconnected.",
      "That is why the word is reserve: the estimate is held before the call; the books are settled after it, whether or not the call went well.",
    ],
  },
  GAP: {
    title: "What's Not Measured Yet",
    sub: "No bill reconciliation against the vendor. No check that the answer was any good.",
    paragraphs: [
      "Nothing here checks that reported LLM-token usage matches what the provider charged, and nothing checks whether the answer was any good. A fast, confident wrong answer passes every check on this diagram.",
      "Both are solvable — periodic reconciliation, output sampling — but neither exists today.",
    ],
  },

  /* ---------- Lane A - Sign In ---------- */
  ENTRY: {
    title: "1 · Sign-In Request Arrives",
    sub: "A username and password arrive. No token yet — this request is what creates one.",
    paragraphs: [
      "A username and password arrive with no bearer token. This lane is how a token gets minted at all -- every other lane on this canvas requires one already.",
      "GET /auth/options, next to this one, only tells the client which entry points this deployment offers. It reads configuration and joins nothing drawn here.",
    ],
  },
  LIMIT: {
    title: "2 · Too Many Failed Logins Lately?",
    sub: "Ask Redis how many times this username has failed recently. Default: 5 failures in 5 minutes.",
    paragraphs: [
      "Before the password is looked at, Redis is asked how many times this username has failed recently. The limit is 5 failures inside a 5-minute window — both numbers are configuration, not fixed in the code. Over the limit the request stops here and the caller is told how long to wait. The password is never read.",
      "Only failures are counted, and a success wipes the count, so one typo followed by the right password is never punished. Each new failure also restarts the clock, so sustained guessing keeps the door shut rather than letting the window lapse mid-attack.",
      "If Redis cannot be reached, the attempt is allowed. Refusing every sign-in would turn a cache outage into an authentication outage. The password check below still runs.",
    ],
  },
  READ: {
    title: "3 · Fetch The Account From The Database",
    sub: "Get the stored password hash, whether the account is active, and the user’s role.",
    paragraphs: [
      "PostgreSQL returns three things and nothing else: the stored password hash, whether the account is still active, and the user’s platform role. That is the whole trip to the database. The password comparison itself happens in the next box, inside this service.",
      "Finding a row here does not mean the caller gets in. A suspended employee still has a row, and still has a password that matches. Step 4 is what decides — this step only gathers the facts it needs to decide with.",
      "An unknown username still spends the same time as a real comparison, so a missing account is not faster than a wrong password.",
    ],
  },
  VERIFY: {
    title: "4 · Does The Password Match?",
    sub: "Compare the password to the stored hash, then check the account is active. One error covers every failure.",
    paragraphs: [
      "Four different failures share one answer: the username does not exist, the password is wrong, the account is not active, or the stored role is one this service refuses to sign. The caller sees the same 401 every time, so nobody can use the error message to discover which usernames are real. The log records which of the four it actually was.",
      "A rejection counts the failure in Redis. An acceptance clears that counter, then the request is allowed to proceed to issuing a token.",
    ],
  },
  ISSUE: {
    title: "5 · Issue The Token",
    sub: "Identity and platform role. No tenant on the token.",
    paragraphs: [
      "The token is signed here, in this process, from the same settings the verifier will later trust: identity, platform role, issuer, audience, and a lifetime this service refuses to let outrun verification.",
      "This is the only place in the system that issues one. The token carries no tenant, and it carries no flag saying the session was a guest. Run Inference and Manage The Platform only ever check a token -- they never issue one.",
    ],
  },
  TOKENOUT: {
    title: "6 · Return The Sign-In Token",
    sub: "Bearer for Request In and every lane below. Expiry + guest flag on this response only.",
    paragraphs: [
      "The client receives the sign-in token, when it expires, and the identity to display, including whether this session came through the guest entry point.",
      "That guest flag lives only on this response. Request In's 401 means come back here and sign in again — the bearer this box returns is what Phase 1 verifies.",
    ],
  },
  GUEST: {
    title: "1b · Guest Door Open?",
    sub: "Separate URL for local bootstrap. No password. Production refuses to start with it on.",
    paragraphs: [
      "This is a different URL, not a fallback from step 1. It exists so a developer can administer a fresh local stack before any password is known. Closed → 403. Open → straight to step 5, no rate limit and no account read — which is why production will not start while it is switched on.",
    ],
  },
  STOP429: {
    title: "Too Many Attempts",
    sub: "Wait and try again. The password was never read.",
    paragraphs: [
      "The failure budget for this username is already spent. The response says how long to wait. No credential row is read.",
    ],
  },
  STOP401: {
    title: "Credential Rejected",
    sub: "This failure is counted in Redis. Always the same message, whatever went wrong.",
    paragraphs: [
      "The caller cannot tell whether the username exists. The failure is written to Redis so the next attempt spends the budget.",
    ],
  },
  STOP403: {
    title: "Guest Disabled",
    sub: "403 — normal in production. This door is opt-in for local use only.",
    paragraphs: [
      "Guest access is opt-in. A deployment that does not offer it answers 403, which is the normal state.",
    ],
  },

  /* ---------- Lane C - Manage The Platform ---------- */
  MREQ: {
    title: "Receive The Request",
    sub: "Every administrative action in the product goes through here",
    paragraphs: [
      "Create a tenant, list its deployments, update a user's role, revoke an entitlement, retire a model from the catalog - all four HTTP verbs, across half a dozen resource types, all funnelled through the same shape of check.",
    ],
  },
  MIDENT: {
    title: "Verify Caller Identity",
    sub: "The same bearer-token check the Run Inference lane uses",
    paragraphs: [
      "Signature, issuer, audience, expiry, role — the same verification the Run Inference lane uses. Identity is checked once, the same way, on every lane that already holds a token. Sign In does not do this check; it is the lane that mints the token.",
    ],
  },
  MSCOPE: {
    title: "Check Tenant-Scoped Access",
    sub: "A different question from inference's four gates: may you administer this, not may you infer",
    paragraphs: [
      "A platform administrator or operator role passes on that badge alone, no database lookup needed. Anyone else must be an active member of the specific tenant being touched - and a write additionally requires an admin role inside that tenant, not just membership.",
      "This is a genuinely different authorization decision from the four gates in Run Inference. It is never cached: every administrative action is checked fresh.",
    ],
  },
  MREF: {
    title: "Validate References",
    sub: "PostgreSQL - does the tenant, user, provider or model this request names actually exist?",
    paragraphs: [
      "Before anything is written, every foreign id in the request body is confirmed to exist. A request naming a provider that was never created fails here, cleanly, with the exact id that was wrong - rather than as an opaque database constraint error one step later.",
    ],
  },
  MWRITE: {
    title: "Write The Change",
    sub: "PostgreSQL - the actual create, update or delete",
    paragraphs: [
      "The record itself is written. Database constraints - a partial unique index, a check constraint, a foreign key - hold several invariants that this step relies on rather than re-implements, such as a tenant having at most one active entitlement per exact route.",
    ],
  },
  MSECRET: {
    title: "Write The Credential",
    sub: "Vault - only when the request body actually included a provider API key",
    paragraphs: [
      "Creating or rotating a deployment or an entitlement can include a real provider credential. When it does, that value is written to Vault at a fresh versioned path by a write-only identity, and only the resulting reference is stored on the database row - the plaintext never reaches PostgreSQL.",
      "Most administrative writes have no credential in them at all - renaming a tenant, suspending a user - and skip this box entirely.",
    ],
  },
  MCACHE: {
    title: "Invalidate The Cache",
    sub: "Redis - so Run Inference sees this change on its very next request",
    paragraphs: [
      "A version marker is advanced for exactly the scope this change touched - this one deployment, this one membership, this whole tenant. Every cached authorization grant that depended on it is invalid from this moment, without anyone having to find and delete it.",
    ],
  },
  MDONE: {
    title: "Return The Result",
    sub: "The new or updated record, with any secret-bearing field already stripped out",
    paragraphs: [
      "Whatever comes back has been scrubbed of anything that looks like a credential, recursively, even inside nested fields - a defensive filter on the way out, not a promise that a field was never asked for.",
    ],
  },

  /* ---------- Lane D - Health Check ---------- */
  HREQ: {
    title: "Receive The Request",
    sub: "No identity required - this is what a load balancer polls",
    paragraphs: [
      "A plain liveness check (is the process running at all?) returns immediately with no dependency checks. A readiness check goes further and asks whether this instance should actually receive traffic right now.",
    ],
  },
  HPG: {
    title: "Ping PostgreSQL",
    sub: "PostgreSQL - readiness only; a plain liveness check never gets this far",
    paragraphs: [
      "A single lightweight query confirms the connection pool can actually reach the database. This and the Redis check below run at the same time, not one after the other.",
    ],
  },
  HREDIS: {
    title: "Ping Redis",
    sub: "Redis - readiness only, and deliberately strict",
    paragraphs: [
      "An unreachable Redis marks this instance unready, not merely degraded - because the authorization cache's invalidation path cannot function without it, and serving inference traffic on a Redis that might be handing out stale grants is worse than refusing traffic outright.",
    ],
  },
  HDONE: {
    title: "Return Status",
    sub: "200 and ready, or 503 and pulled from rotation",
    paragraphs: [
      "In non-production environments the response also names which dependency failed, to make local debugging faster. In production it says only ready or degraded - enough for a load balancer to act on, nothing that helps an attacker map the internal topology.",
    ],
  },
};

function PhasePanel({ data }) {
  return h(
    "div",
    { className: `hld-phase hld-phase--${data.phase}` },
    h("div", { className: "hld-phase-heading" },
      data.index ? h("span", { className: "hld-phase-index" }, data.index) : null,
      h("div", null,
        h("p", { className: "hld-phase-title" }, data.title),
        h("p", { className: "hld-phase-note" }, data.note)
      )
    )
  );
}

const HLD_ICONS = {
  description: [
    ["rect", { x: 5, y: 3, width: 14, height: 18, rx: 2 }],
    ["path", { d: "M9 8h6M9 12h6M9 16h4" }],
  ],
  documents: [
    ["path", { d: "M8 6V3h11v14h-3" }],
    ["rect", { x: 5, y: 6, width: 11, height: 15, rx: 2 }],
    ["path", { d: "M8 11h5M8 15h5" }],
  ],
  database: [
    ["ellipse", { cx: 12, cy: 5, rx: 7, ry: 3 }],
    ["path", { d: "M5 5v6c0 1.7 3.1 3 7 3s7-1.3 7-3V5M5 11v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6" }],
  ],
  queue: [
    ["rect", { x: 3, y: 5, width: 13, height: 4, rx: 1 }],
    ["rect", { x: 3, y: 11, width: 13, height: 4, rx: 1 }],
    ["rect", { x: 3, y: 17, width: 13, height: 4, rx: 1, opacity: 0.45 }],
    ["path", { d: "M19 8v11m0 0-3-3m3 3 3-3" }],
  ],
  search: [
    ["circle", { cx: 10, cy: 10, r: 5.5 }],
    ["path", { d: "m14.2 14.2 5.3 5.3M7.5 10h5" }],
  ],
  table: [
    ["rect", { x: 3, y: 4, width: 18, height: 16, rx: 2 }],
    ["path", { d: "M3 9h18M9 9v11M15 9v11" }],
  ],
  logic: [
    ["circle", { cx: 5, cy: 6, r: 2 }],
    ["circle", { cx: 19, cy: 6, r: 2 }],
    ["circle", { cx: 12, cy: 18, r: 2 }],
    ["path", { d: "M7 6h10M6.5 7.5l4.2 8.7M17.5 7.5l-4.2 8.7" }],
  ],
  gate: [
    ["path", { d: "m12 3 9 9-9 9-9-9z" }],
    ["path", { d: "m8.5 12 2.2 2.2 4.8-4.8" }],
  ],
  gap: [
    ["circle", { cx: 12, cy: 12, r: 9 }],
    ["path", { d: "M8.5 8.5l7 7M15.5 8.5l-7 7" }],
  ],
  function: [
    ["path", { d: "M11 4H9.5A2.5 2.5 0 0 0 7 6.5V18M4.5 10H11M14 9l6 6M20 9l-6 6" }],
  ],
  assembly: [
    ["rect", { x: 3, y: 4, width: 8, height: 7, rx: 1.5 }],
    ["rect", { x: 13, y: 4, width: 8, height: 7, rx: 1.5 }],
    ["rect", { x: 8, y: 14, width: 8, height: 7, rx: 1.5 }],
    ["path", { d: "M7 11v1.5h10V11M12 12.5V14" }],
  ],
  contract: [
    ["rect", { x: 5, y: 4, width: 14, height: 17, rx: 2 }],
    ["path", { d: "M9 4V2h6v2M8.5 12l2 2 5-5M9 18h6" }],
  ],
  decision: [
    ["path", { d: "m12 3 8 8-8 8-8-8zM12 19v3M4 11H1M20 11h3" }],
  ],
  guard: [
    ["path", { d: "M12 3 20 6v5c0 5-3.2 8.5-8 10-4.8-1.5-8-5-8-10V6z" }],
    ["path", { d: "m8.5 12 2.2 2.2 4.8-4.8" }],
  ],
  external: [
    ["rect", { x: 2.5, y: 3, width: 8, height: 8, rx: 2 }],
    ["rect", { x: 13.5, y: 13, width: 8, height: 8, rx: 2 }],
    ["path", { d: "M10.5 10.5l3 3" }],
  ],
  accept: [
    ["circle", { cx: 12, cy: 12, r: 9 }],
    ["path", { d: "m8 12 2.7 2.7L16.5 9" }],
  ],
  artifact: [
    ["path", { d: "M4 7h16v13H4zM3 3h18v4H3zM9 11h6" }],
  ],
  repository: [
    ["path", { d: "M3 6h7l2 2h9v12H3z" }],
    ["path", { d: "M8 13h8M12 10v6" }],
  ],
  runtime: [
    ["rect", { x: 3, y: 4, width: 18, height: 16, rx: 2 }],
    ["path", { d: "M3 9h18M7 14l3 2-3 2M13 18h4" }],
  ],
  report: [
    ["path", { d: "M6 3h9l4 4v14H6zM15 3v5h4" }],
    ["path", { d: "M9 17v-3M12.5 17v-6M16 17v-8" }],
  ],
};

function HldIcon({ name }) {
  const shapes = HLD_ICONS[name] || HLD_ICONS.description;
  return h(
    "svg",
    { viewBox: "0 0 24 24", focusable: "false", "aria-hidden": "true" },
    ...shapes.map(([element, props], index) => h(element, { ...props, key: index }))
  );
}

function StepCard({ data }) {
  const classes = [
    "hld-node",
    `hld-node--${data.phase}`,
    data.tone ? `hld-node--tone-${data.tone}` : "",
    data.gate ? "hld-node--gate" : "",
    data.blocked ? "hld-node--blocked" : "",
    data.hop ? "hld-node--hop" : "",
  ].filter(Boolean).join(" ");

  return h(
    "div",
    {
      className: classes,
      tabIndex: 0,
      role: "group",
      "aria-label": `${data.title}. ${data.sub || ""}`,
      onFocus: (event) => data.onHover(event.currentTarget, data.id, true),
      onBlur: (event) => data.onLeave(event.currentTarget),
    },
    h(Handle, { type: "target", position: Position.Left, id: "left", className: "hld-handle" }),
    h(Handle, { type: "target", position: Position.Top, id: "top", className: "hld-handle" }),
    h(Handle, { type: "target", position: Position.Bottom, id: "bottom-in", className: "hld-handle" }),
    data.hop ? h("span", { className: "hld-hop-chip" }, `→ ${data.hop}`) : null,
    h("span", { className: "hld-node-icon", "aria-hidden": "true" }, h(HldIcon, { name: data.icon })),
    h("span", { className: "hld-node-tag" }, data.tag),
    h("p", { className: "hld-node-title", style: { whiteSpace: "pre-line" } }, data.title),
    data.sub ? h("p", { className: "hld-node-sub" }, data.sub) : null,
    h(Handle, { type: "source", position: Position.Right, id: "right", className: "hld-handle" }),
    h(Handle, { type: "source", position: Position.Bottom, id: "bottom", className: "hld-handle" })
  );
}

/* A hop between two phase columns travels through the open band above the
   target's first card, so the connector and its label stay clear of every
   node rather than relying on React Flow's midpoint placement. */
function PhaseHopEdge({ id, sourceX, sourceY, targetX, targetY, markerEnd, style, label, data }) {
  const exitX = data?.exitX ?? sourceX + 60;
  const viaY = data?.viaY ?? targetY - 70;
  const labelX = data?.labelX ?? targetX;
  const labelY = data?.labelY ?? viaY;
  const labelWidth = Math.min(320, Math.max(110, (label?.length || 0) * 6.7 + 24));
  const path = `M ${sourceX},${sourceY} L ${exitX},${sourceY} L ${exitX},${viaY} L ${targetX},${viaY} L ${targetX},${targetY}`;

  return h(
    React.Fragment,
    null,
    h(BaseEdge, { id, path, markerEnd, style }),
    label
      ? h(
          "g",
          { className: "hld-flow-label", transform: `translate(${labelX} ${labelY})` },
          h("rect", { x: -labelWidth / 2, y: -12, width: labelWidth, height: 24, rx: 8, ry: 8 }),
          h("text", { x: 0, y: 1, textAnchor: "middle", dominantBaseline: "middle" }, label)
        )
      : null
  );
}

/* A bypass route that skips one or more phases travels through the empty band
   below every card, using the narrow gutters between phase panels to get down
   there and back up again. */
function LowerCorridorEdge({ id, sourceX, sourceY, targetX, targetY, markerEnd, style, label, data }) {
  const leftRailX = data?.leftRailX ?? sourceX + 60;
  const rightRailX = data?.rightRailX ?? targetX - 48;
  const corridorY = data?.corridorY ?? Math.max(sourceY, targetY) + 120;
  const labelX = data?.labelX ?? (leftRailX + rightRailX) / 2;
  const labelY = data?.labelY ?? corridorY;
  const labelWidth = Math.min(320, Math.max(110, (label?.length || 0) * 6.7 + 24));
  const path = `M ${sourceX},${sourceY} L ${leftRailX},${sourceY} L ${leftRailX},${corridorY} L ${rightRailX},${corridorY} L ${rightRailX},${targetY} L ${targetX},${targetY}`;

  return h(
    React.Fragment,
    null,
    h(BaseEdge, { id, path, markerEnd, style }),
    label
      ? h(
          "g",
          { className: "hld-flow-label", transform: `translate(${labelX} ${labelY})` },
          h("rect", { x: -labelWidth / 2, y: -12, width: labelWidth, height: 24, rx: 8, ry: 8 }),
          h("text", { x: 0, y: 1, textAnchor: "middle", dominantBaseline: "middle" }, label)
        )
      : null
  );
}

const NODE_TYPES = { phase: PhasePanel, step: StepCard };
const EDGE_TYPES = { phaseHop: PhaseHopEdge, lowerCorridor: LowerCorridorEdge };

function flowEdge(id, source, target, options = {}) {
  const palette = {
    main: { color: "#50615a", dash: undefined, width: 2.4 },
    evidence: { color: "#55758a", dash: "5 5", width: 1.45 },
    loop: { color: "#8a5574", dash: "7 5", width: 2 },
    blocked: { color: "#9d4b41", dash: "5 4", width: 1.7 },
  }[options.kind || "main"];

  return {
    id,
    source,
    target,
    sourceHandle: options.sourceHandle || "right",
    targetHandle: options.targetHandle || "left",
    type: options.type || "smoothstep",
    label: options.label,
    labelStyle: { fill: "#e2bc88", fontSize: 12.5, fontWeight: 750, letterSpacing: "0.035em" },
    labelBgStyle: { fill: "#101614", fillOpacity: 0.98 },
    labelBgPadding: [9, 6],
    labelBgBorderRadius: 8,
    markerEnd: { type: MarkerType.ArrowClosed, width: 17, height: 17, color: palette.color },
    style: { stroke: palette.color, strokeWidth: palette.width, strokeDasharray: palette.dash },
    data: options.data,
    zIndex: options.zIndex ?? (options.label ? 4 : 0),
  };
}

function buildElements(onHover, onLeave) {
  const nodes = Object.entries(PHASES).map(([phase, box]) => ({
    id: `phase-${phase}`,
    type: "phase",
    position: { x: box.x, y: box.y },
    style: { width: box.w, height: box.h },
    data: { phase, index: box.index, title: box.title, note: box.note },
    draggable: false,
    selectable: false,
    focusable: false,
    zIndex: 0,
  }));

  Object.entries(LAYOUT).forEach(([id, layout]) => {
    const detail = DETAILS[id];
    nodes.push({
      id,
      type: "step",
      position: { x: layout.x, y: layout.y },
      style: { width: layout.w },
      data: { id, ...detail, ...layout, onHover, onLeave },
      draggable: false,
      selectable: false,
      zIndex: 2,
    });
  });

  const edges = [
    /* Lane A — Sign In. One straight line for the password path.
       The guest path is a short branch directly below Receive The
       Request, joining back in right before the token is issued --
       the same "bypass a few steps" shape already used in Access
       Control and Manage The Platform, not a new pattern to learn. */
    flowEdge("entry-limit", "ENTRY", "LIMIT", { sourceHandle: "right", targetHandle: "left" }),
    flowEdge("limit-read", "LIMIT", "READ", { sourceHandle: "right", targetHandle: "left", label: "Under the limit" }),
    flowEdge("limit-429", "LIMIT", "STOP429", { sourceHandle: "bottom", targetHandle: "top", kind: "blocked", label: "Over the limit" }),
    flowEdge("read-verify", "READ", "VERIFY", { sourceHandle: "right", targetHandle: "left" }),
    flowEdge("verify-issue", "VERIFY", "ISSUE", { sourceHandle: "right", targetHandle: "left", label: "Password matches" }),
    flowEdge("verify-401", "VERIFY", "STOP401", { sourceHandle: "bottom", targetHandle: "top", kind: "blocked", label: "No match · count this failure in Redis" }),
    flowEdge("issue-tokenout", "ISSUE", "TOKENOUT", { sourceHandle: "right", targetHandle: "left" }),
    flowEdge("guest-403", "GUEST", "STOP403", { sourceHandle: "bottom", targetHandle: "top", kind: "blocked", label: "Guest door closed" }),
    flowEdge("guest-issue", "GUEST", "ISSUE", {
      type: "phaseHop",
      sourceHandle: "right",
      targetHandle: "bottom-in",
      kind: "evidence",
      label: "Open · no password needed",
      data: { exitX: 330, viaY: 740, labelX: 950, labelY: 740 },
      zIndex: 4,
    }),

    /* Lane 01 -- Request In */
    flowEdge("entry-ident", "REQIN", "IDENT", { sourceHandle: "bottom", targetHandle: "top" }),
    flowEdge("ident-401", "IDENT", "DENY401", { sourceHandle: "bottom", targetHandle: "top", kind: "blocked", label: "Rejected" }),
    flowEdge("ident-authctx", "IDENT", "AUTHCTX", {
      type: "phaseHop",
      targetHandle: "top",
      label: "Identity confirmed",
      data: { exitX: 305, viaY: 1100, labelX: 305, labelY: 1100 },
      zIndex: 4,
    }),

    /* Lane 02 -- Access Control: key → cache → gates; both yes paths
       meet at Approved before Resolve Deployment. */
    flowEdge("authctx-cache", "AUTHCTX", "CACHE", { sourceHandle: "bottom", targetHandle: "top" }),
    flowEdge("cache-gates", "CACHE", "GATES", { sourceHandle: "bottom", targetHandle: "top", label: "No saved yes" }),
    flowEdge("gates-deny", "GATES", "DENYACL", { sourceHandle: "bottom", targetHandle: "top", kind: "blocked", label: "First check failed" }),
    flowEdge("gates-cache-loop", "GATES", "CACHE", {
      type: "phaseHop",
      sourceHandle: "right",
      targetHandle: "left",
      kind: "evidence",
      label: "Save this yes",
      data: { exitX: 705, viaY: 1595, labelX: 555, labelY: 1606 },
      zIndex: 5,
    }),
    flowEdge("gates-approved", "GATES", "APPROVED", {
      type: "phaseHop",
      sourceHandle: "right",
      targetHandle: "bottom-in",
      label: "Pass",
      data: { exitX: 745, viaY: 1705, labelX: 720, labelY: 1702 },
      zIndex: 4,
    }),
    flowEdge("cache-approved", "CACHE", "APPROVED", {
      type: "phaseHop",
      sourceHandle: "right",
      targetHandle: "left",
      kind: "evidence",
      label: "Cached yes",
      data: { exitX: 745, viaY: 1475, labelX: 720, labelY: 1455 },
      zIndex: 4,
    }),
    flowEdge("approved-live", "APPROVED", "LIVE", {
      type: "phaseHop",
      sourceHandle: "right",
      targetHandle: "top",
      label: "MAY you — settled",
      data: { exitX: 830, viaY: 1185, labelX: 930, labelY: 1250 },
      zIndex: 4,
    }),

    /* Lane 03 -- Resolve Deployment */
    flowEdge("live-plan", "LIVE", "PLANBOX", { sourceHandle: "bottom", targetHandle: "top" }),
    flowEdge("plan-422", "PLANBOX", "DENY422", { sourceHandle: "bottom", targetHandle: "top", kind: "blocked", label: "Wrong capability" }),
    flowEdge("plan-slot", "PLANBOX", "SLOT", {
      type: "phaseHop",
      targetHandle: "top",
      label: "Streaming chat",
      data: { exitX: 1125, viaY: 1185, labelX: 1240, labelY: 1250 },
      zIndex: 4,
    }),
    flowEdge("plan-resv-bypass", "PLANBOX", "RESV", {
      type: "phaseHop",
      sourceHandle: "right",
      targetHandle: "left",
      kind: "evidence",
      label: "Non-stream · embed · rerank",
      data: { exitX: 1125, viaY: 1645, labelX: 1125, labelY: 1755 },
      zIndex: 4,
    }),

    /* Lane 04 -- Capacity Check: 4a streaming-only local slot, then
       4b shared LLM-token reservation for every inference. */
    flowEdge("slot-503", "SLOT", "DENYSLOT", { sourceHandle: "right", targetHandle: "left", kind: "blocked" }),
    flowEdge("slot-resv", "SLOT", "RESV", { sourceHandle: "bottom", targetHandle: "top", label: "Stream slot taken" }),
    flowEdge("resv-429", "RESV", "DENYRESV", { sourceHandle: "right", targetHandle: "left", kind: "blocked" }),
    flowEdge("resv-cred", "RESV", "CRED", {
      type: "phaseHop",
      sourceHandle: "bottom",
      targetHandle: "top",
      label: "LLM tokens reserved",
      data: { exitX: 1700, viaY: 1185, labelX: 1745, labelY: 1250 },
      zIndex: 4,
    }),

    /* Lane 05 -- AI Provider Call */
    flowEdge("cred-vault-fail", "CRED", "DENYVAULT", { sourceHandle: "right", targetHandle: "left", kind: "blocked" }),
    flowEdge("cred-callbox", "CRED", "CALLBOX", { sourceHandle: "bottom", targetHandle: "top", label: "Key in hand" }),
    flowEdge("callbox-send", "CALLBOX", "SEND", {
      type: "phaseHop",
      targetHandle: "top",
      label: "Response received",
      data: { exitX: 2140, viaY: 1185, labelX: 2185, labelY: 1250 },
      zIndex: 4,
    }),

    /* Lane 06 -- Response & Wrap-Up */
    flowEdge("send-done", "SEND", "DONE", { sourceHandle: "bottom", targetHandle: "top", label: "Sent OK" }),
    flowEdge("send-mid", "SEND", "MIDSTREAM", { sourceHandle: "right", targetHandle: "left", kind: "blocked" }),
    flowEdge("mid-done", "MIDSTREAM", "DONE", {
      type: "phaseHop",
      sourceHandle: "bottom",
      targetHandle: "left",
      label: "Cleanup still runs",
      data: { exitX: 2600, viaY: 1680, labelX: 2550, labelY: 1620 },
      zIndex: 4,
    }),
    flowEdge("callbox-done", "CALLBOX", "DONE", {
      type: "phaseHop",
      sourceHandle: "bottom",
      targetHandle: "left",
      kind: "blocked",
      label: "Failed before first byte",
      data: { exitX: 1885, viaY: 1720, labelX: 2035, labelY: 1720 },
      zIndex: 4,
    }),
    flowEdge("done-gap", "DONE", "GAP", { sourceHandle: "bottom", targetHandle: "top", kind: "blocked", label: "Known limitation" }),

    /* Lane C -- Manage The Platform. Linear, with one optional branch:
       only a request that included a credential visits Vault. */
    flowEdge("mreq-mident", "MREQ", "MIDENT", { sourceHandle: "right", targetHandle: "left" }),
    flowEdge("mident-mscope", "MIDENT", "MSCOPE", { sourceHandle: "right", targetHandle: "left" }),
    flowEdge("mscope-mref", "MSCOPE", "MREF", { sourceHandle: "right", targetHandle: "left", label: "Scope approved" }),
    flowEdge("mref-mwrite", "MREF", "MWRITE", { sourceHandle: "right", targetHandle: "left" }),
    flowEdge("mwrite-msecret", "MWRITE", "MSECRET", { sourceHandle: "right", targetHandle: "left", label: "Includes a credential" }),
    flowEdge("mwrite-mcache-bypass", "MWRITE", "MCACHE", {
      type: "lowerCorridor",
      sourceHandle: "bottom",
      kind: "evidence",
      label: "No credential -- skip Vault",
      data: { leftRailX: 1670, rightRailX: 2120, corridorY: 2640, labelX: 1895, labelY: 2640 },
      zIndex: 4,
    }),
    flowEdge("msecret-mcache", "MSECRET", "MCACHE", { sourceHandle: "right", targetHandle: "left" }),
    flowEdge("mcache-mdone", "MCACHE", "MDONE", { sourceHandle: "right", targetHandle: "left" }),

    /* Lane D -- Health Check. Linear; Postgres and Redis are actually
       checked at the same time, noted in each box's own detail rather
       than drawn as a fork the reader has to untangle. */
    flowEdge("hreq-hpg", "HREQ", "HPG", { sourceHandle: "right", targetHandle: "left" }),
    flowEdge("hpg-hredis", "HPG", "HREDIS", { sourceHandle: "right", targetHandle: "left" }),
    flowEdge("hredis-hdone", "HREDIS", "HDONE", { sourceHandle: "right", targetHandle: "left" }),
  ];

  return { nodes, edges };
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

const BUBBLE_HOVER_DELAY = 1000;
let bubbleShowTimer = null;
let bubbleHideTimer = null;

function cancelBubbleShow() {
  if (bubbleShowTimer) window.clearTimeout(bubbleShowTimer);
  bubbleShowTimer = null;
}

function keepBubbleOpen() {
  if (bubbleHideTimer) window.clearTimeout(bubbleHideTimer);
  bubbleHideTimer = null;
}

function hideBubble(delay = 110) {
  cancelBubbleShow();
  const bubble = document.getElementById("fullFlowBubble");
  if (!bubble) return;
  keepBubbleOpen();
  bubbleHideTimer = window.setTimeout(() => {
    bubble.classList.remove("is-open");
    window.setTimeout(() => {
      if (!bubble.classList.contains("is-open")) bubble.hidden = true;
    }, 140);
  }, delay);
}

function showBubble(nodeElement, detail) {
  cancelBubbleShow();
  const shell = document.getElementById("fullFlowShell");
  const bubble = document.getElementById("fullFlowBubble");
  if (!shell || !bubble || !nodeElement || !detail) return;
  keepBubbleOpen();

  const body = detail.paragraphs?.length
    ? `<div class="full-flow-bubble-copy">${detail.paragraphs.map((paragraph) => `<p>${escapeHtml(paragraph)}</p>`).join("")}</div>`
    : `<p>${escapeHtml(detail.sub)}</p>`;

  bubble.innerHTML = `
    <span>${escapeHtml(detail.tag || "Component detail")}</span>
    <h3>${escapeHtml(detail.title)}</h3>
    ${body}
  `;
  bubble.onpointerenter = keepBubbleOpen;
  bubble.onpointerleave = () => hideBubble();
  bubble.onwheel = (event) => event.stopPropagation();
  bubble.hidden = false;

  const shellRect = shell.getBoundingClientRect();
  const nodeRect = nodeElement.getBoundingClientRect();
  const bubbleRect = bubble.getBoundingClientRect();
  const gutter = 16;
  let left = nodeRect.right - shellRect.left + 14;
  if (left + bubbleRect.width > shellRect.width - gutter) {
    left = nodeRect.left - shellRect.left - bubbleRect.width - 14;
  }
  left = Math.max(gutter, Math.min(left, shellRect.width - bubbleRect.width - gutter));
  let top = nodeRect.top - shellRect.top + nodeRect.height / 2 - bubbleRect.height / 2;
  top = Math.max(gutter, Math.min(top, shellRect.height - bubbleRect.height - gutter));
  bubble.style.left = `${left}px`;
  bubble.style.top = `${top}px`;
  requestAnimationFrame(() => bubble.classList.add("is-open"));
}

function scheduleBubble(nodeElement, detail) {
  cancelBubbleShow();
  bubbleShowTimer = window.setTimeout(() => {
    bubbleShowTimer = null;
    showBubble(nodeElement, detail);
  }, BUBBLE_HOVER_DELAY);
}

function FullHldFlow() {
  const onHover = React.useCallback((nodeElement, nodeId, immediate = false) => {
    const detail = DETAILS[nodeId];
    if (!detail) return;
    const bubbleDetail = { ...detail, tag: LAYOUT[nodeId]?.tag };
    if (immediate) showBubble(nodeElement, bubbleDetail);
    else scheduleBubble(nodeElement, bubbleDetail);
  }, []);
  const onLeave = React.useCallback(() => hideBubble(), []);
  const { nodes, edges } = React.useMemo(() => buildElements(onHover, onLeave), [onHover, onLeave]);

  const handleMouseEnter = React.useCallback((event, node) => {
    if (node.type !== "phase") onHover(event.currentTarget || event.target, node.id);
  }, [onHover]);
  const handleMouseLeave = React.useCallback(() => onLeave(), [onLeave]);

  return h(
    ReactFlow,
    {
      nodes,
      edges,
      nodeTypes: NODE_TYPES,
      edgeTypes: EDGE_TYPES,
      defaultViewport: { x: 28, y: 12, zoom: 0.72 },
      fitView: true,
      fitViewOptions: { padding: 0.06 },
      minZoom: 0.15,
      maxZoom: 1.5,
      nodesDraggable: false,
      nodesConnectable: false,
      elementsSelectable: false,
      panOnScroll: true,
      panOnScrollMode: "free",
      panOnScrollSpeed: 0.9,
      zoomOnScroll: false,
      zoomOnPinch: true,
      zoomOnDoubleClick: false,
      panOnDrag: true,
      preventScrolling: true,
      proOptions: { hideAttribution: false },
      onNodeMouseEnter: handleMouseEnter,
      onNodeMouseLeave: handleMouseLeave,
      onMoveStart: () => hideBubble(0),
    },
    h(Background, { gap: 24, size: 1, color: "rgba(16, 22, 20, 0.07)" }),
    h(Controls, { showInteractive: false, position: "bottom-right" })
  );
}

let root = null;

window.mountFullFlowReactFlow = function mountFullFlowReactFlow() {
  const container = document.getElementById("fullFlowReactFlow");
  if (!container) return;
  if (!root) root = createRoot(container);
  root.render(h(FullHldFlow));
};

window.unmountFullFlowReactFlow = function unmountFullFlowReactFlow() {
  hideBubble();
  if (!root) return;
  root.unmount();
  root = null;
};
