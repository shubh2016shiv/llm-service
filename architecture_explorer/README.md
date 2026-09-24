# llm_services — Architecture Explorer

An interviewer-facing, three-level walkthrough of a multi-tenant LLM gateway: many customers, each
with their own credentials, models and limits, reaching many model providers through one API that
never lets one tenant spend, read or break another tenant's anything. Vanilla HTML, CSS and
JavaScript — no build step, no packages, no network calls except the React Flow CDN modules used by
the end-to-end canvas.

## Run it

Serve the folder, then open `http://127.0.0.1:4182/`:

```powershell
npm run dev
```

That runs `python serve.py 4182` — there are no dependencies to install, only Python on the PATH.
Serve it rather than double-clicking `index.html`: the Full flow canvas loads as an ES module, which
browsers block on `file://` URLs.

## What the system does

A client sends a chat, embedding or rerank request naming only a tenant and a deployment key. It
never names a provider, a model, an endpoint, a credential or a budget — and it is deliberately not
allowed to. Everything that decides what actually happens is resolved server-side, in an order where
each answer is only reachable once the previous one has been proven: who the caller is, whether this
tenant may use this deployment, which entitlement grants it, which provider and model that
entitlement points at, which credential sits behind it in the vault, and whether there is capacity
left against a ceiling shared by every replica.

Three properties carry the argument:

- **Isolation is a sequence, not a flag.** Four ordered gates read four separate sources of truth,
  and the first failure is the one the caller hears about. There is no partial pass and no fallback
  to a weaker grant.
- **Caching stops exactly where security begins.** An authorization decision is cached against four
  version markers and stored with a compare-and-set, so a revocation kills every dependent grant
  without enumerating them. Tenant status and entitlement status are read uncached on every request,
  because those are the two values a revocation actually moves.
- **Every ending settles the same way.** A stream that completes, fails, is cancelled, or whose
  client simply vanished all run the same cleanup exactly once — provider stream closed, usage
  reconciled, capacity slot returned.

## The three levels

| Level | What it shows | How you get there |
| --- | --- | --- |
| **1 · System** | Eight pipeline stages, four cross-cutting concerns, and the topology decision | The landing page |
| **2 · Stage** | The stage's mechanism, design decision, failure controlled, trade-off accepted, talk track | Click a stage card |
| **3 · Component** | What a component owns, what it is forbidden from doing, what it receives, how its output is validated, and a "passes every check, still wrong" example | Click an outlined node in the mechanism ladder |

The eight rail stages are Bootstrap (settings root, config loader, exit stack, app state), Admission
(request context, body limit, error boundary), Identity (JWT validator, role guard, token issuer),
Authorization (four gates, grant cache, version invalidation), Resolve Deployment (config read, capability check,
route fingerprint), Quota (reservation, endpoint binding, finalization), Execution (provider
registry, transport factory, credential resolution, circuit breaker) and Delivery (capacity lease,
streaming session, SSE delivery).

Four stages sit off the rail on the **Cross-cutting** strip, and are also reachable by deep link:
Control Plane (`?stage=controlplane`), Secret Management (`?stage=secrets`), Persistence & Schema
(`?stage=persistence`) and Why Two Services (`?stage=topology`).

## Full flow diagram

**Full flow** opens a horizontal React Flow canvas — a genuine HLD, not the detailed rail collapsed
onto one screen. No latency figures appear anywhere on it: the service has no published benchmark, and
the terms that would dominate a real measurement vary by deployment, provider and model — quoting a
number would be inventing one.

Earlier drafts of this canvas told the story of exactly one request shape, `POST
/llm/chat`, as if it were the only kind of traffic this service handles. It isn't. The canvas is now
four lanes, stacked top to bottom — lanes by *request shape*, not the actor swimlanes of a
sequence diagram, because a `GET /health` check, a `POST /auth/sign-in`, a
streaming chat completion and a `PATCH` to a tenant's deployment take four genuinely different paths
through the infrastructure:

- **Lane A · Sign In** — two unauthenticated doors that join at one in-process mint.
  `POST /auth/sign-in` checks a Redis failure budget, reads the credential row from
  PostgreSQL, and judges the password in this process. `POST /auth/guest-session` checks
  configuration only and never touches Redis or PostgreSQL. A spent budget, a rejected
  credential, and a closed guest door each end the lane (429, 401, 403). The password path is
  numbered 1-6 and runs dead straight; the guest door is 1b and runs along its own clear row into
  step 5. Nothing is drawn between the two doors, because nothing flows between them — they are
  separate routes (``sign_in_with_password`` and ``sign_in_as_guest``), not a fallback. All three
  refusals sit in one row along the bottom, so the eye never leaves the happy path to find them.
- **Lanes 01–06 · Run Inference** — `POST /llm/chat`, `/embed`, `/rerank`. The only lane complex
  enough to need six internal sub-phases of its own (Request In, Access Control, Resolve Deployment, Capacity
  Check, AI Provider Call, Response & Wrap-Up) — which is itself the point: most requests this
  service handles are much simpler than a chat call.
- **Lane C · Manage The Platform** — `GET`/`POST`/`PATCH`/`DELETE` across tenants, users, providers,
  models, deployments and entitlements. A different identity check feeds a different authorization
  question ("may you administer this", not "may you infer") than the Inference lane's four gates.
- **Lane D · Health Check** — `GET /health`, `GET /health/ready`. No identity, no cache, no provider
  — the shortest lane on the canvas, by design.

