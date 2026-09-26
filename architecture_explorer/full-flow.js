/* ============================================================
   Full HLD: horizontal, explorable React Flow canvas
   ============================================================
   Three request shapes, stacked as swimlanes top to bottom — because
   "the request" this service handles is not one thing. A POST sign-in,
   a POST chat completion and a PATCH to a tenant's deployment take three
   different paths through the infrastructure, and showing only the busiest (as
   an earlier version of this canvas did) implied every request looks
   like a chat completion. It doesn't.

   Lane A — Sign In            POST /auth/sign-in, /guest-session
   Lanes 01–06 — Run Inference POST /llm/chat, /embed, /rerank
   Lane C — Manage Tenants & Deployments. GET/POST/PATCH/DELETE across
                                /tenants, /users, /providers,
                                /deployments, /entitlements

   Run Inference is the one lane complex enough to need six internal
   sub-phases of its own (Request In, Access Control, Resolve Deployment,
   Capacity Check, AI Provider Call, Response & Wrap-Up). Sign In is
   the other lane that branches: two unauthenticated entry points join
   just before one in-process token issuer, and each decision that can
   stop the request has its refusal drawn.

   Infrastructure is not a separate legend below the diagram — it is
   inline, at the exact step where a request actually crosses a
   process boundary. Those steps carry a dashed border and a small
   "→ System Name" chip (see .hld-node--hop / .hld-hop-chip in
   explorer.css) so a reader can tell, at a glance, which boxes are
   this service's own logic and which ones are a network hop to
   PostgreSQL, Redis, Vault, the sibling Token Manager service, or
   the AI provider itself.

   A small precondition note floats above all three lanes: the process
   already has its database pool, cache connection, secret client and
   provider catalog built before any of the three request shapes below
   arrives. It has no outgoing edges — it applies to all three lanes
   equally, not to one more than another.

   Nodes never navigate away: hover and keyboard focus reveal a
   compact bubble instead.

   Phase keys reuse the stylesheet's colour families for the six
   Run Inference sub-phases (inputs / knowledge / context / plan /
   execute / post), plus the boot, authn and manage families used by
   the precondition and the other two lanes.
   ============================================================ */

import React from "react";
import { createRoot } from "react-dom/client";
import { ReactFlow, Background, Controls, Handle, Position, MarkerType, BaseEdge } from "@xyflow/react";

const h = React.createElement;

/* Standalone lane captions sit in the whitespace above each lane. They are
   deliberately not phase headings and not cards: the dotted rule establishes
   the lane boundary before the reader reaches its diagram. */
const LANE_HEADERS = {
  concepts: { label: "Definition and Key Concepts", meaning: "Shared vocabulary and service-wide preconditions", x: 0, y: 0, w: 3410 },
  laneA: { label: "Lane A", meaning: "Sign In — create a bearer token", x: 0, y: 220, w: 2440 },
  laneB: { label: "Lane B", meaning: "Run Inference — authorize, resolve, execute and wrap up an AI request", x: 0, y: 1200, w: 3010 },
  laneC: { label: "Lane C", meaning: "Manage Tenants & Deployments — administrative configuration and access changes", x: 0, y: 2290, w: 3010 },
};

/* Six real phases. No "boot" column here — startup is a precondition
   note floating above phase one, not a step a request takes. */
const PHASES = {
  /* Three request shapes, stacked as swimlanes top to bottom. Sign In is
     the one lane with two entry points and three ways to be refused —
     everything else is one straight line. Run Inference is the lane
     with six internal sub-phases — most requests are simpler than a
     chat call, and sign-in is simpler still, but it still branches. */

  /* 2440 wide, not 2160: the six steps need a gap big enough to hold a
     decision label without it touching the card on either side. */
  authn:  { index: "A", title: "Sign In", note: "HOW DOES A CALLER GET A TOKEN? Every other lane below needs a token already. This is the only lane that creates one. Two separate doors lead to it.", x: 0, y: 260, w: 2440, h: 900 },

  inputs:    { index: "01", title: "Request In", note: "STEP 1 · WHO ARE YOU? Show a sign-in token and the server checks it is genuine. The tenant and deployment headers are only what you are asking for — not proof.", x: 0,    y: 1240, w: 300, h: 900 },
  knowledge: { index: "02", title: "Access Control", note: "STEP 2 · ARE YOU ALLOWED? Your identity is proven; your access is not. May this USER use this DEPLOYMENT, inside this TENANT? Answered from a brief memory of a past yes, or checked fresh in the database.", x: 340,  y: 1240, w: 610, h: 1010 },
  context:   { index: "03", title: "Resolve Deployment", note: "STEP 3 · WHAT DOES THAT DEPLOYMENT KEY MEAN? Step 2 proved you may use the name. Resolving turns that name into one concrete plan — provider, model, URL, key, limits — allowed for this tenant and able to do this job, then frozen.", x: 990,  y: 1240, w: 420, h: 1010 },
  /* "Capacity Check" never said capacity OF WHAT, which made this the hardest
     phase to read cold: two unrelated limits share one word. The title now
     names both limits instead of the word they get mistaken for, and the two
     cards below answer to it in identical shape — Counts / Kept by / Asked by
     / When full — so the differences are the only thing that varies between
     them. */
  plan:      { index: "04", title: "Connections & Tokens", note: "STEP 4 · TWO LIMITS, NOT ONE — and they guard different resources. 8a is CONCURRENCY: can this one process take another live connection? Counted in its own memory, shared with nobody. 8b is WORKLOAD: can the platform reserve enough tokens? Counted fleet-wide by Token Manager. Passing one says nothing about the other. Neither is about money.", x: 1450, y: 1240, w: 540, h: 1010 },
  execute:   { index: "05", title: "AI Provider Call", note: "RUN IT. Fetch the API key from Vault, then call the vendor named in the Execution Plan.", x: 2010, y: 1240, w: 420, h: 1010 },
  post:      { index: "06", title: "Response & Wrap-Up", note: "Send the response. Always release the streaming slot in this process (if we took one) and report how many LLM tokens were really used — even if the stream failed halfway.", x: 2450, y: 1240, w: 560, h: 1010 },

  manage: { index: "C", title: "Manage Tenants & Deployments", note: "WHERE A DEPLOYMENT OR ENTITLEMENT ACTUALLY COMES FROM. An administrator's CRUD API — GET · POST · PATCH · DELETE across tenants, users, providers, models, deployments and entitlements. Every row Run Inference reads above was created here first, by an admin, never by an end user's own request.", x: 0, y: 2330, w: 3010, h: 570 },

};

/* `hop` marks a step that leaves this process for another system —
   rendered with a dashed border and a "→ System" chip by StepCard.
   Everything without `hop` is this service's own in-process logic. */