A small precondition note floats above all four lanes with no outgoing edges of its own: the process
already has its database pool, cache connection, secret client and provider catalog built before any
of the four request shapes below it arrives.

Infrastructure is not a legend below the diagram — it is inline, at the exact step a request actually
crosses a process boundary. A dashed border and a `→ System Name` chip mark every one of those steps,
fifteen boxes in total: PostgreSQL six times (the sign-in credential read, the inference
access-control gates, inference's second and deliberately-uncached routing re-read, the management
write path's reference check and the write itself, and the health-check readiness ping), Redis four
times (the sign-in failure budget, the inference permission cache, cache invalidation after an
administrative change, and the health-check readiness ping), Vault twice (one read-only fetch in
Inference, one write-only fetch in Manage), the sibling Token Manager service twice (reserve, then
release), and the AI provider itself once. Recording and clearing that sign-in failure count are
drawn on the edges out of the judgment, not as extra boxes. The branches worth showing on a first
read are there too: the guest door skipping budget and database to join the mint, the three sign-in
refusals, a red path from the provider call straight to cleanup for a call that failed, timed out or lost its
caller (the box claims it always runs, so it needed an arrow proving it), a cache hit that skips the four database checks in Access Control and rejoins at step 6 (it never
skips the configuration read, and a revocation advances a version seal that voids the stored yes at
once), a loop-back that writes a fresh database answer into that same cache, and a branch in Manage The Platform for the one case that
touches Vault — a request that actually included a provider credential.

Components are grouped by lane colour, with the six Run Inference sub-phases keeping their own
distinct colours within that lane. Hover or keyboard-focus any component for a compact explanation;
the canvas deliberately does not navigate into another page. Drag to pan, use the built-in controls to
zoom or fit, and Escape or × to close. The canvas opens already fitted to the window — with four lanes
stacked vertically it's taller than it is wide, so a fit-to-view on open matters more than it did for
a single horizontal lane.

## Presentation mechanics

- **Zoom captions** — a one-clause line lands with each zoom and fades within two seconds.
- **Push on this** — a collapsed section of real interview questions with candidate answers, mapped
  to the component in view. Fourteen probes, each written to concede the strongest counterargument
  rather than defend the design uncritically.
- **Quiet mode** — when the failure and trade-off content comes into view, ambient motion stops and
  the mechanism section recedes, so the interface changes register between "here is the mechanism"
  and "here is the honest judgment call".
- **Zoom out to the whole system** — a deliberate close that reverses the zoom and lands back on the
  hero claim, distinct from the plain × close.

## Navigation

- Breadcrumb at every panel: System / Stage / Component, each segment steps back one level.
- Deep links: `?stage=authorization`, `?stage=execution&component=circuit-breaker`.
- Browser back and forward step through System → Stage → Component naturally.
- Click any stage card to open it, or use **Previous / Next** below the map to move the highlight.
- **← / →** move between sibling stages or components once a panel is open.
- **Esc** goes back exactly one level: component → stage → overview.
- **Present** toggles browser fullscreen.
- Motion, captions and transitions honor the operating system's reduced-motion setting.

## Suggested live sequence

1. Deliver the hero claim in under thirty seconds: a request that names a tenant and a deployment key
   and nothing else, and a server that resolves everything else before a provider is dialled.
2. Open **Authorize** first — it is the densest decision in the system, carrying the four gates, the
   versioned cache and the compare-and-set race fix at once.
3. Zoom into **Version Markers** and make the invalidation argument concretely: revoking access is one
   write, it costs the same for one grant or ten thousand, and it fails closed rather than open.
4. Step to **Route** and volunteer the apparent redundancy — yes, the entitlement is read twice, and
   the reason is that only one of the two questions is safe to cache.
5. Open **Execute** and concede the breaker: state is per process, each replica discovers an outage
   separately, and the alternative would put a blocking Redis call in the event loop.
6. Use **Push on this** when you want to invite the harder question rather than wait for it. The probe
   under Quota — "how would you know if the ledger had drifted" — is the one a strong interviewer
   reaches for anyway, so it is better volunteered.
7. Close with **Zoom out to the whole system**.

Optimised for a 16:9 desktop display; usable down to tablet and mobile widths.

## Folder contents

- `index.html` — page shell, hero, rail, cross-cutting strip, panel and full-flow dialog.
- `content.js` — all content: the probe bank, twelve stages, thirty-six component contracts.
- `app.js` — the rendering and navigation engine. Content-agnostic.
- `full-flow.js` — the consolidated end-to-end HLD as a React Flow canvas.
- `styles.css` — the shared design system, carried over unmodified from the sibling explorer.
- `explorer.css` — this explorer's adaptations: eight rail cards instead of seven, a pure-CSS stage
  motif in place of the illustrated sprite, one extra diagram colour family, and the cross-cutting
  strip.
- `serve.py` — dev server that disables caching, so an edited stylesheet is never served stale.

The engine/content split is deliberate: `app.js` and `styles.css` carry no domain knowledge, so
another explorer needs only a new `content.js` and a new `full-flow.js`.

## Scope note

This describes the architecture of the `llm_services` gateway in this repository. Tenant names,
deployment keys and identifiers used as examples are synthetic. The mechanisms, their ordering, the
failure handling and the trade-offs are drawn from the service's own source. The site documents design
decisions and their costs, not an implementation guide — and where a control is missing, it is named
rather than omitted.