const LAYOUT = {
  /* y=8, not 20: the glossary cards to the right are four lines tall and at
     y=20 their bottom edge landed 1px inside Lane A. All four top-margin
     cards move up together so their top edges still line up. */
  START:   { phase: "boot", icon: "repository", tag: "BEFORE ANY REQUEST", x: 40,   y: 50,  w: 280 },

  /* Glossary. Three reference cards sharing the top margin with START —
     same "boot" phase, so no panel sits behind them, and no edge touches
     them: they are not a step and must never read as one, which is what
     the NOT A STEP tag says out loud. They sit here because the canvas
     opens at its top-left, so the nouns are on screen before the numbered
     flow below uses them. Definitions only — no mechanism, no file names,
     no status codes; every one of those lives on the step that owns it. */
  GLOSSWHO:  { phase: "boot", icon: "documents", tag: "GLOSSARY · NOT A STEP", x: 360,  y: 50, w: 660 },
  GLOSSWHAT: { phase: "boot", icon: "documents", tag: "GLOSSARY · NOT A STEP", x: 1060, y: 50, w: 660 },
  GLOSSRUN:  { phase: "boot", icon: "documents", tag: "GLOSSARY · NOT A STEP", x: 1760, y: 50, w: 660 },

  /* The key -> entitlement -> plan chain, in the same reference row and for the
     same reason: a reader has otherwise to infer it from four separate
     cards spread across phases 02 and 03. Four lines only — the correction
     that matters (the dialled values come from the entitlement, NOT from
     the deployment row) is too long for a card and lives in the hover. */
  CHAIN:     { phase: "boot", icon: "assembly", tag: "RELATIONSHIP · NOT A STEP", x: 2450, y: 50, w: 540 },
  ARROWLEGEND: { phase: "boot", icon: "documents", tag: "LEGEND · NOT A STEP", x: 3030, y: 50, w: 380 },

  /* Lane A — Sign In. Three bands, and the band a box sits in is what it
     means, so a reader can read the shape before reading a word:

       y=318  the password path, left to right, six steps
       y=558  the second door (guest), in the same column as the first door
       y=808  every refusal, on one shared baseline

     Row 1 sits 148px below the panel header — matching REQIN's offset in
     Request In below (the second swimlane's own entry-point row), so the
     two lanes' "step 1" boxes read at the same relative height inside
     their own panels instead of Sign In's happy path floating high.

     The six steps share one width (236) and one gap (160) so the row reads
     as a single pipeline rather than a set of clumps — an earlier version
     mixed 55px and 242px gaps, which made steps 3-4 and 5-6 look fused.
     The gap is 160 rather than something tighter because a decision label
     sits in it, and a label touching the cards on both sides reads as a
     third box rather than as a note on the arrow.
     Each refusal sits in its own gate's column, so a refusal is always
     straight down from the question that caused it, never a diagonal a
     reader has to trace. Row heights are pinned (see `h`) so every
     connector between two steps is a straight line.

     Every box uses a plain verb — "check", "read", "issue" — never a
     metaphor that has to be decoded before the mechanism can be read. */
  ENTRY:   { phase: "authn", icon: "description", tag: "POST /auth/sign-in", x: 112,  y: 408, w: 236, h: 150 },
  LIMIT:   { phase: "authn", icon: "queue", tag: "FAILED ATTEMPTS ONLY", x: 508,  y: 408, w: 236, h: 150, gate: true, hop: "Redis" },
  READ:    { phase: "authn", icon: "database", tag: "PASSWORD HASH + ROLE", x: 904,  y: 408, w: 236, h: 150, hop: "PostgreSQL" },
  VERIFY:  { phase: "authn", icon: "decision", tag: "SAME ERROR EITHER WAY", x: 1300, y: 408, w: 236, h: 150, gate: true },
  ISSUE:   { phase: "authn", icon: "guard", tag: "SIGNED, TIME-BOUNDED", x: 1696, y: 408, w: 236, h: 150 },
  TOKENOUT:{ phase: "authn", icon: "accept", tag: "200 OK", x: 2092, y: 408, w: 236, h: 150 },

  GUEST:   { phase: "authn", icon: "description", tag: "POST /auth/guest-session", x: 112, y: 648, w: 236, h: 136, gate: true },

  STOP403: { phase: "authn", icon: "gap", tag: "403", x: 112,  y: 898, w: 236, h: 124, blocked: true },
  STOP429: { phase: "authn", icon: "gap", tag: "429", x: 508,  y: 898, w: 236, h: 124, blocked: true },
  STOP401: { phase: "authn", icon: "gap", tag: "401", x: 1300, y: 898, w: 236, h: 124, blocked: true },

  /* Lane 01 — Request In. What arrives, then the one gate that can stop it
     here. A bad token ends in this column and never reaches Access Control.

     Drawn top to bottom in the order the code actually runs: the bearer
     token is proven FIRST, and only then is the shape of the two headers
     validated. An earlier version of this column's text had that backwards.

     Same rhythm as Lane A: one card height for the two steps, one gap
     (132) between every pair, and the refusal directly below the gate that
     produced it. */
  REQIN:   { phase: "inputs", icon: "description", tag: "ONE ENTRY POINT", x: 22, y: 1388, w: 256, h: 156 },
  IDENT:   { phase: "inputs", icon: "guard", tag: "AUTHENTICATION", x: 22, y: 1676, w: 256, h: 156, gate: true },
  DENY401: { phase: "inputs", icon: "gap", tag: "401", x: 22, y: 1964, w: 256, h: 124, blocked: true },

  /* Lane 02 — Access Control. One question, then the two ways it can be
     answered, stacked in the order they are tried: the cache first, the
     database only if the cache had nothing. Both yes-paths meet at one
     merge card so the next phase never looks like it began mid-decision.

     Uniform 68px gaps down the column. APPROVED's vertical centre is set
     to CACHE's vertical centre on purpose — that is what makes the
     "Remembered" hit a dead straight horizontal line rather than a dogleg,
     and a straight line is the fastest way to show that a cache hit skips
     the database entirely. Moving either card means recomputing the other. */
  AUTHCTX: { phase: "knowledge", icon: "decision", tag: "THE THREE INPUTS", x: 360, y: 1345, w: 300, h: 165, gate: true },
  CACHE:   { phase: "knowledge", icon: "queue", tag: "YES ONLY · BRIEF", x: 360,  y: 1578, w: 300, h: 136, hop: "Redis" },
  GATES:   { phase: "knowledge", icon: "database", tag: "FOUR QUESTIONS · IN ORDER", x: 360,  y: 1782, w: 300, h: 165, gate: true, hop: "PostgreSQL" },
  APPROVED:{ phase: "knowledge", icon: "accept", tag: "MERGE POINT", x: 760, y: 1561, w: 170, h: 170, gate: true },
  DENYACL: { phase: "knowledge", icon: "gap", tag: "403 · 404 · 422", x: 360, y: 2015, w: 300, h: 180, blocked: true },

  /* Lane 03 — Resolve Deployment. The most congested column on the canvas,
     and the only one where two cards had been left touching edge to edge
     (LIVE ended at 1491 and PLANBOX began at 1491), which read as one tall
     box rather than two steps. The lane went 310 -> 380 wide and every phase
     to its right moved 100 to the right to pay for it. */
  LIVE:    { phase: "context", icon: "database", tag: "PERMISSION · EVERY CALL", x: 1020,  y: 1370, w: 320, h: 212, hop: "PostgreSQL" },
  PLANBOX: { phase: "context", icon: "decision", tag: "ALLOW · KNOWN · CAPABLE", x: 1020,  y: 1623, w: 320, h: 188, gate: true },
  RECIPE:  { phase: "context", icon: "assembly", tag: "FIXED FOR THIS REQUEST", x: 1020,  y: 1852, w: 320, h: 154 },
  DENY422: { phase: "context", icon: "gap", tag: "403 · 422 · 500", x: 1020, y: 2047, w: 320, h: 172, blocked: true },

  /* Two columns: the checks on the left, what refuses them on the right, each
     refusal's top aligned to the check that raises it. RESV raises two very
     different refusals, so it gets two boxes stacked beneath one another —
     they used to overlap by 39px, and the lower one escaped the panel
     entirely (bottom 2020 against a panel ending at 1960). */
  SLOT:    { phase: "plan", icon: "queue", tag: "CONCURRENCY · THIS PROCESS", x: 1485, y: 1410, w: 250, h: 216 },
  DENYSLOT:{ phase: "plan", icon: "gap", tag: "503", x: 1770, y: 1410, w: 200, h: 182, blocked: true },
  RESV:    { phase: "plan", icon: "external", tag: "WORKLOAD · FLEET-WIDE", x: 1485, y: 1710, w: 250, h: 238, gate: true, hop: "Token Manager Service" },
  DENYRESV:{ phase: "plan", icon: "gap", tag: "429", x: 1770, y: 1710, w: 200, h: 202, blocked: true },
  RESVDOWN:{ phase: "plan", icon: "gap", tag: "503", x: 1770, y: 1982, w: 200, h: 202, blocked: true },

  CRED:    { phase: "execute", icon: "guard", tag: "API KEY", x: 2035, y: 1410, w: 240, hop: "Vault" },
  DENYVAULT:{ phase: "execute", icon: "gap", tag: "503", x: 2290, y: 1410, w: 130, blocked: true },
  CALLBOX: { phase: "execute", icon: "logic", tag: "OPENAI · ANTHROPIC · …", x: 2035, y: 1685, w: 280, gate: true, hop: "AI Provider" },

  SEND:    { phase: "post", icon: "runtime", tag: "CLIENT CONTRACT", x: 2475, y: 1410, w: 280 },
  MIDSTREAM:{ phase: "post", icon: "gap", tag: "AFTER 200 SENT", x: 2780, y: 1410, w: 200, blocked: true },
  DONE:    { phase: "post", icon: "accept", tag: "ALWAYS ONCE", x: 2475, y: 1685, w: 280, gate: true, hop: "Token Manager Service" },
  GAP:     { phase: "post", icon: "gap", tag: "KNOWN LIMITATION", x: 2475, y: 1930, w: 280, blocked: true },

  /* Lane C — Manage Tenants & Deployments. A different identity check (the same
     bearer-token verification the Inference lane uses) feeds a different
     authorization question, then a linear write path with one optional
     branch: only deployment and entitlement writes touch Vault. */
  /* All eight cards share one width (260) and one pinned height (130, the
     tallest natural content — MSCOPE's two paragraphs — plus a small
     margin). Without a shared h, cards auto-size to their own text, their
     handles land at different vertical midpoints, and every connector
     between them renders as a slight S-bend instead of a straight line. */
  MREQ:    { phase: "manage", icon: "description", tag: "GET · POST · PATCH · DELETE", x: 35,   y: 2440, w: 260, h: 130 },
  MIDENT:  { phase: "manage", icon: "guard", tag: "SAME BEARER TOKEN CHECK", x: 355,  y: 2440, w: 260, h: 130 },
  MSCOPE:  { phase: "manage", icon: "decision", tag: "PLATFORM-WIDE ROLE OR TENANT ADMIN", x: 675,  y: 2440, w: 260, h: 130, gate: true },
  MREF:    { phase: "manage", icon: "database", tag: "DOES THE ID EXIST?", x: 1120, y: 2440, w: 260, h: 130, hop: "PostgreSQL" },
  MWRITE:  { phase: "manage", icon: "database", tag: "CREATE · UPDATE · DELETE", x: 1440, y: 2440, w: 260, h: 130, gate: true, hop: "PostgreSQL" },
  MSECRET: { phase: "manage", icon: "guard", tag: "ONLY IF A KEY WAS INCLUDED", x: 1900, y: 2440, w: 260, h: 130, hop: "Vault" },
  MCACHE:  { phase: "manage", icon: "queue", tag: "SO INFERENCE SEES IT NEXT TIME", x: 2220, y: 2440, w: 260, h: 130, hop: "Redis" },
  MDONE:   { phase: "manage", icon: "accept", tag: "200 · 201 · 204", x: 2540, y: 2440, w: 260, h: 130 },

  /* Management refusals mirror the red dashed stop cards used by the other
     lanes. Keeping each card directly below the gate that owns it makes the
     status mapping visible without disturbing the aligned happy-path row. */
  MIDENT401: { phase: "manage", icon: "gap", tag: "401", x: 355,  y: 2660, w: 260, h: 130, blocked: true },
  MSCOPE403: { phase: "manage", icon: "gap", tag: "403", x: 675,  y: 2660, w: 260, h: 130, blocked: true },
  MREF404:   { phase: "manage", icon: "gap", tag: "404", x: 1120, y: 2660, w: 260, h: 130, blocked: true },
  MWRITE4XX: { phase: "manage", icon: "gap", tag: "400 · 409", x: 1440, y: 2660, w: 260, h: 130, blocked: true },
  MSECRET503:{ phase: "manage", icon: "gap", tag: "503", x: 1900, y: 2660, w: 260, h: 130, blocked: true },
  MCACHE503: { phase: "manage", icon: "gap", tag: "503", x: 2220, y: 2660, w: 260, h: 130, blocked: true },

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

  GLOSSWHO: {
    title: "Key Concepts · Who Is Asking",
    sub: "User — a person's login identity, proven by the sign-in token.\nTenant — one customer organisation; the isolation boundary everything is scoped to.\nCustomer — the same thing as Tenant. There is no separate customer entity.\nEntitlement — the record granting one user one provider + model route in a tenant.",
  },
  GLOSSWHAT: {
    title: "Key Concepts · What They Ask For",
    sub: "Deployment — a tenant's saved AI setup: one provider, one model, its settings.\nDeployment Key — the short name a caller sends to choose one Deployment.\nProvider — an AI vendor this service can call, such as OpenAI or Bedrock.\nModel — one model a provider offers, such as gpt-4o.",
  },
  GLOSSRUN: {
    title: "Key Concepts · What Runs It",
    sub: "Execution Plan — the frozen settings one request runs on, decided before any call.\nStreaming Slot — one live connection slot, counted per process; only so many exist.\nToken Quota — an allowance of LLM tokens, reserved before a call and settled after.\nToken Manager — a separate internal service, shared by every instance, granting token quota.",
  },

  CHAIN: {
    title: "How A Key Becomes A Plan",
    sub: "Request: tenant id + deployment key. The token supplies the user.\nDeployment (tenant+key): must be active; fixes the provider + model that key means.\nEntitlement (tenant+user+key+that provider/model): endpoint URL, credential ref, region.\nExecution Plan: those + catalog defaults, frozen once and read by every later step.",
    paragraphs: [
      "Read the four lines as one sentence: the caller names a key, the key names a deployment, the deployment pins which provider and model that key means, and the entitlement for that exact combination supplies what is actually dialled. Each step narrows the question; none of them re-opens an earlier one. (Not \"grant\" — that word is already taken in this codebase by the Access Control cache, a different thing: the remembered yes from step 4, not this permission record.)",
      "One thing worth being exact about, because the obvious assumption is wrong: the endpoint URL and the credential reference come from the entitlement, not from the deployment row — even though a deployment carries its own copy of both. The routing read joins user_entitlements to the provider and model catalogs and never touches the deployments table at all, and it deliberately never falls back to a deployment credential. The deployment's job is narrower than it looks: prove the key exists and is switched on for this tenant, and fix the provider and model the selected entitlement must agree with.",
      "Why everything downstream reads the plan instead of resolving again: the plan is built once and frozen, carrying one already-decided value per question — which timeout, which temperature, which token ceiling. Capacity, Vault and the provider call all read that same sheet, so no two steps can disagree about what this request is. It also carries a fingerprint of itself, which is what lets the provider layer reuse one cached connection per exact route.",
    ],
  },
  ARROWLEGEND: {
    title: "Reading The Arrows",
    sub: "──── Normal path, step to step\n┄┄┄┄ Failure, red — the request ends here\n┄┄┄┄ Branch, blue — skips a step; read its own label",
  },

  REQIN: {
    title: "1 · Request Arrives",
    /* Three lines, each short enough to survive the card width without
       wrapping — a wrapped fourth line breaks the one-header-per-row
       reading that makes this block scannable at all. */
    sub: "Authorization: Bearer eyJ… ← who you are\nX-Tenant-ID: <tenant id> ← which tenant\nX-Deployment-Key: support-gpt ← the AI setup",
    paragraphs: [
      "One naming note, stated once and then dropped: this canvas uses \"tenant\" as its only word for this from here on — see Key Concepts above. A tenant is the customer boundary; there is no separate customer entity anywhere in the code.",
      "Think of it as arriving at a building with a badge and a destination. Authorization: Bearer <token> is the badge — a sign-in token from Lane A. X-Tenant-ID is the tenant you want to act for (a UUID). X-Deployment-Key names the saved AI setup you want to use inside that tenant — a short name like customer-support-gpt or invoice-summariser, identifying a deployment rather than being one. A deployment is a named provider + model + settings bundle, never a software release.",
      "To be exact about what that key is, since it could mean four different things: it is not something the caller invents on the spot — a key that was never registered simply matches no row later, in Resolve Deployment. It is not the database's own primary key either — that is a separate internal UUID (deployment_id) the caller never sees. It is a human-chosen, URL-safe slug (e.g. gpt4-prod) an administrator registered ahead of time for this tenant, over in Manage Tenants & Deployments — and that act of registering is what created the stored record the key now names: which provider, which model, where its credential lives in Vault, which region, and the timeout / temperature / token-limit defaults for calls made under it. So of the four readings, the last is closest: the key is a mapping, a lookup handle to that whole bundle, not a piece of configuration in itself.",
      "Only the badge proves anything. The tenant and deployment are what the caller is asking for, not something they have shown a right to — naming them is a request. All three routes (chat, embed, rerank) share this one door; whether a deployment can chat but not embed is decided later, in Resolve Deployment.",
      "The deployment key must start with a letter or digit, and may then use letters, digits, dot, dash and underscore, up to 128 characters in all. A key that breaks any of those rules is refused with 422 — as is a tenant id that is not a UUID.",
      "But that shape check runs AFTER the token is proven, not before it. Get both wrong at once and the answer is 401, never 422. The order is not cosmetic: it means a caller who cannot prove who they are never learns whether their tenant id or deployment key was even well-formed.",
      "In the code: require_inference_access in app/api/inference_dependencies.py declares both headers and takes get_current_user as a sub-dependency. FastAPI resolves sub-dependencies before validating the dependant's own parameters, and that is what puts the 401 ahead of the 422.",
    ],
  },
  IDENT: {
    title: "2 · Is The Sign-In Token Genuine?",
    sub: "Like checking a badge is real, unexpired and not forged. Done in memory — no database, no network call.",
    paragraphs: [
      "In the code: get_current_user (app/auth/auth_dependencies.py) calls validate_access_token (app/auth/jwt_token_validator.py). It requires signature, expiry, issuer, audience and token type = \"access\" (a refresh token is refused). Two further refusals are easy to miss: a token older than the configured ceiling is rejected even if it has not expired, and so is one carrying a role this service does not recognise. A correctly signed token can still fail here.",
      "This is the sign-in token, not the LLM-token quota — that is Capacity Check. Two unrelated things are called a token in this system, and this is the one that means identity.",
      "Why not put the tenant on the token itself: a membership row links one user to one tenant, and the same user can hold a separate membership row in another tenant. Baking one tenant into the token would either force a fresh sign-in on every switch or let one token silently act for every tenant the user belongs to. Naming the tenant per request keeps the blast radius of one leaked token to what that single request asked for.",
      "Authentication answers WHO ARE YOU. Authorization, in the next column, answers ARE YOU ALLOWED TO USE THIS. Signature, expiry, issuer and audience are checked here, in memory. A bad token ends at 401 — sign in again in Lane A. A good one proves the person and nothing about which tenant they may act for.",
      "The platform role on the token is not the Access Control decision below. That decision uses the caller's membership role inside the requested tenant.",
    ],
  },
  DENY401: {
    title: "Sign-In Token Rejected",
    sub: "401 — the badge is missing, forged, expired or the wrong kind. Sign in again (Lane A).",
    paragraphs: [
      "No Authorization header (\"Authorization token is required.\"), a bad signature or expired token (\"Token is invalid or has expired.\"), or a token with unusable contents (\"Token format is invalid.\") all stop here with 401. Tenant and deployment are never looked up; Access Control does not run.",
      "This 401 also wins when the tenant or deployment header is malformed at the same moment. The caller is told the token is wrong and learns nothing about the headers, so the response cannot be used to probe what a valid tenant id or deployment key looks like.",
    ],
  },

  AUTHCTX: {
    title: "3 · May This User Use This Deployment?",
    sub: "May this USER use this DEPLOYMENT in this TENANT?\nuser = user_id on your verified token\ntenant = X-Tenant-ID, from the request header\nkey = X-Deployment-Key, identifies a deployment\nunique per tenant, never global",
    paragraphs: [
      "Who is being authorized: a user, and only a user. The verified token carries a user_id and that user's PLATFORM role — authority across the whole service. Access Control does not use that platform role. Authority inside one tenant is a separate thing, stored on a membership row as a tenant role, and the code keeps the two sets deliberately apart even though four role names appear in both.",
      "Tenant is this canvas's only word for that boundary — see the naming note on Request In if you want the one-time alias explanation. Nothing below uses 'customer' again.",
      "What is being authorized is one deployment inside that tenant, named by X-Deployment-Key. That key is unique per tenant rather than globally, so the identical key string in another tenant is a different deployment and grants nothing here. The thing that finally permits the call is an entitlement — the row tying this user to that deployment's exact provider and model.",
      "In the code: InferenceAuthorizationService.authorize_inference(tenant_id, deployment_key, current_user) in app/auth/authorization/tenant_inference_auth.py — those three arguments are the question. Its answer is one frozen object (InferenceAccessContext) that later phases trust instead of re-checking.",
      "Those three values together are the authorization question for every inference call. Redis and PostgreSQL both work on exactly that triple. Chat, embed, and rerank ask the same question — the operation is not part of it. The operation is checked one phase later, in Resolve Deployment.",
      "X-Tenant-ID means \"I want to work as tenant T\", never \"I belong to tenant T\". Only the user id on the token is proven. Naming a tenant you have nothing to do with is allowed and achieves nothing — membership is required below or the request is 403.",
    ],
  },
  CACHE: {
    title: "4 · Recent Approval Cached?",
    sub: "A shortcut, never the source of truth.\nKEY · (user, tenant, deployment) → approved\nMISS → always check PostgreSQL fresh",
    paragraphs: [
      "Why a cache at all: the four database checks below are the same for every request this user makes against this deployment, and they rarely change between one request and the next. A hit replaces four lookups with one — but it is purely a speed-up. PostgreSQL is the only place this decision is actually made; Redis only remembers an answer PostgreSQL already gave.",
      "What is remembered, in plain terms: which user, which tenant, which deployment, and that the answer for that exact combination was yes. A miss means Redis has no memory of that combination — not that the answer is no, just that it has to be looked up. A hit does not skip verification forever, either: it still carries the identifiers the next phase needs, so nothing downstream has to re-fetch them, but it does not carry anything you could dial a model with — no provider name, no URL, no credential.",
      "What actually invalidates a cached yes is not the timer: a management change (revoking access, deactivating a deployment, suspending a tenant) advances a version seal, and the very next read sees the mismatch and discards the stored answer immediately. The TTL (30 seconds by default, configurable 1 to 300) only exists as defense-in-depth for the one pathological case where a seal write itself failed — it is not the real invalidation path.",
      "Only approvals are ever cached, on purpose: caching a refusal would lock out someone who was just granted access for as long as that entry lived. Redis unavailable is treated exactly like a miss — PostgreSQL still runs, so correctness never depends on the cache being up. That is the sense in which Redis is optional and PostgreSQL is not.",
    ],
  },
  GATES: {
    title: "5 · Look It Up In The Database",
    sub: "Four questions. The first \"no\" stops it.\n1  Is this tenant real and switched on?\n2  Are you an active member allowed to use AI there?\n3  Does this saved AI setup exist and is it on?\n4  Were you given permission to use it?",
    paragraphs: [
      "In the code: _authorize_from_source_of_truth in tenant_inference_auth.py runs Gate 1 (tenant exists; status active or trial), Gate 2 (an active membership whose tenant role is developer or above — a viewer is read-only and cannot invoke), Gate 3 (deployment exists and is active), Gate 4 (an active entitlement for this exact tenant + user + deployment + provider + model). Errors map to HTTP codes in app/api/exception_handlers.py.",
      "Why a strange-looking deployment key simply vanishes at Gate 3: the header in Request In accepts a fairly loose shape, but a key that is actually stored is kebab-case — lowercase letters, digits, single hyphens — and that shape is enforced three times over, by the management schema, by this phase's own answer object, and by a CHECK constraint on the table. A key like Support_GPT clears the header check one phase earlier and then matches no row here, so it ends as a 404 rather than an error about its spelling.",
      "A pass here writes the yes back to Redis before moving on — that write is step 5's last action, not a separate arrow back out of this box, and not something Approved itself does.",
      "Order is deliberate, and cheapest first: each check only runs once the one before it holds. Prove the tenant exists before reading membership; prove membership before reading the deployment; prove the deployment before looking up the permission that names its provider and model.",
      "Checks 2 and 4 are a hierarchy, not a repeat. Check 2 is coarse: does this user belong to this tenant with a role allowed to call AI. Check 4 is specific: is this user permitted to use THIS deployment.",
      "404 means the named thing does not exist for this tenant. 403 means it exists and you may not use it. 422 for an inactive deployment is deliberate — a real thing the caller may have permission for, in a state that cannot serve traffic.",
    ],
  },
  APPROVED: {
    title: "Access Approved",
    sub: "May use this deployment — yes. Not yet: which company, model, URL, or key. That is the next column.",
    paragraphs: [
      "Both paths through Access Control — a fresh Postgres pass and a Redis cache hit — join here so Resolve Deployment never looks like a skipped unfinished Phase 2.",
      "What leaves here is identifiers and a yes — including which provider row and which model row the deployment points at. What does not leave with it is anything you could dial a model with: no provider name, no URL, no settings, no key. Those are what the next column goes and reads.",
    ],
  },
  DENYACL: {
    title: "Access Denied",
    sub: "Stops at the first failed question. No fallback to a default tenant or deployment.\n1 → 404 not found · 403 suspended\n2 → 403 not a member / no AI role\n3 → 404 missing · 422 switched off\n4 → 403 no permission",
    paragraphs: [
      "The 422 above means the deployment is switched off — it exists, but cannot serve traffic right now. That is a different 422 from the one in Resolve Deployment, which means the deployment does not support the operation that was called.",
      "Redis being down never causes this box: a cache miss just means the four checks run against PostgreSQL instead, exactly as if nothing had ever been cached. PostgreSQL being down is a different story, and less reassuring — there is no matching deliberate rule for it here. It has no dedicated handling today and would surface as an unhandled 500, not a designed 503. It fails closed by accident, not by design.",
      "No such tenant → 404. Tenant suspended → 403. Not a member, or a role without AI access → 403. No such deployment → 404. Deployment switched off → 422. No permission for that deployment → 403.",
    ],
  },

  LIVE: {
    title: "6 · Resolve Entitlement Configuration",
    sub: "Entitlement = this user's own override of that deployment. Re-read fresh, never cached:\n• which provider company  e.g. OpenAI\n• which model  e.g. gpt-4o\n• which URL to call\n• where its API key is kept\nTenant gone → 404. Suspended or revoked → 403.",
    paragraphs: [
      "Why \"entitlement\" and not \"deployment,\" since this step loads operational detail rather than checking permission again: an entitlement is the specific grant of one provider + model to one user, and it is deliberately where the dial-able details live — not the deployment row, even though a deployment carries its own copy of provider name, model name, endpoint URL and credential reference. The query behind this step joins only user_entitlements to the provider and model catalogs and never touches the deployments table at all. This is not authorization happening twice: Access Control (step 5) already proved this user may use this deployment — that was the permission check. This step reads the one record that actually names what to dial, and that record happens to be scoped per user, not per deployment, which is why it has its own name. Gate 4 already identified it; this step re-reads that exact record, plus the tenant row, so a revocation or model change applies on the very next call.",
      "The key path is only a location. The real API key is fetched later from Vault, in AI Provider Call.",
      "In the code: InferenceRouteResolver._read_active_tenant and _read_authorized_entitlement (app/inference_routing/route_resolution.py). PostgreSQL unreachable here has no designed status — it would surface as an unhandled 500.",
    ],
  },
  PLANBOX: {
    title: "7 · Provider Policy, Then Model Capability",
    sub: "A · PROVIDER POLICY — allowed for this tenant? → else 403\nB · MODEL CAPABILITY — known, fit for chat/embed/rerank? → else 422\nFirst failure wins, before capacity or Vault.",
    paragraphs: [
      "A — provider policy is one check, and it is a tenant-level allow-list, not a global on/off switch. There is no \"is this provider enabled\" flag anywhere in the code — every provider the system has loaded is available by default unless this tenant's own allowed-provider list excludes it (no list at all permits everything; an empty list permits nothing). There is also no \"are credentials configured\" check at this gate — that is not verified until Vault is read in step 9, seconds before the call. Fail here and the answer is 403: the provider is fine, this tenant's policy is not.",
      "B — model capability is two checks against the catalog loaded at startup, no database call. First, does this model exist under this provider at all — an unknown model is 422, the caller's problem. Second, can this exact model perform the specific operation actually being called: chat, embed, or rerank — that is the only capability this step checks. Streaming, tool calling, structured output and vision are not modeled as separate capability gates anywhere in this catalog; only those three operations are. The catalog does carry an is_active / is_deprecated flag per model, and this step does not read either one — a deprecated model that still lists the right operation still passes.",
      "Example: the permission points at an embeddings-only model and the caller asks it to chat. Provider policy (A) is satisfied — this tenant may use that provider. Model capability (B) is not — the model simply cannot do that job → 422, before capacity or Vault.",
      "Gate 1 reads the tenant's allowed-provider list. No list at all (null) means every provider is allowed — a list, even an empty one, restricts the tenant to exactly what it names, so an empty list locks out every provider. Gate 2 looks the company and model up in the catalog loaded at startup — no database call. Gate 3 checks that model's capabilities in the same catalog.",
      "Check 2 hides a split worth knowing, and the status code gives it away. An unknown MODEL is the caller's problem — the permission names a model this company does not offer — and answers 422. An unknown COMPANY is not: it means a provider name is sitting in the database with no matching config file loaded at startup, so the database and this service have drifted apart. That raises a configuration error no status map covers, and it reaches the caller as a 500. That is the right answer — nothing the caller can change would fix it.",
      "Why here and not in Access Control: Gate 4 never looked at whether the HTTP route was chat, embed or rerank. A cached yes still reaches this box, and this is the first place that can refuse the wrong job. Nothing is substituted — if this exact model cannot do it, the call fails.",
      "In the code: _require_provider_allowed and _resolve_provider_model in route_resolution.py.",
    ],
  },
  RECIPE: {
    title: "Freeze The Execution Plan",
    sub: "Build the fixed, request-scoped configuration.\nEvery answer from steps 6 and 7, written once onto one sheet — never rewritten:\nprovider · model · URL · key location · timeout · temperature · max reply length.",
    paragraphs: [
      "What's on the sheet: from the permission record — the AI company, model, URL, region, key location, extra options. From the startup catalog — how long to wait (timeout), creativity (temperature), longest reply allowed (max tokens). Nothing on it is guessed or re-decided after this point; every value was already settled in steps 6 and 7.",
      "\"Frozen\" is not a figure of speech here. In the code this sheet is ResolvedRoute (app/inference_routing/models.py), built with Pydantic's frozen=True: once constructed, trying to change any field raises an error instead of silently succeeding. There is no code path anywhere that edits a ResolvedRoute after it is built — it is genuinely immutable, not just handled carefully.",
      "The question worth asking: why not just keep querying the deployment's configuration again later, whenever capacity or Vault needs a value, instead of writing it all down now? Two reasons. Correctness: resolve it once, and every later stage reads the exact same values — nothing can see one setting from an earlier moment and a different setting from a later one, because there is no later read to drift. Cost: resolving these values already took a database read and a catalog lookup; nothing in this request writes to that data afterward, so reading it again downstream would just repeat work whose answer cannot have changed.",
      "That's what makes the freeze pay off: Capacity, Vault and the provider call never look any of this up themselves — they take the sheet as a parameter and read fields straight off it. route_fingerprint becomes the provider-connection cache key, secret_reference says which credential to fetch, quota_key is what usage counts against. Each stage can trust those values precisely because nothing between here and the provider call is able to change them.",
      "Two labels are added specifically for the stages ahead: quota_key (the permission's id) that Token Manager meters against, and route_fingerprint, a fixed identity of this exact provider + model + settings combination, computed in route_builder.py.",
      "From here the request forks, and the two arrows leaving this box say why. A streaming chat holds a connection open for as long as the reply takes to type out, so it goes through 8a first — that check exists to stop one process's open connections from exhausting its own memory and sockets. Embed, rerank, and non-streaming chat all return one complete response and close immediately; there is no held-open connection for 8a to protect, so those requests skip it entirely — that is the \"Not streaming\" arrow. Token quota (8b) makes no such exception: streamed or not, every operation still spends the AI provider's tokens, so every route passes through it.",
    ],
  },
  DENY422: {
    title: "Route Rejected",
    sub: "403 · Provider not allowed for this tenant\n422 · Model unknown, or cannot do this job\n500 · Provider configuration missing here — our fault\nAll three stop before capacity or Vault.",
    paragraphs: [
      "The two failures read very differently even though both can land here. \"Provider not permitted\" (A, 403) means nothing is wrong with the provider or model — this tenant's own allow-list simply excludes that provider. \"Model not capable\" (B, 422) means the opposite: the provider is fine and permitted, but the specific model behind this entitlement cannot perform the operation being called.",
      "This 422 is different from Access Control's 422 (deployment switched off). Here the permission is on, but the provider behind it is forbidden, unknown, or the wrong kind of model for the route.",
      "Tenant suspended or permission revoked fail one step earlier (step 6) with 403/404 — they never reach these three gates.",
    ],
  },

  /* 8a and 8b deliberately answer the SAME four questions in the same order.
     A reader who has read one card can read the other by only noticing what
     changed — which is the whole point, because the two limits are unrelated
     and were previously easy to blur together. */
  SLOT: {
    title: "8a · Is There A Free Streaming Slot?",
    sub: "PROTECTS · this process's sockets\nSCOPE · this process only, in memory\nRESERVES · 1 slot for the whole stream\nFREED · when the stream ends\nASKED BY · streaming chat · max 20\nNO ROOM → 503 at once, never a queue",
    paragraphs: [
      "Picture several people watching answers type out live from the same process. Each one holds a connection open until their answer finishes. Too many open at once and that process runs out of memory and sockets — so it counts them, and refuses a new one when it is full.",
      "Twenty at once is the default. The process refuses to even start if that number is set higher than the outbound HTTP connection pool can support, because a stream that cannot get a connection is not capacity at all.",
      "Scope, exactly: the count is a plain number in this Python process's memory, guarded by an asyncio lock — no Redis, no database, nothing shared. The code calls it \"intentionally process-local\", and the setting behind it is named stream_max_concurrent_per_worker. Every other running instance keeps its own separate count, so a refusal here says nothing about the fleet and a retry may simply land somewhere with room. Contrast 8b directly below: that one is shared by everybody.",
      "One caveat worth knowing, because \"process\" and \"container\" are not automatically the same thing: the shipped Dockerfile starts uvicorn with no --workers flag, so as built there is exactly one such process per container, and process-local and container-local mean the same thing here. Add --workers N and that stops being true — you would get N independent counters inside one container, each allowing its own 20.",
      "It never queues. Waiting would mean holding an open socket to say \"please wait\", which spends the very resource that has run out.",
    ],
  },
  DENYSLOT: {
    title: "No Streaming Slot Left",
    sub: "503 — try again shortly; the response says how long.\nOnly a streaming chat can ever reach this box.\nAnother instance may have room right now.",
    paragraphs: [
      "The refusal carries a Retry-After hint, one second by default.",
      "Note the ordering: this check runs BEFORE the token quota in 8b. If 8b then refuses, the streaming slot claimed here is handed straight back, so a refusal never leaves this count stuck high.",
    ],
  },
  RESV: {
    title: "8b · Is There Token Quota Left?",
    sub: "PROTECTS · the provider's token pool\nSCOPE · fleet-wide, via Token Manager\nRESERVES · estimated tokens for this call\nFREED · settled for real after the call\nASKED BY · every chat, embed, rerank\nNO ROOM → 429 · NO ANSWER → 503",
    paragraphs: [
      "Why this exists when 8a already said yes: the two protect completely different resources. 8a protects this one process from running out of connections and memory — a local, technical limit. 8b protects the token allowance at the AI provider, which every instance in the fleet spends from the same pool. Passing one tells you nothing about the other: a quiet process with plenty of free connections can still be refused here because the rest of the fleet has spent the tokens, and a busy process can be full at 8a while the token pool is barely touched.",
      "\"Token Manager\" here means a real, separately deployed sibling service — llm_token_manager, its own repository and process, reached over HTTP by TokenManagerClient (app/clients/token_manager_client.py). Not a component inside this service and not a metaphor.",
      "The scope worth being exact about: the ceiling number itself is read from the deployment row (its own token_capacity_limit), but the running counter it is checked against is not scoped to that deployment, and not to a tenant either. Token Manager keys its Redis counter by model name plus a hash of the API endpoint URL alone — tenant_id and deployment_id are not part of that key. So the pool is shared by every deployment, in any tenant, that happens to point at the same model on the same endpoint; it is not the per-tenant allowance an earlier version of this card claimed.",
      "AI companies meter and cap by how much text moves. Every instance in the fleet may be talking to the same model at the same moment, so only one shared service can say whether there is still room — no single instance can know.",
      "How much is asked for: the tokens in your actual message, plus the longest reply we will allow, which came off the plan frozen in step 3. Embed and rerank add nothing for a reply, because they do not write one.",
      "That same reply limit is what we then tell the AI company not to exceed, so what we reserve and what we permit cannot drift apart.",
      "One more answer exists that this diagram does not draw: if Token Manager replies with something that breaks the contract — rejecting our service credentials, malformed JSON, or a reservation for a different endpoint than the one we authorised — that is a 502, not a 429 or a 503. The endpoint cross-check is a deliberate guard: a reservation that silently points somewhere else is refused rather than used.",
    ],
  },
  DENYRESV: {
    title: "No Token Quota Left",
    sub: "429 — Token Manager said no, or could not grant room yet.\nSomebody answered. Retrying helps only once quota frees up elsewhere.\nThe AI company is never called.",
    paragraphs: [
      "This is a real refusal, not a sign that Token Manager is unwell. Nothing stays reserved for this call.",
      "The streaming slot claimed back in 8a is freed on the way out.",
    ],
  },
  RESVDOWN: {
    title: "Token Manager Did Not Answer",
    sub: "503 — timed out, unreachable, or their own server error.\nNobody answered, rather than somebody saying no.\nNothing was reserved, so there is nothing to give back.",
    paragraphs: [
      "The difference from the 429 above is worth holding on to: there, the shared quota is genuinely spent and retrying is pointless until it frees up. Here, we simply never heard back, and the quota may be entirely untouched.",
      "We never got a yes, so nothing is left reserved on Token Manager's side. The streaming slot claimed in 8a is still freed in this process.",
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
    sub: "503 — no provider call. Streaming slot and token quota reservation are both freed on the way out.",
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
    title: "11 · Send The Response",
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
    title: "12 · Release The Streaming Slot · Report Token Usage",
    sub: "Always runs once. Give back this process's streaming slot if we took one; tell Token Manager how much text was really used (or none if unknown).",
    paragraphs: [
      "If this was a streaming chat, this process's open-streaming-slot count goes down by one. Token Manager is told the real input and output sizes when known, and the reservation closes as completed, failed, cancelled, or disconnected.",
      "We reserved an estimate before the call; we settle the books after — whether the call went well or not.",
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
    sub: "A person submits their username and password. This is the starting point for proving who they are.",
    paragraphs: [
      "A person opens the sign-in screen and enters a username and password. Their web or mobile app sends those details to POST /auth/sign-in. POST means the app is sending information to the service so it can perform an action. At this point the person has not yet proved their identity, so the request does not include a bearer token. A bearer token is the temporary digital pass that the service gives back after a successful sign-in. The rest of this sign-in path checks the details and, if they are valid, creates that pass for later requests.",
      "This matters because most other actions in the diagram, such as asking an AI model for an answer or changing a deployment, begin by showing that digital pass. It lets the service identify the caller without asking for their password again. You may also see GET /auth/options near this step. It is not part of signing in. Before displaying the sign-in screen, an app can call it to learn which sign-in choices it should show to the person, such as username and password or a local guest option. It only returns that list of choices. It does not receive a password, verify anyone's identity, or issue a token.",
    ],
  },
  LIMIT: {
    title: "2 · Too Many Failed Logins Lately?",
    sub: "Before checking the password, the service checks whether this username has had too many recent unsuccessful sign-in attempts.",
    paragraphs: [
      "Before the service reads an account or compares a password, it asks Redis whether this username has failed to sign in too many times recently. Redis is a fast, temporary data store used here to remember recent failed attempts. By default, the service allows up to five failed attempts within five minutes. These values are settings that operators can change, not fixed rules built into the application.",
      "If the allowed number of failed attempts has already been reached, the service stops the request immediately and tells the app how long to wait before trying again. It does not read the password or contact the database. This slows down repeated password guessing while avoiding unnecessary work for a request that is already blocked.",
      "Only unsuccessful attempts are counted. A successful sign-in clears the failure count, so an ordinary typing mistake does not punish someone who then enters the correct password. Each additional failed attempt refreshes the five-minute period. This means continuous guessing keeps the temporary block in place instead of allowing the attacker to wait briefly and resume.",
      "Redis is helpful for protection, but it is not allowed to become a single point of failure for signing in. If Redis is temporarily unavailable, the service continues to the password-checking step. Otherwise, a problem with this temporary store would prevent every person from signing in, even when their password is correct.",
    ],
  },
  READ: {
    title: "3 · Fetch The Account From The Database",
    sub: "The service retrieves only the account details needed to safely decide whether this person may sign in.",
    paragraphs: [
      "The service now asks PostgreSQL, the application’s main database, for the account associated with the submitted username. It retrieves only three pieces of information: the stored password hash, whether the account is active, and the person’s platform role. A password hash is a protected mathematical representation of the password, not the original password itself.",
      "This step does not decide whether the sign-in succeeds. Finding an account in the database only shows that an account exists. For example, a former employee may still have an account record and may enter the correct password, but their account could be inactive. The next step compares the submitted password safely and checks whether the account is allowed to sign in.",
      "The service must not reveal whether a username exists. If it rejected an unknown username immediately but took longer to check a real account, someone could repeatedly try usernames and use the response time as a clue. A fast response could suggest that no account exists, while a slower response could suggest that one does.",
      "When no account is found, the service still runs a password comparison against a safe, pre-prepared placeholder value. The comparison cannot succeed because the placeholder does not belong to a real account. Its only purpose is to make the response take a similar amount of time whether the username exists or not.",
    ],
  },
  VERIFY: {
    title: "4 · Does The Password Match?",
    sub: "The service checks the password, account status, and allowed role, while returning the same safe response for every failed sign-in.",
    paragraphs: [
      "The service compares the password that was submitted with the protected password hash retrieved in Step 3. It also checks that the account is active and that the person has a platform role this service allows to sign in. A sign-in can fail for four reasons: the username does not belong to an account, the password is incorrect, the account is inactive, or the account has a role that is not allowed to use this service.",
      "The app receives the same response for all four failures: 401 Unauthorized. In this context, 401 means the service could not confirm the caller's identity. It does not tell the person which check failed. This prevents someone from learning whether a username belongs to a real account simply by trying to sign in with it.",
      "The service records the precise internal reason in its protected logs so support and security teams can investigate problems. That detail is never sent back to the person attempting to sign in.",
      "When the sign-in fails, the service adds one failed attempt to the Redis counter introduced in Step 2. When every check succeeds, it clears that counter because the person has proved their identity. The request can then move to the next step, where the service creates their temporary access token.",
    ],
  },
  ISSUE: {
    title: "5 · Issue The Token",
    sub: "After a successful sign-in, the service creates a signed, temporary access token that the app can use on later requests.",
    paragraphs: [
      "The person has now proved their identity, so this service creates an access token. An access token is a small piece of signed data that works like a temporary digital pass. The app sends it with later requests instead of sending the person's username and password again.",
      "The token contains the person's identity and platform role, along with who issued it, which service may accept it, and when it expires. The service signs the token using its trusted security settings. Later, when the token is presented, the service can check that it was created by this service, has not been changed, is intended for this service, and has not expired.",
      "The token has a limited lifetime. This limits the risk if it is copied or stolen because it becomes unusable after its expiry time. The service will not create a token that remains valid longer than the period its verification rules allow.",
      "This is the only point in the system that creates access tokens. The token does not name a tenant and does not say whether the session began through the local guest route. Tenant-specific permission is checked separately when the person later asks to use an AI deployment or manage tenant settings. Those later parts of the system verify tokens only. They never create them.",
    ],
  },
  TOKENOUT: {
    title: "6 · Return The Sign-In Token",
    sub: "The app receives the temporary access token and the basic information it needs to begin the signed-in session.",
    paragraphs: [
      "The service returns 200 OK, which means the sign-in completed successfully. The response gives the app the new access token, the time when that token will expire, and basic identity details that can be shown in the application, such as the person's name and platform role.",
      "If the person entered through the local guest route, the response also identifies this session as a guest session. This guest indicator belongs only to this sign-in response. It helps the app present the session appropriately, but it is not stored inside the access token.",
      "On a later request, the app sends the access token as its bearer token. The first step of the Run Inference and Manage Tenants flows checks that token before allowing the request to continue. If that check returns 401 Unauthorized, the token is missing, invalid, or expired. The app must return the person to the sign-in process to obtain a new token.",
    ],
  },
  GUEST: {
    title: "1b · Guest Door Open?",
    sub: "A separate local-development sign-in route. It is not for normal users or production.",
    paragraphs: [
      "This is a separate sign-in route for local development. It is not tried automatically when username and password sign-in fails, and it is not intended for normal users.",
      "A developer may start a new copy of the service on their own computer before any user accounts or passwords have been created. If the local guest option is enabled, POST /auth/guest-session gives that developer a temporary guest token so they can enter the application and perform the initial setup. The service does not ask for a password, check failed-login limits, or look up an account in the database. Instead, it moves directly to the token-creation step.",
      "In a production environment, this route must be disabled. If it is disabled, the service returns 403 Forbidden, which means \"you are not allowed to use this route.\" Production refuses to start while the guest option is enabled because anyone who could reach this URL could otherwise obtain a guest token without providing credentials.",
    ],
  },
  STOP429: {
    title: "Too Many Attempts",
    sub: "The recent failed-attempt limit for this username has been reached. Wait before trying again.",
    paragraphs: [
      "The service has already recorded too many unsuccessful sign-in attempts for this username within the current time window. It therefore returns 429 Too Many Requests. This response is a temporary protection measure, not a statement that the username or password is definitely wrong.",
      "The response tells the app how long to wait before another attempt can be made. During that waiting period, the service does not read an account from the database and does not examine the submitted password. This prevents repeated guessing from reaching the password-checking process.",
    ],
  },
  STOP401: {
    title: "Credential Rejected",
    sub: "The service could not confirm the person's identity, but it does not reveal which sign-in check failed.",
    paragraphs: [
      "The service returns 401 Unauthorized when the sign-in cannot be accepted. This can mean the username does not exist, the password is incorrect, the account is inactive, or the account's role is not allowed to sign in.",
      "The same response is returned for every one of these cases. This is intentional. Giving a different message for an unknown username would help someone discover which accounts exist, so the service only tells the app that sign-in was not successful.",
      "The failed attempt is recorded in Redis, the temporary store used to track recent unsuccessful sign-ins. If more failures follow, they count toward the temporary limit described in Step 2. The detailed internal reason is written only to protected service logs for support and security investigation.",
    ],
  },
  STOP403: {
    title: "Guest Disabled",
    sub: "Guest access is disabled unless a developer deliberately enables it for local development.",
    paragraphs: [
      "Guest access is deliberately disabled unless a developer turns it on for local development. This is the expected and safe setting for a normal installation, especially in production.",
      "When someone calls POST /auth/guest-session while guest access is disabled, the service returns 403 Forbidden. This response means the service understood the request but will not allow that route to be used. No password is checked, no account is read, and no guest token is created.",
    ],
  },

  /* ---------- Lane C - Manage Tenants & Deployments ---------- */
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
    sub: "Platform-wide token role or tenant-local admin: may you administer this, not may you infer?",
    paragraphs: [
      "A platform role is the service-wide role carried in the caller's token (for example operator, admin or owner); it is distinct from a role held only inside one tenant. A sufficiently privileged platform role passes on that badge alone. Anyone else must be an active member of the specific tenant being touched - and a write additionally requires a tenant-admin role, not just membership.",
      "On tenant-scoped routes this check can safely precede body-reference validation: the tenant being authorized is the tenant_id already parsed from the route or query, while MREF checks the related tenant, user, provider or model identifiers supplied by the operation. Authorization therefore does not depend on trusting those body references first.",
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
  MIDENT401: {
    title: "Identity Rejected",
    sub: "401 - the bearer token is missing, invalid, expired or the wrong kind",
    paragraphs: [
      "The shared authentication dependency returns this 401 before the management route runs. It is not produced by the management exception map; it is the same front-door bearer-token response used by the other protected API lanes.",
    ],
  },
  MSCOPE403: {
    title: "Administration Denied",
    sub: "403 - the caller lacks the required platform-wide or tenant-local role",
    paragraphs: [
      "TenantAccessDeniedError maps to 403. The caller is authenticated, but is not permitted to read or administer the tenant-scoped resource.",
    ],
  },
  MREF404: {
    title: "Reference Not Found",
    sub: "404 - a referenced tenant, user, provider or model does not exist",
    paragraphs: [
      "ResourceNotFoundError maps to 404. Reference validation stops the operation before a write can turn a missing related record into an opaque database error.",
    ],
  },
  MWRITE4XX: {
    title: "Write Rejected",
    sub: "409 for conflict or invalid state; 400 for management validation",
    paragraphs: [
      "InvalidStateTransitionError and ResourceConflictError map to 409. ManagementValidationError maps to 400. These are distinct client-visible outcomes of the write gate, so the card names both instead of inventing one catch-all code.",
    ],
  },
  MSECRET503: {
    title: "Vault Unavailable",
    sub: "503 - the credential cannot be written safely",
    paragraphs: [
      "SecretBackendUnavailableError maps to 503. The operation cannot claim success when a supplied credential could not be persisted to Vault.",
    ],
  },
  MCACHE503: {
    title: "Cache Invalidation Failed",
    sub: "503 - authorization grants could not be invalidated in Redis",
    paragraphs: [
      "AuthorizationGrantCacheUnavailableError maps to 503. The API refuses to report a clean success while stale authorization grants may still be served.",
    ],
  },

};

function LaneHeader({ data }) {
  return h(
    "div",
    { className: "hld-lane-header" },
    h("span", { className: "hld-lane-header-name" }, data.label),
    h("span", { className: "hld-lane-header-meaning" }, data.meaning)
  );
}

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
    /* pre-line lets a DETAILS.sub carry short "label = source" lines (see AUTHCTX). */
    data.sub ? h("p", { className: "hld-node-sub", style: { whiteSpace: "pre-line" } }, data.sub) : null,
    h(Handle, { type: "source", position: Position.Right, id: "right", className: "hld-handle" }),
    h(Handle, { type: "source", position: Position.Bottom, id: "bottom", className: "hld-handle" }),
    /* A left-hand exit, used where a refusal cannot simply drop: in Resolve
       Deployment the refused path has to get past the card BELOW it to reach
       its stop box, and the right-hand margin is already carrying the two
       forward branches into Capacity Check. Sending refusals out of the left
       keeps the two directions on opposite sides of the column instead of
       three lines fighting for one gutter. */
    h(Handle, { type: "source", position: Position.Left, id: "left-out", className: "hld-handle" })
  );
}

/* A hop between two phase columns travels through the open band above the
   target's first card, so the connector and its label stay clear of every
   node rather than relying on React Flow's midpoint placement. */
function PhaseHopEdge({ id, sourceX, sourceY, targetX, targetY, markerEnd, style, label, data }) {
  const exitX = data?.exitX ?? sourceX + 60;
  /* viaY: "target" means "run in level with the handle you are aiming at".
     Say that rather than copying the handle's current Y as a number: a
     handle sits at half its card's height, so every later change to that
     card's y or h silently invalidates the number, and the only symptom is
     a stub at the end of the path that rotates the arrowhead. */
  const requestedViaY = data?.viaY === "target" ? targetY : (data?.viaY ?? targetY - 70);
  /* Snap the corridor onto the target's own Y when it is already within a
     few pixels of it. A hand-written viaY is a whole number, but a handle
     sits at half its card's height and lands on a .5 — so "go along and
     turn in" leaves a sub-pixel vertical stub at the very end of the path.
     An SVG arrow marker orients along the LAST segment, so that invisible
     stub swings the arrowhead through 90 degrees: it points down into the
     card it should be pointing into sideways, and the rotated head spills
     over the card's title. Collapsing the stub makes the final segment
     zero-length, and the marker then takes its angle from the horizontal
     run a reader can actually see. */
  const viaY = Math.abs(requestedViaY - targetY) < 8 ? targetY : requestedViaY;
  const labelX = data?.labelX ?? targetX;
  const labelY = data?.labelY ?? viaY;
  const labelWidth = data?.labelWidth ?? Math.min(320, Math.max(110, (label?.length || 0) * 6.7 + 24));
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

/* A refusal dropping straight from a gate to the stop box in its own column.
   React Flow's default label placement puts the text at the midpoint of the
   path, which for a long drop leaves it stranded in whitespace, touching
   neither end. Here the label is pinned just under the gate instead, so it
   reads as that gate's answer rather than as a caption for the stop box. */
function RefusalDropEdge({ id, sourceX, sourceY, targetX, targetY, markerEnd, style, label, data }) {
  const path = `M ${sourceX},${sourceY} L ${sourceX},${targetY - 18} L ${targetX},${targetY}`;
  const labelY = data?.labelY ?? sourceY + 46;
  const labelWidth = data?.labelWidth ?? Math.min(320, Math.max(96, (label?.length || 0) * 6.7 + 24));

  return h(
    React.Fragment,
    null,
    h(BaseEdge, { id, path, markerEnd, style }),
    label
      ? h(
          "g",
          { className: "hld-flow-label", transform: `translate(${sourceX} ${labelY})` },
          h("rect", { x: -labelWidth / 2, y: -12, width: labelWidth, height: 24, rx: 8, ry: 8 }),
          h("text", { x: 0, y: 1, textAnchor: "middle", dominantBaseline: "middle" }, label)
        )
      : null
  );
}

const NODE_TYPES = { laneHeader: LaneHeader, phase: PhasePanel, step: StepCard };
const EDGE_TYPES = {
  phaseHop: PhaseHopEdge,
  lowerCorridor: LowerCorridorEdge,
  refusalDrop: RefusalDropEdge,
};

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
  const nodes = Object.entries(LANE_HEADERS).map(([id, header]) => ({
    id: `lane-header-${id}`,
    type: "laneHeader",
    position: { x: header.x, y: header.y },
    style: { width: header.w },
    data: header,
    draggable: false,
    selectable: false,
    focusable: false,
    zIndex: 5,
  }));

  nodes.push(...Object.entries(PHASES).map(([phase, box]) => ({
    id: `phase-${phase}`,
    type: "phase",
    position: { x: box.x, y: box.y },
    style: { width: box.w, height: box.h },
    data: { phase, index: box.index, title: box.title, note: box.note },
    draggable: false,
    selectable: false,
    focusable: false,
    zIndex: 0,
  })));

  Object.entries(LAYOUT).forEach(([id, layout]) => {
    const detail = DETAILS[id];
    nodes.push({
      id,
      type: "step",
      position: { x: layout.x, y: layout.y },
      /* `h` is optional and pins a row to one card height. Without it a card
         sizes to its own text, so cards sharing a row end up different heights,
         their left/right handles sit at different mid-points, and every
         connector between them renders as a slight S-bend. Pinning the row
         makes those connectors dead straight. */
      style: layout.h ? { width: layout.w, height: layout.h } : { width: layout.w },
      data: { id, ...detail, ...layout, onHover, onLeave },
      draggable: false,
      selectable: false,
      zIndex: 2,
    });
  });

  const edges = [
    /* Lane A — Sign In.

       Labelling rule, applied consistently so that an unlabelled arrow is
       itself information: every edge leaving a GATE carries a label, because
       a gate has two outcomes and the reader must be told which is which.
       Plain sequence steps (ENTRY→LIMIT, READ→VERIFY, ISSUE→TOKENOUT) carry
       none, because there is nothing to choose. The two outcomes of one gate
       are phrased as a matched pair — "Under the limit" / "Over the limit" —
       so the contrast is visible without reading to the end of the sentence.

       The guest bypass runs UNDER the refusal row rather than above it. Above
       it, the line had to cross both of the long refusal drops on its way to
       the token issuer; underneath, it crosses nothing at all, and the one
       place it rises is the far right, where no refusal box sits. A bypass
       that touches no other line is the whole reason a reader can trust it
       goes where it appears to go. */
    flowEdge("entry-limit", "ENTRY", "LIMIT", { sourceHandle: "right", targetHandle: "left" }),
    flowEdge("limit-read", "LIMIT", "READ", { sourceHandle: "right", targetHandle: "left", label: "Under limit" }),
    flowEdge("limit-429", "LIMIT", "STOP429", {
      type: "refusalDrop",
      sourceHandle: "bottom",
      targetHandle: "top",
      kind: "blocked",
      label: "Over limit",
    }),
    flowEdge("read-verify", "READ", "VERIFY", { sourceHandle: "right", targetHandle: "left" }),
    flowEdge("verify-issue", "VERIFY", "ISSUE", { sourceHandle: "right", targetHandle: "left", label: "Matches" }),
    flowEdge("verify-401", "VERIFY", "STOP401", {
      type: "refusalDrop",
      sourceHandle: "bottom",
      targetHandle: "top",
      kind: "blocked",
      /* Short enough to pair against "Matches" at a glance. That the failure
         is counted in Redis is on the stop box itself, where a reader who
         wants the mechanism is already looking. */
      label: "No match",
    }),
    flowEdge("issue-tokenout", "ISSUE", "TOKENOUT", { sourceHandle: "right", targetHandle: "left" }),
    flowEdge("guest-403", "GUEST", "STOP403", {
      type: "refusalDrop",
      sourceHandle: "bottom",
      targetHandle: "top",
      kind: "blocked",
      label: "Closed",
      data: { labelY: 834 },
    }),
    flowEdge("guest-issue", "GUEST", "ISSUE", {
      type: "lowerCorridor",
      sourceHandle: "right",
      targetHandle: "bottom-in",
      kind: "evidence",
      label: "Open · no password, no rate limit, no account read",
      /* leftRailX 428 threads the gap between the 403 box (ends 348) and the
         429 box (starts 508); rightRailX is the token issuer's centre line,
         where the corridor rises through empty space. The label sits at 1022,
         the midpoint of the wide gap between the 429 and 401 boxes, so it
         never reads as a caption belonging to either of them. */
      data: { leftRailX: 428, rightRailX: 1814, corridorY: 1076, labelX: 1022, labelY: 1076 },
      zIndex: 4,
    }),

    /* Lane 01 -- Request In */
    flowEdge("entry-ident", "REQIN", "IDENT", { sourceHandle: "bottom", targetHandle: "top" }),
    flowEdge("ident-401", "IDENT", "DENY401", {
      type: "refusalDrop",
      sourceHandle: "bottom",
      targetHandle: "top",
      kind: "blocked",
      label: "Rejected",
    }),
    flowEdge("ident-authctx", "IDENT", "AUTHCTX", {
      type: "phaseHop",
      targetHandle: "left",
      label: "Identity OK",
      /* Rises through the gutter between this column and Access Control; the
         label sits at the midpoint of that vertical run, clear of both. */
      data: { exitX: 320, viaY: 1427, labelX: 320, labelY: 1590, labelWidth: 92 },
      zIndex: 4,
    }),

    /* Lane 02 -- Access Control: key → cache → gates; both yes paths
       meet at Approved before Resolve Deployment. */
    flowEdge("authctx-cache", "AUTHCTX", "CACHE", { sourceHandle: "bottom", targetHandle: "top" }),
    flowEdge("cache-gates", "CACHE", "GATES", { sourceHandle: "bottom", targetHandle: "top", label: "Not remembered" }),
    flowEdge("gates-deny", "GATES", "DENYACL", {
      type: "refusalDrop",
      sourceHandle: "bottom",
      targetHandle: "top",
      kind: "blocked",
      label: "A question said no",
      /* Centred in the 68px gap rather than the usual offset-under-the-gate:
         the gap here is short enough that the default would clip the box. */
      data: { labelY: 1981 },
    }),
    flowEdge("gates-approved", "GATES", "APPROVED", {
      type: "phaseHop",
      sourceHandle: "right",
      targetHandle: "bottom-in",
      label: "All 4 pass",
      data: { exitX: 690, viaY: 1864, labelX: 767, labelY: 1864 },
      zIndex: 4,
    }),
    flowEdge("cache-approved", "CACHE", "APPROVED", {
      type: "phaseHop",
      sourceHandle: "right",
      targetHandle: "left",
      kind: "evidence",
      label: "Remembered",
      /* Sits ON the line, not floating above it: a label hovering beside a
         connector reads as belonging to neither end. */
      data: { exitX: 690, viaY: 1646, labelX: 710, labelY: 1646, labelWidth: 92 },
      zIndex: 4,
    }),
    flowEdge("approved-live", "APPROVED", "LIVE", {
      type: "phaseHop",
      sourceHandle: "right",
      targetHandle: "left",
      label: "Approved",
      /* Turns in level with the target's left handle, so the arrival reads
         as a left-hand arrival. The label sits on the vertical run. */
      data: { exitX: 970, viaY: "target", labelX: 970, labelY: 1550, labelWidth: 76 },
      zIndex: 4,
    }),

    /* Lane 03 -- Resolve Deployment.

       Three connectors need to get past this column's own cards, so each one
       is given its own rail rather than letting them share a gutter:

         x=1004  LEFT margin  — the refusal, which has to reach a stop box
                               that sits below the card following it
         x=1356  right margin — the streaming branch, climbing to 8a
         x=1382  right margin — the one-shot branch, crossing to 8b

       Refusal out of the left, forward paths out of the right: the two
       directions never share a gutter, and no line crosses another. */
    flowEdge("live-plan", "LIVE", "PLANBOX", { sourceHandle: "bottom", targetHandle: "top" }),
    flowEdge("plan-recipe", "PLANBOX", "RECIPE", { sourceHandle: "bottom", targetHandle: "top", label: "Allowed & capable" }),
    flowEdge("plan-422", "PLANBOX", "DENY422", {
      type: "phaseHop",
      sourceHandle: "left-out",
      targetHandle: "top",
      kind: "blocked",
      label: "Refused",
      data: { exitX: 1004, viaY: 2027, labelX: 1092, labelY: 2027, labelWidth: 84 },
      zIndex: 4,
    }),
    flowEdge("plan-slot", "RECIPE", "SLOT", {
      type: "phaseHop",
      targetHandle: "top",
      label: "Live (streaming) chat → 8a",
      data: { exitX: 1356, viaY: 1380, labelX: 1610, labelY: 1355 },
      zIndex: 4,
    }),
    flowEdge("plan-resv-bypass", "RECIPE", "RESV", {
      type: "phaseHop",
      sourceHandle: "right",
      targetHandle: "left",
      kind: "evidence",
      label: "Not streaming — skip 8a, go to 8b",
      /* Turns in flat at 8b's own handle; anything else leaves a stub that
         swings the arrowhead downward. labelWidth widened from 120 to fit
         the longer label — the auto formula (len*6.7+24) wants ~245px.

         The label does NOT sit on the line. The gutter between RECIPE (ends
         x1340) and 8b (starts x1485) is only 145px, and a 245px label placed
         there covered 8b's "NO ROOM -> 429" line. It moves instead to the
         clear band under 8b: below 8b's bottom edge (1818), right of RECIPE
         (1340), above DENY422's top (1917). Same trick guest-issue already
         uses — park a wide label in open space rather than crop it. */
      data: { exitX: 1425, viaY: "target", labelX: 1470, labelY: 1982, labelWidth: 240 },
      zIndex: 4,
    }),

    /* Lane 04 -- Connections & Tokens: live connections in this process, then
       shared token quota for every inference. */
    flowEdge("slot-503", "SLOT", "DENYSLOT", {
      type: "phaseHop",
      sourceHandle: "right",
      targetHandle: "left",
      kind: "blocked",
      label: "Already too many live replies",
      data: { exitX: 1752, viaY: "target", labelX: 1870, labelY: 1370 },
      zIndex: 4,
    }),
    flowEdge("slot-resv", "SLOT", "RESV", { sourceHandle: "bottom", targetHandle: "top", label: "This process has a free slot" }),
    flowEdge("resv-429", "RESV", "DENYRESV", {
      type: "phaseHop",
      sourceHandle: "right",
      targetHandle: "left",
      kind: "blocked",
      label: "Not enough token quota",
      data: { exitX: 1752, viaY: "target", labelX: 1870, labelY: 1675 },
      zIndex: 4,
    }),
    flowEdge("resv-503", "RESV", "RESVDOWN", {
      type: "phaseHop",
      sourceHandle: "right",
      targetHandle: "left",
      kind: "blocked",
      label: "Token Manager silent",
      /* Centres in the 70px gap now open between the two stacked refusals;
         while they overlapped, every candidate position sat over a card. */
      data: { exitX: 1755, viaY: "target", labelX: 1870, labelY: 1947 },
      zIndex: 4,
    }),
    flowEdge("resv-cred", "RESV", "CRED", {
      type: "phaseHop",
      /* "left" is a TARGET handle id, so this never matched a source and
         React Flow was falling back. The left-hand SOURCE is "left-out".
         Leaving on the left is deliberate: it lets the success path climb
         over the two refusal boxes stacked to the right of 8b instead of
         threading between them. */
      sourceHandle: "left-out",
      targetHandle: "top",
      label: "Token quota reserved",
      data: { exitX: 1440, viaY: 1380, labelX: 2090, labelY: 1380 },
      zIndex: 4,
    }),

    /* Lane 05 -- AI Provider Call */
    /* CRED and DENYVAULT sit only 15px apart (2275 to 2290) — too tight for
       smoothstep's default rounded corners, which produced a small loop
       instead of a line. phaseHop with an explicit exitX and viaY snapped
       to the target's own row draws a straight, controlled path instead. */
    flowEdge("cred-vault-fail", "CRED", "DENYVAULT", {
      type: "phaseHop",
      sourceHandle: "right",
      targetHandle: "left",
      kind: "blocked",
      data: { exitX: 2283, viaY: "target" },
    }),
    flowEdge("cred-callbox", "CRED", "CALLBOX", { sourceHandle: "bottom", targetHandle: "top", label: "Key in hand" }),
    flowEdge("callbox-send", "CALLBOX", "SEND", {
      type: "phaseHop",
      targetHandle: "top",
      label: "Response received",
      data: { exitX: 2430, viaY: 1380, labelX: 2475, labelY: 1380 },
      zIndex: 4,
    }),

    /* Lane 06 -- Response & Wrap-Up */
    flowEdge("send-done", "SEND", "DONE", { sourceHandle: "bottom", targetHandle: "top", label: "Sent OK" }),
    /* SEND and MIDSTREAM sit only ~23px apart — the same too-tight-for-
       smoothstep gap as cred-vault-fail, which rendered as a full zigzag
       (forward, curve, then a backward step) instead of a line. Same fix:
       an explicit phaseHop corridor inside the gutter. */
    flowEdge("send-mid", "SEND", "MIDSTREAM", {
      type: "phaseHop",
      sourceHandle: "right",
      targetHandle: "left",
      kind: "blocked",
      data: { exitX: 2768, viaY: "target" },
    }),
    /* viaY was hardcoded at 1680. DONE's "left" handle sits at its true
       vertical center — for a two-line title that center falls inside the
       title text itself, so entering there draws the arrow straight through
       the words no matter how well viaY is snapped. MIDSTREAM sits above
       DONE, so — same as plan-slot/resv-cred/callbox-send into their own
       phase's top handle — targeting "top" with viaY a clean margin above
       DONE's own fixed y (1555, independent of its auto-measured height)
       gives a real vertical drop into open space above all of DONE's
       content, converging with send-done's straight arrival from SEND. */
    flowEdge("mid-done", "MIDSTREAM", "DONE", {
      type: "phaseHop",
      sourceHandle: "bottom",
      targetHandle: "top",
      label: "Cleanup still runs",
      data: { exitX: 2890, viaY: 1650, labelX: 2840, labelY: 1650 },
      zIndex: 4,
    }),
    flowEdge("callbox-done", "CALLBOX", "DONE", {
      type: "phaseHop",
      sourceHandle: "bottom",
      targetHandle: "left",
      kind: "blocked",
      label: "Failed before first byte",
      data: { exitX: 2175, viaY: 1850, labelX: 2325, labelY: 1850 },
      zIndex: 4,
    }),
    flowEdge("done-gap", "DONE", "GAP", { sourceHandle: "bottom", targetHandle: "top", kind: "blocked", label: "Known limitation" }),

    /* Lane C -- Manage Tenants & Deployments. Linear, with one optional branch:
       only a request that included a credential visits Vault. */
    flowEdge("mreq-mident", "MREQ", "MIDENT", { sourceHandle: "right", targetHandle: "left" }),
    flowEdge("mident-mscope", "MIDENT", "MSCOPE", { sourceHandle: "right", targetHandle: "left" }),
    flowEdge("mscope-mref", "MSCOPE", "MREF", { sourceHandle: "right", targetHandle: "left", label: "Scope approved" }),
    flowEdge("mref-mwrite", "MREF", "MWRITE", { sourceHandle: "right", targetHandle: "left" }),
    flowEdge("mwrite-msecret", "MWRITE", "MSECRET", { sourceHandle: "right", targetHandle: "left", label: "Includes a credential" }),
    flowEdge("mident-401", "MIDENT", "MIDENT401", {
      type: "refusalDrop", sourceHandle: "bottom", targetHandle: "top",
      kind: "blocked", label: "Missing or invalid token",
    }),
    flowEdge("mscope-403", "MSCOPE", "MSCOPE403", {
      type: "refusalDrop", sourceHandle: "bottom", targetHandle: "top",
      kind: "blocked", label: "Not authorized",
    }),
    flowEdge("mref-404", "MREF", "MREF404", {
      type: "refusalDrop", sourceHandle: "bottom", targetHandle: "top",
      kind: "blocked", label: "Reference missing",
    }),
    flowEdge("mwrite-4xx", "MWRITE", "MWRITE4XX", {
      type: "refusalDrop", sourceHandle: "bottom", targetHandle: "top",
      kind: "blocked", label: "Invalid or conflicting write",
    }),
    flowEdge("msecret-503", "MSECRET", "MSECRET503", {
      type: "refusalDrop", sourceHandle: "bottom", targetHandle: "top",
      kind: "blocked", label: "Vault unavailable",
    }),
    flowEdge("mcache-503", "MCACHE", "MCACHE503", {
      type: "refusalDrop", sourceHandle: "bottom", targetHandle: "top",
      kind: "blocked", label: "Redis unavailable",
    }),
    flowEdge("mwrite-mcache-bypass", "MWRITE", "MCACHE", {
      type: "lowerCorridor",
      sourceHandle: "bottom",
      kind: "evidence",
      label: "No credential -- skip Vault",
      /* leftRailX/rightRailX recomputed for the uniform-width row above (30px
         gutter past MWRITE's new right edge 1700, and before MCACHE's new
         left edge 2220). corridorY unchanged: it deliberately drops into the
         open band below the management refusal row. The rail rises through
         the gap between the Vault and Redis refusal cards. */
      data: { leftRailX: 1730, rightRailX: 2190, corridorY: 2850, labelX: 1960, labelY: 2850 },
      zIndex: 4,
    }),
    flowEdge("msecret-mcache", "MSECRET", "MCACHE", { sourceHandle: "right", targetHandle: "left" }),
    flowEdge("mcache-mdone", "MCACHE", "MDONE", { sourceHandle: "right", targetHandle: "left" }),

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
      /* Open at a zoom where the writing can actually be read, anchored at
         the top-left where the reading order starts.

         This used to be `fitView`, which sounds right and is not: fitting a
         3010 x 2700 canvas into a 1920px viewport lands on zoom 0.345, and at
         0.345 NOTHING on this canvas is legible — body text renders at 3.6px,
         the hop chips naming PostgreSQL and Vault at 3.3px, and even the
         phase titles at 5.9px. The first thing a visitor saw was coloured
         rectangles, which reads as "this diagram omits the detail" when in
         fact the detail is present and merely sub-pixel.

         0.85 keeps 10.5px body text at ~9px on screen, the floor for reading.
         The overview is still one click away on the fit-view control, and
         minZoom leaves it reachable by zooming out. */
      defaultViewport: { x: 16, y: 10, zoom: 0.9 },
      fitView: false,
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
