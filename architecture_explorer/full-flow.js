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
  APPROVED:{ phase: "knowledge", icon: "accept", tag: "MERGE POINT", x: 760, y: 1536, w: 170, h: 220, gate: true },
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
    sub: "Every AI request identifies the caller, names the tenant they want to act for, and selects a saved AI setup.",
    paragraphs: [
      "Three kinds of AI work enter here. POST /api/v1/llm/chat requests a chat completion, POST /api/v1/llm/embed turns text into embedding vectors, and POST /api/v1/llm/rerank orders documents by how relevant they are to a query. Each route has a different request body, but all three require the same three headers before the service can continue.",
      "A tenant is the customer or organisation boundary. Think of the request as arriving with a badge and a destination: Authorization: Bearer <token> is the badge, X-Tenant-ID is the organisation the caller wants to act for, and X-Deployment-Key is the saved AI setup they want to use within that organisation.",
      "The bearer token is the only item that proves anything at this point. It came from the successful sign-in flow in Lane A and identifies the caller. X-Tenant-ID and X-Deployment-Key are requests, not proof of permission. A caller can name any tenant and any deployment key, but the next phase decides whether that caller is actually allowed to use that deployment in that tenant.",
      "X-Tenant-ID must be a UUID, the long unique identifier used internally for a tenant. X-Deployment-Key is a human-chosen, URL-safe lookup name that an administrator registered in advance, such as support-gpt or invoice-summariser. It is not invented for each request, it is not the database's internal identifier, and it is not the AI configuration itself. It is the handle used to find that configuration later.",
      "A deployment key can contain up to 128 characters. It must start with a letter or number. The remaining characters may be letters, numbers, periods, hyphens, or underscores. A tenant ID that is not a UUID, or a deployment key that breaks those rules, is invalid and is returned as 422 Unprocessable Entity.",
      "A valid-looking deployment key is still not proof that it exists. For example, support-gpt can satisfy the formatting rules even when no deployment with that key has been registered for the named tenant. The later Access Control phase looks up the deployment and decides whether it exists, is active, and is available to the caller.",
      "The caller does not send a provider name, model name, API key, or provider URL. The deployment selected by the key determines the provider and model. After authorization succeeds, the service reads the caller's matching permission record to obtain the provider endpoint and the reference to the stored credential. It then combines those values with provider settings, such as timeout and token limits, to create the execution route.",
      "Before access is considered, the service validates the bearer token and identifies the caller. The later steps use that verified identity, together with the tenant ID and deployment key, to decide whether the request may proceed.",
    ],
  },
  IDENT: {
    title: "2 · Is The Sign-In Token Genuine?",
    sub: "The service checks that the sign-in token is genuine, valid, and current before it considers the requested tenant or AI setup.",
    paragraphs: [
      "The app sends the access token it received after sign-in in the Authorization header. The service checks that the token was signed with the trusted secret and has not been changed since it was issued. A token that has been changed or forged cannot pass this check.",
      "The service also checks the token's purpose and timing. It must be an access token, not another kind of token. It must come from a trusted issuer, be intended for this service, and contain all required identity details. It must not be expired, issued in the future, or valid for longer than the maximum lifetime allowed by the service.",
      "The token contains the caller's identity and platform role. That role must be one the service recognises. A token can therefore be correctly signed and still be rejected because its contents are incomplete, invalid, or no longer acceptable.",
      "These checks happen inside the running service using its configured security settings. No database lookup or network request is needed. This allows the service to establish who the caller is before it reads tenant or deployment information.",
      "The access token does not name a tenant. One person can be a member of more than one tenant, sometimes with different responsibilities in each one. The app therefore names the tenant separately on every request. The service checks that requested tenant later, rather than treating the token as automatic permission for every tenant the person belongs to.",
      "The platform role in the access token is not the final permission decision. It describes the person's role across the service as a whole. The next phase checks the person's separate membership role and specific permission within the tenant they requested. A valid token proves identity first. Permission to use a particular AI setup is decided afterward.",
      "This access token is unrelated to AI token quota. Here, \"token\" means proof of identity. Later in the flow, the service separately checks whether enough AI processing capacity is available for the requested work.",
      "If any identity check fails, the request ends with 401 Unauthorized and the person must sign in again to obtain a valid access token.",
    ],
  },
  DENY401: {
    title: "Sign-In Token Rejected",
    sub: "The service could not establish a valid identity, so the request stops before tenant or deployment access is checked.",
    paragraphs: [
      "The service returns 401 Unauthorized when the Authorization header is missing, when the access token has expired, when its signature cannot be trusted, or when its contents do not meet the required access-token rules. In plain language, the service cannot safely confirm who made this request.",
      "No tenant, deployment, provider, or AI model is looked up after this failure. The request ends at the identity check. This prevents unauthenticated callers from reaching information about tenant configuration or available AI setups.",
      "If the access token is invalid and either X-Tenant-ID or X-Deployment-Key is malformed at the same time, the service returns 401 Unauthorized rather than a header-format error. The caller must first provide a valid identity before learning whether the tenant ID or deployment key was correctly formatted.",
      "The app should send the person back to sign in. A successful sign-in creates a new access token, which the app can use on a new request.",
    ],
  },

  AUTHCTX: {
    title: "3 · May This User Use This Deployment?",
    sub: "The service now asks one permission question: may this verified person use this saved AI setup inside this tenant?",
    paragraphs: [
      "The sign-in token has proved who the caller is. The service now combines that verified identity with X-Tenant-ID and X-Deployment-Key from the request. Together, these values form the permission question for every chat, embedding, and reranking request.",
      "A tenant is an organisation boundary. X-Tenant-ID means \"I want to act within this tenant.\" It does not mean \"I belong to this tenant.\" A caller may name any tenant in a request, but access is granted only when the service finds an active membership that permits AI use in that tenant.",
      "The deployment key identifies one saved AI setup within the named tenant. Deployment keys are unique only within a tenant. For example, two different tenants can both have a deployment named support-gpt, but they are separate deployments and permission for one gives no access to the other.",
      "The platform role inside the access token is not used to answer this question. It describes the caller's role across the service as a whole. Access within a particular tenant is decided separately using the caller's tenant membership role. A viewer, for example, is read-only and cannot run AI requests, while an eligible tenant role can continue to the next checks.",
      "The final permission is an active entitlement. An entitlement is the record that gives this person permission to use this exact deployment with its exact provider and model. It is more specific than tenant membership. Membership asks whether the person may use AI in the tenant at all. The entitlement asks whether they may use this particular saved AI setup.",
      "At this point, the service does not yet ask whether the caller wants a chat answer, an embedding vector, or a reranked document list. It only asks, \"May this person use this deployment in this tenant?\" Once the answer is yes, the service reads the deployment's provider and model. It then checks whether that model supports the requested task. For example, a person may be allowed to use support-gpt, but the request is rejected later if its configured model cannot create embedding vectors.",
      "If access is approved, the service keeps the resulting identity, tenant, deployment, provider, model, and entitlement identifiers together for later steps. Later parts of the request can use that approved context instead of repeating this permission decision.",
    ],
  },
  CACHE: {
    title: "4 · Recent Approval Cached?",
    sub: "Redis may remember a recent approval to save database work, but PostgreSQL remains the source of truth.",
    blocks: [
      { type: "heading", text: "Why Redis Is Checked" },
      { type: "paragraph", text: "Before repeating the full permission check, the service asks Redis whether it already holds a recent approval for this exact person, tenant, and deployment." },
      { type: "paragraph", text: "Redis only makes repeated requests faster. PostgreSQL remains the source of truth that originally approved the access." },

      { type: "heading", text: "The Approval Key" },
      { type: "code", text: "inference_authz:{tenant ID}:{user ID}:{deployment key}" },
      { type: "paragraph", text: "Example:" },
      { type: "code", text: "inference_authz:9f0e2dd4-9ec7-4b8e-ae45-91be5d34a711:4c669c1f-669c-4b73-a275-7c46b36210fb:support-gpt" },
      { type: "paragraph", text: "Each person, tenant, and deployment combination receives a different key. The example means that user 4c...10fb was approved to use support-gpt inside tenant 9f...a711." },

      { type: "heading", text: "What The Approval Contains" },
      { type: "code", text: `{
  "context": {
    "tenant_id": "9f0e2dd4-9ec7-4b8e-ae45-91be5d34a711",
    "user_id": "4c669c1f-669c-4b73-a275-7c46b36210fb",
    "deployment_key": "support-gpt",
    "deployment_id": "17392e64-9dab-4c1d-ae46-c0fc6e3dfa6d",
    "provider_id": "a38b4b95-9e0c-4b60-b162-2a24c3e35456",
    "model_id": "da7dd9d6-16f7-4ac3-9a73-d14dd832a05a",
    "tenant_role": "developer",
    "entitlement_id": "ce963f83-ca13-4b89-b29a-8c9e6b330907"
  },
  "versions": {
    "tenant_version": "tenant:0",
    "membership_version": "membership:0",
    "deployment_version": "deployment:0",
    "route_version": "route:0"
  }
}` },
      { type: "paragraph", text: "The context section answers, \"Who was approved to use what?\" It records the approved person, tenant, deployment, provider, model, tenant role, and entitlement." },
      { type: "paragraph", text: "It does not contain the provider URL, secret reference, API key, or plaintext credential." },

      { type: "heading", text: "How Redis Decides Whether An Approval Is Still Current" },
      { type: "paragraph", text: "Redis treats an approval as a short-lived copy of a previous \"yes.\" Before reusing it, the service must answer one question: has anything relevant changed since this approval was created?" },
      { type: "paragraph", text: "It checks four things: the tenant, the person's membership in that tenant, the saved AI setup, and the person's exact permission to use that setup. Each has a small version value that changes when an administrator updates it." },
      { type: "paragraph", text: "The cached approval stores the four values that existed when it was created. On the next request, Redis reads the latest four values and compares them with the saved ones. If every value is the same, the approval is still current. If any value differs, the service ignores the old approval and checks PostgreSQL again." },

      { type: "heading", text: "What A Cache Hit Or Miss Means" },
      { type: "list", items: [
        { label: "Cache hit", text: "Redis found an approval and all four version values still match. The request can continue without repeating the database permission check." },
        { label: "Cache miss", text: "Redis has no approval, cannot read one safely, or found one that is no longer current. The service checks PostgreSQL." },
        { label: "Redis unavailable", text: "The service treats Redis as unavailable memory and checks PostgreSQL." },
        { label: "Rejected access", text: "A refusal is never stored. Redis remembers only successful approvals." },
      ] },

      { type: "heading", text: "How Long A Saved Approval Lasts" },
      { type: "paragraph", text: "A saved approval expires automatically after 30 seconds by default. The permitted setting range is 1 to 300 seconds. Expiry is a backup safety limit. The version comparison is intended to detect relevant changes sooner." },

      { type: "heading", text: "Current Code Issue" },
      { type: "paragraph", text: "The version comparison cannot currently work reliably after an administrator changes a tenant, membership, deployment, or permission. The stored default values look like tenant:0, but the update path writes a different shape, such as v:<unique ID>. The reader expects the first shape and rejects the second." },
      { type: "paragraph", text: "Until that defect is repaired, Redis can still be treated as an optional speed improvement, because the service falls back to PostgreSQL when it cannot use the cached approval. But the system should not claim immediate cache invalidation is working correctly." },
    ],
  },
  GATES: {
    title: "5 · Look It Up In The Database",
    sub: "PostgreSQL answers four permission questions in a fixed order. The first \"no\" stops the request.",
    blocks: [
      { type: "heading", text: "What This Step Decides" },
      { type: "paragraph", text: "Redis had no usable remembered approval, so PostgreSQL now becomes the source of truth. The service checks whether this verified person may use the requested AI setup inside the requested tenant." },

      { type: "heading", text: "The Four Questions" },
      { type: "ordered", items: [
        { label: "Is the tenant real and able to receive AI requests", text: "The tenant must exist and its status must be active or trial." },
        { label: "Is this person an active member who may use AI", text: "The person must have an active membership in the tenant and a tenant role that permits AI use. A viewer can read information but cannot submit an AI request." },
        { label: "Does the saved AI setup exist and is it active", text: "The deployment key must identify a deployment within this tenant, and that deployment must be switched on." },
        { label: "Does this person have permission for this exact AI setup", text: "An active entitlement must connect this tenant, this person, this deployment, and the provider and model selected by that deployment." },
      ] },

      { type: "heading", text: "Why The Questions Are In This Order" },
      { type: "paragraph", text: "The checks run from broad and inexpensive to specific. The service first confirms that the tenant exists. Only then does it read membership. Only after confirming membership does it look up the deployment. It checks the most specific permission last, because that permission depends on the deployment's provider and model." },
      { type: "paragraph", text: "Membership and entitlement are not duplicates. Membership answers, \"May this person use AI in this tenant at all?\" Entitlement answers, \"May this person use this particular AI setup?\"" },

      { type: "heading", text: "About The Deployment Key" },
      { type: "paragraph", text: "The request header accepts a broad format so the service can safely receive the key. A key that was actually registered follows a stricter lowercase, hyphen-separated format, such as support-gpt." },
      { type: "paragraph", text: "For example, Support_GPT can pass the initial header-format check but still match no registered deployment in this tenant. It is therefore returned as 404 Not Found at this step, because there is no saved AI setup with that exact key." },

      { type: "heading", text: "What Happens After A Yes" },
      { type: "paragraph", text: "When all four questions pass, the service creates an approved access record containing the tenant, person, deployment, provider, model, tenant role, and entitlement identifiers. It then attempts to save that approval in Redis so a later identical request can avoid repeating these database checks." },

      { type: "heading", text: "How Failures Are Reported" },
      { type: "list", items: [
        { label: "404 Not Found", text: "The named tenant or deployment does not exist for this request." },
        { label: "403 Forbidden", text: "The tenant exists, but the person is not an eligible active member or does not have the required permission." },
        { label: "422 Unprocessable Entity", text: "The deployment exists, but it is inactive and cannot serve AI traffic." },
      ] },
    ],
  },
  APPROVED: {
    title: "Access Approved",
    sub: "The request has been approved to use one deployment in one tenant, whether Redis remembered the approval or PostgreSQL checked it again.",
    blocks: [
      { type: "heading", text: "Why Two Paths Meet Here" },
      { type: "paragraph", text: "Access can be approved in two ways. Redis may provide a still-current remembered approval, or PostgreSQL may perform the four database checks and create a fresh approval." },
      { type: "paragraph", text: "Both paths arrive here with the same result: the caller is allowed to use the requested deployment inside the requested tenant. From this point onward, the next phase does not need to know how that approval was obtained." },

      { type: "heading", text: "What Has Been Approved" },
      { type: "paragraph", text: "The service now carries the identifiers for the approved person, tenant, deployment, provider, model, tenant role, and exact permission record. These identifiers are the evidence that the request passed Access Control." },
      { type: "paragraph", text: "The approval answers only one question: \"May this person use this deployment in this tenant?\" It does not yet determine how to contact an AI provider or whether the selected model can perform the requested task." },

      { type: "heading", text: "What Does Not Move Forward" },
      { type: "paragraph", text: "No provider name, provider URL, API key, secret reference, timeout, temperature, token limit, or other AI connection setting is available at this point." },
      { type: "paragraph", text: "The next phase reads the approved permission record and provider configuration to build the complete execution route. Keeping permission approval separate from connection details prevents later steps from using unapproved or mismatched AI settings." },
    ],
  },
  DENYACL: {
    title: "Access Denied",
    sub: "The request stops when the tenant, membership, AI setup, or permission check does not pass.",
    blocks: [
      { type: "heading", text: "What These Responses Mean" },
      { type: "paragraph", text: "The service has already confirmed the caller's identity. It now knows who made the request, but one of the database permission checks did not allow the requested AI work to continue." },
      { type: "list", items: [
        { label: "404 Not Found", text: "The requested tenant does not exist, or the requested deployment key does not identify a saved AI setup in that tenant." },
        { label: "403 Forbidden", text: "The tenant exists, but it is suspended, the person is not an eligible active member, or the person does not have permission for the requested AI setup." },
        { label: "422 Unprocessable Entity", text: "The deployment exists and is known, but it is switched off and cannot receive AI traffic." },
      ] },

      { type: "heading", text: "Two Different 422 Responses" },
      { type: "paragraph", text: "A 422 at this point means the saved AI setup is inactive. It exists, but the administrator has switched it off." },
      { type: "paragraph", text: "A later 422 in Resolve Deployment means something different. The saved AI setup is active, but its configured model cannot perform the requested work. For example, an active chat model may not support embedding vectors." },

      { type: "heading", text: "Why Redis Does Not Cause This Outcome" },
      { type: "paragraph", text: "Redis is only a performance shortcut. If Redis is unavailable, missing an approval, or unable to read one, the service treats that as a cache miss and performs the four database checks. Redis being down does not itself deny access." },
      { type: "paragraph", text: "If PostgreSQL is unavailable, the service cannot answer the permission question at all. There is no dedicated database-outage response for this authorization step. The request reaches the general unexpected-error handling and returns 500 Internal Server Error, with a request ID for investigation. It does not return a designed 503 Service Unavailable response." },

      { type: "heading", text: "The Complete Decision Path" },
      { type: "ordered", items: [
        { label: "Tenant missing", text: "404 Not Found." },
        { label: "Tenant suspended", text: "403 Forbidden." },
        { label: "No active membership, or membership role cannot use AI", text: "403 Forbidden." },
        { label: "Deployment missing", text: "404 Not Found." },
        { label: "Deployment inactive", text: "422 Unprocessable Entity." },
        { label: "No active permission for that person and deployment", text: "403 Forbidden." },
      ] },
    ],
  },

  LIVE: {
    title: "6 · Resolve Entitlement Configuration",
    sub: "Access Control approved who may use the deployment. PostgreSQL now supplies the current details needed to prepare the AI call.",
    blocks: [
      { type: "heading", text: "What Access Control Approved" },
      { type: "paragraph", text: "Access Control has already approved these facts:" },
      { type: "list", items: [
        { label: "Person", text: "The verified person identified by the sign-in token." },
        { label: "Tenant", text: "The organisation in which the person wants to perform the AI request." },
        { label: "Tenant membership", text: "The person is an active member of that tenant and has a tenant role that permits AI use." },
        { label: "Deployment", text: "The requested deployment key identifies an active saved AI setup inside that tenant." },
        { label: "Provider and model identifiers", text: "The deployment points to a specific provider record and model record." },
        { label: "Entitlement", text: "An active permission record allows this person to use that exact deployment, provider, and model combination." },
      ] },
      { type: "paragraph", text: "This approval proves permission. It does not yet provide the provider name, model name, endpoint, region, credential location, or provider-specific settings needed to prepare the call." },

      { type: "heading", text: "What PostgreSQL Reads Now" },
      { type: "paragraph", text: "PostgreSQL performs two fresh reads." },

      { type: "heading", text: "1 · The Tenant Record" },
      { type: "list", items: [
        { label: "Tenant identity", text: "Confirms which tenant is being used." },
        { label: "Current tenant status", text: "Confirms that the tenant still exists and is still active or in trial." },
        { label: "Subscription tier", text: "Loads the tenant's current service tier." },
        { label: "Request limit", text: "Loads the configured request allowance." },
        { label: "Token limit", text: "Loads the configured AI-token allowance." },
        { label: "Concurrent-request limit", text: "Loads the number of AI requests the tenant may run at once." },
        { label: "Allowed providers", text: "Loads the current list of AI providers this tenant is permitted to use." },
      ] },
      { type: "paragraph", text: "This step immediately uses the tenant's status. The allowed-provider list is used by the next policy check. The other tenant settings are included in the current tenant configuration but are not all enforced at this exact box." },

      { type: "heading", text: "2 · The Exact Entitlement Approved Earlier" },
      { type: "list", items: [
        { label: "Entitlement identity", text: "Confirms that the same permission record approved by Access Control is being read." },
        { label: "Person and tenant", text: "Confirms that the entitlement still belongs to the same person and tenant." },
        { label: "Deployment key", text: "Not a field on the result. It is one of four values — tenant, user, deployment key, entitlement id — the query matches in its WHERE clause; if any one is wrong, no row comes back at all, rather than a mismatch being caught afterward." },
        { label: "Current entitlement status", text: "Confirms that the entitlement has not been revoked or deactivated." },
        { label: "Provider name", text: "Identifies the AI company or platform." },
        { label: "Model name", text: "Identifies the model selected for this person." },
        { label: "Provider endpoint", text: "Supplies the network address used for the future provider call." },
        { label: "Secret reference", text: "Supplies the location of the provider credential, but not the credential itself." },
        { label: "Cloud provider and region", text: "Supplies optional routing information for cloud-hosted models." },
        { label: "Additional configuration", text: "Supplies provider-specific settings stored with the entitlement." },
      ] },

      { type: "heading", text: "Why Provider And Model Names Require A Join" },
      { type: "list", items: [
        { label: "What the entitlement stores", text: "Internal provider and model identifiers." },
        { label: "What later steps need", text: "Readable provider and model names, such as openai and gpt-4o." },
        { label: "What PostgreSQL does", text: "It joins the entitlement to the provider and model catalogs and returns the corresponding names." },
        { label: "What PostgreSQL does not read", text: "The deployment table is not queried again at this step." },
      ] },

      { type: "heading", text: "Example" },
      { type: "paragraph", text: "Access Control has approved:" },
      { type: "code", text: `Person:       Priya
Tenant:       Acme
Deployment:   support-gpt
Entitlement:  priya-support-access
Provider ID:  a38b4b95...
Model ID:     da7dd9d6...` },
      { type: "paragraph", text: "PostgreSQL now reads the current tenant details:" },
      { type: "code", text: `Tenant:               Acme
Status:               active
Subscription tier:    enterprise
Request limit:        600 per minute
Token limit:          500,000 per minute
Concurrent requests:  25
Allowed providers:    openai, bedrock` },
      { type: "paragraph", text: "PostgreSQL also reads Priya's exact entitlement:" },
      { type: "code", text: `Entitlement:       priya-support-access
Status:            active
Provider:          openai
Model:             gpt-4o
Endpoint:          https://api.openai.com/v1
Cloud region:      not required
Secret reference:  vault://acme/priya/openai-key
Additional config: {"organization": "acme-support"}` },

      { type: "heading", text: "What The Example Means" },
      { type: "list", items: [
        { label: "Permission is already proven", text: "Priya may use support-gpt inside Acme." },
        { label: "The tenant is still usable", text: "Acme remains active." },
        { label: "The provider is identified", text: "The request is associated with OpenAI." },
        { label: "The model is identified", text: "The request is associated with gpt-4o." },
        { label: "The destination is known", text: "The provider endpoint has been loaded." },
        { label: "The credential location is known", text: "The service knows where the OpenAI credential is stored." },
        { label: "The credential is not loaded", text: "The actual API key remains in the secret store and is retrieved later." },
      ] },

      { type: "heading", text: "Why These Records Are Read Again" },
      { type: "list", items: [
        { label: "Immediate revocation", text: "A recently revoked entitlement is rejected on the next request." },
        { label: "Current status", text: "A newly suspended tenant or deactivated entitlement is not allowed to continue." },
        { label: "Current routing", text: "Changes to the endpoint, region, provider, model, credential location, or additional settings become visible immediately." },
        { label: "No substitution", text: "If the approved entitlement is missing or inactive, the service does not search for another entitlement or fallback credential." },
      ] },

      { type: "heading", text: "When This Step Stops The Request" },
      { type: "list", items: [
        { label: "Tenant missing", text: "404 Not Found." },
        { label: "Tenant no longer active", text: "403 Forbidden." },
        { label: "Approved entitlement missing, revoked, or inactive", text: "403 Forbidden." },
        { label: "Returned entitlement does not match the approved entitlement", text: "500 Internal Server Error." },
        { label: "PostgreSQL unavailable", text: "500 Internal Server Error. There is currently no dedicated 503 Service Unavailable response for this database read." },
      ] },
    ],
  },
  PLANBOX: {
    title: "7 · May this tenant use this AI provider and model for chat, embedding, or reranking?",
    sub: "The selected provider must be allowed for this tenant, and the selected model must support this endpoint's operation.",
    blocks: [
      { type: "paragraph", text: "The earlier steps confirmed that this person may use the selected deployment. This step answers a different question: can the provider and model selected by that deployment perform the type of AI request that just arrived?" },

      { type: "heading", text: "Where the information comes from" },
      { type: "paragraph", text: "Before this step begins, the preceding step reads two current records from PostgreSQL:" },
      { type: "list", items: [
        { label: "Tenant configuration", text: "Whether the tenant is active and which AI providers, if any, that tenant permits." },
        { label: "Exact approved entitlement", text: "The active permission record for this person, tenant, and deployment. It identifies the provider and model selected for the request." },
      ] },
      { type: "paragraph", text: "For example, the entitlement might identify OpenAI and `gpt-4o` as the provider and model selected for Priya's `customer-support` deployment." },
      { type: "paragraph", text: "Step 7 does not make another PostgreSQL query. It uses those already-read tenant and entitlement details, then checks the provider configuration loaded into memory when the service started." },
      { type: "paragraph", text: "That startup configuration is stored in provider YAML files. It lists each provider's registered models and the AI operations each model supports." },
      { type: "paragraph", text: "The PostgreSQL `model_catalog` table is used by the management area to register and manage models. It is not the catalog consulted by this step. A model registered in PostgreSQL still needs a matching entry in its provider's startup configuration before this step can use it." },

      { type: "heading", text: "Check 1: Is this provider allowed for the tenant?" },
      { type: "paragraph", text: "The tenant can limit which AI providers it permits." },
      { type: "list", items: [
        { label: "No provider list", text: "The tenant permits every provider known to the running service." },
        { label: "A list with provider names", text: "The tenant permits only the providers on that list." },
        { label: "An empty list", text: "The tenant permits no providers." },
      ] },
      { type: "paragraph", text: "This is a rule for one tenant, not a global provider on or off switch." },
      { type: "paragraph", text: "If the provider selected by the entitlement is excluded from the tenant's list, the request stops with **403 Forbidden**." },
      { type: "paragraph", text: "This check does not confirm that an API key exists or that the provider can be reached. The secret is read from Vault later, immediately before the external AI call." },

      { type: "heading", text: "Check 2: Is the provider and model known to the running service?" },
      { type: "paragraph", text: "The service next checks the startup-loaded provider configuration." },
      { type: "list", items: [
        { text: "The provider must have a configuration file loaded when the service started." },
        { text: "The selected model must be listed under that provider." },
        { text: "A model listed for one provider is not automatically available from another provider." },
      ] },
      { type: "paragraph", text: "If the provider is missing from the running service's configuration, the database and the running service disagree. For example, PostgreSQL may contain an entitlement for OpenAI, but the running service may have started without the OpenAI provider configuration. The caller cannot fix that mismatch, so it currently reaches the caller as an internal **500 error**." },
      { type: "paragraph", text: "If the provider is known but the model is not listed under it, the request stops with **422 Unprocessable Entity**." },

      { type: "heading", text: "Check 3: Can the model perform this endpoint's operation?" },
      { type: "paragraph", text: "A known model must support the operation represented by the endpoint:" },
      { type: "list", items: [
        { label: "Chat", text: "Generate a conversational response." },
        { label: "Embedding", text: "Convert text into numerical vectors." },
        { label: "Reranking", text: "Reorder supplied results by relevance." },
      ] },
      { type: "paragraph", text: "These are the only capabilities checked here. Streaming, tool calling, structured output, and vision are not separate checks at this point." },
      { type: "paragraph", text: "A model can remain listed in the startup catalog even when marked deprecated. This step does not reject it for that reason. It checks only whether the model supports chat, embedding, or reranking." },
      { type: "paragraph", text: "If the model cannot perform the endpoint's operation, the request stops with **422 Unprocessable Entity**." },

      { type: "heading", text: "Example" },
      { type: "paragraph", text: "Suppose Acme Support permits OpenAI, and Priya is approved to use a deployment whose entitlement selects `text-embedding-3-small`. Priya sends a chat request:" },
      { type: "ordered-list", items: [
        "The provider check passes because Acme Support permits OpenAI.",
        "The provider and model check passes because OpenAI and `text-embedding-3-small` are known to the running service.",
        "The capability check fails because that model supports embedding, not chat.",
        "The service returns **422** before checking capacity, reading Vault, or contacting OpenAI.",
      ] },
      { type: "paragraph", text: "The service does not quietly replace the model with a chat-capable alternative." },

      { type: "heading", text: "Why this check happens after access control" },
      { type: "paragraph", text: "Access Control answered, \"May this person use this deployment inside this tenant?\" It did not answer, \"Can the selected model perform the operation requested by this endpoint?\"" },
      { type: "paragraph", text: "That is why a cached access approval still reaches this step. Permission to use a deployment does not mean its selected model can perform every kind of AI request." },

      { type: "heading", text: "Possible outcomes" },
      { type: "list", items: [
        { label: "Provider is not permitted for this tenant", text: "403." },
        { label: "Provider is missing from the running service's configuration", text: "500." },
        { label: "Model is not listed under that provider", text: "422." },
        { label: "Model does not support chat, embedding, or reranking as requested", text: "422." },
        { label: "Every check passes", text: "Continue to capacity checks and credential retrieval." },
      ] },
    ],
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
    title: "Create the fixed plan for this AI call",
    sub: "Gather every approved routing decision into one read-only plan for the remaining stages of this request.",
    blocks: [
      { type: "paragraph", text: "Steps 6 and 7 have now answered every routing question for this request:" },
      { type: "list", items: [
        { text: "Which tenant and deployment are involved." },
        { text: "Which AI provider and model were selected." },
        { text: "Where the provider request should be sent." },
        { text: "Which credential location will be used later." },
        { text: "How long the provider call may run." },
        { text: "How creative a chat response may be." },
        { text: "How many tokens a chat response may generate." },
      ] },
      { type: "paragraph", text: "This step gathers those answers into one fixed plan that the remaining stages use for this request." },

      { type: "heading", text: "What the completed plan looks like" },
      { type: "paragraph", text: "The completed plan is a validated, read-only record held in the running application. It is not a database row, and it is not sent to the caller." },
      { type: "code", language: "text", text: "Completed plan\n├── Tenant and deployment\n│   ├── tenant ID\n│   └── deployment key\n│\n├── AI route\n│   ├── provider name\n│   ├── model name\n│   ├── provider endpoint address\n│   └── cloud region, when applicable\n│\n├── Operating limits\n│   ├── timeout\n│   ├── temperature\n│   └── maximum output tokens\n│\n├── Credential handling\n│   └── credential location, never the API key itself\n│\n├── Provider settings\n│   ├── full startup-loaded provider configuration\n│   ├── additional approved entitlement settings\n│   └── extra request headers, currently empty at creation\n│\n└── Internal tracking\n    ├── usage-meter identity\n    └── route identity" },
      { type: "paragraph", text: "For example, Priya's completed plan may contain:" },
      { type: "code", language: "text", text: "Tenant: Acme Support\nDeployment: customer-support\n\nProvider: OpenAI\nModel: gpt-4o\nEndpoint: https://api.openai.com/v1\nRegion: none\n\nTimeout: 60 seconds\nTemperature: 0.7\nMaximum response length: 4,096 tokens\n\nCredential location: secret/acme/openai/customer-support\nAPI key: not included\n\nUsage-meter identity: Priya's approved entitlement ID\nRoute identity: a fixed SHA-256 fingerprint" },
      { type: "paragraph", text: "The API key is not copied into this plan. Think of the credential location as a locker number written on an instruction sheet. The key remains in the locker until the later Vault step retrieves it." },

      { type: "heading", text: "What comes from the approved entitlement" },
      { type: "paragraph", text: "The approved entitlement, read from PostgreSQL in the earlier step, supplies the details specific to this person's permitted AI setup:" },
      { type: "list", items: [
        { label: "Provider and model", text: "For example, OpenAI and `gpt-4o`." },
        { label: "Endpoint address", text: "The provider URL to contact." },
        { label: "Cloud region", text: "When the provider uses one." },
        { label: "Credential location", text: "A pointer to where the API key is stored." },
        { label: "Additional provider settings", text: "Any approved settings specific to this entitlement." },
        { label: "Usage-meter identity", text: "The identifier used later when the token service records this request's quota usage." },
      ] },

      { type: "heading", text: "What comes from the startup catalog" },
      { type: "paragraph", text: "The provider and model catalog checked in Step 7 supplies shared defaults:" },
      { type: "list", items: [
        { label: "Timeout", text: "How long the provider call may wait before it is treated as unsuccessful." },
        { label: "Temperature", text: "The default level of variation for a chat response." },
        { label: "Maximum output tokens", text: "The largest response the selected model may generate." },
        { label: "Provider configuration", text: "The provider's connection and authentication rules, without any actual API secret." },
      ] },
      { type: "paragraph", text: "The plan keeps the full provider configuration so later stages do not need to load that catalog again." },

      { type: "heading", text: "What this step does not do" },
      { type: "list", items: [
        { text: "Make another PostgreSQL query." },
        { text: "Read Vault." },
        { text: "Retrieve the API key." },
        { text: "Contact the AI provider." },
        { text: "Reserve token capacity." },
        { text: "Choose a different provider or model." },
      ] },
      { type: "paragraph", text: "It only assembles information already approved and verified." },

      { type: "heading", text: "Why make one fixed plan?" },
      { type: "paragraph", text: "Without this step, each later stage would need to rediscover the provider, model, timeout, credential location, and limits. That creates two risks:" },
      { type: "list", items: [
        { label: "Inconsistent decisions", text: "Different stages could use different versions of the configuration." },
        { label: "Repeated work", text: "The service would repeat database and catalog lookups whose answers have already been established." },
      ] },
      { type: "paragraph", text: "The completed plan means every remaining stage works from the same approved answers for this one request." },

      { type: "heading", text: "It cannot be changed after creation" },
      { type: "paragraph", text: "The plan is deliberately locked after it is created." },
      { type: "paragraph", text: "Later stages can read its values, but cannot:" },
      { type: "list", items: [
        { text: "Switch to a cheaper model." },
        { text: "Change the provider endpoint." },
        { text: "Select another credential location." },
        { text: "Increase the output-token limit." },
        { text: "Add an unexpected setting." },
      ] },
      { type: "paragraph", text: "For example, if `gpt-4o` is the approved model, the capacity step cannot decide to use `gpt-4o-mini` instead. The provider call must use the route that was already approved." },

      { type: "heading", text: "Two internal identities used later" },
      { type: "list", items: [
        { label: "Usage-meter identity", text: "Tells the token service which approved entitlement this request spends from." },
        { label: "Route identity", text: "A one-way SHA-256 fingerprint calculated from the deployment key and the full approved entitlement details. The service uses it to recognise requests that share the same saved route and can reuse a cached provider connection." },
      ] },
      { type: "paragraph", text: "The route identity changes when the deployment key or approved entitlement changes. It does not include the provider and model defaults loaded from the startup catalog." },

      { type: "heading", text: "What happens next" },
      { type: "list", items: [
        { text: "Capacity checks use the provider, model, and maximum output length." },
        { text: "Vault uses the credential location to retrieve the API key only when needed." },
        { text: "The provider call uses the selected endpoint, region, model, and settings." },
        { text: "The token service uses the usage-meter identity to record quota consumption." },
      ] },
    ],
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
    title: "Why was this AI route rejected?",
    sub: "A 403, 422, or 500 here identifies whether the problem is the tenant's provider policy, the selected model, or the service's own configuration.",
    blocks: [
      { type: "paragraph", text: "The person is allowed to use the selected deployment, but that approval is only the first part of the decision. The service must still confirm that the deployment leads to an AI provider this tenant permits and to a model that can handle the request that just arrived." },
      { type: "paragraph", text: "Think of the deployment as a saved travel plan. Access Control confirmed that this person is allowed to use the plan. This step checks whether the destination is permitted, whether it exists in the service's current map, and whether it can provide the requested service." },

      { type: "heading", text: "When the response is 403" },
      { type: "paragraph", text: "A **403 Forbidden** means the tenant's own policy does not permit the provider selected by the entitlement." },
      { type: "paragraph", text: "For example, Priya may be allowed to use Acme Support's `customer-support` deployment, and that deployment may select OpenAI. If Acme Support currently permits only Anthropic, Priya's permission to use the deployment does not override the tenant's provider policy. The request stops with 403." },
      { type: "paragraph", text: "Nothing is necessarily wrong with OpenAI, the model, Priya's account, or her entitlement. The provider is simply not permitted for this tenant." },
      { type: "paragraph", text: "A missing provider list means the tenant has placed no provider restriction. A populated list permits only the providers named in it." },
      { type: "paragraph", text: "The intended meaning of an empty list is that the tenant permits no providers. The current implementation does not preserve that distinction correctly. It treats an empty list like a missing list and therefore permits every provider. That is a known defect recorded in the production review document." },

      { type: "heading", text: "When the response is 422" },
      { type: "paragraph", text: "A **422 Unprocessable Entity** means the provider is permitted, but the selected model cannot be used for this request." },
      { type: "paragraph", text: "One possibility is that the entitlement names a model that is not present under that provider in the catalog loaded when the service started. PostgreSQL may contain the model and the entitlement, but the running service does not recognise that model in its startup configuration." },
      { type: "paragraph", text: "The other possibility is that the model is known but cannot perform the operation represented by the endpoint." },
      { type: "paragraph", text: "For example, `text-embedding-3-small` may be correctly registered as an embedding model. If Priya sends it to the chat endpoint, the service does not guess, replace it with a chat model, or send a request that is expected to fail. It stops here with 422 because the approved model cannot perform chat." },
      { type: "paragraph", text: "There is another 422 earlier in Access Control, but it means something different. The earlier 422 says the deployment itself is switched off. The 422 here says the deployment is active and approved, but its selected model is unknown to the running service or cannot perform chat, embedding, or reranking as requested." },

      { type: "heading", text: "When the response is 500" },
      { type: "paragraph", text: "A **500 Internal Server Error** means the database and the running service disagree about the provider." },
      { type: "paragraph", text: "For example, PostgreSQL may contain an active entitlement that selects OpenAI, while the running service may have started without loading the OpenAI provider configuration. The database says which provider to use, but the application does not know how to operate it." },
      { type: "paragraph", text: "That is not something Priya can fix by changing her request. It is an internal configuration problem that an operator must correct." },

      { type: "heading", text: "What happens after any of these failures" },
      { type: "paragraph", text: "The request ends here. The service does not choose another provider, substitute another model, reserve capacity, retrieve an API key, or contact an AI company." },
      { type: "paragraph", text: "Related failures can also stop one step earlier. A missing tenant returns 404, a suspended tenant returns 403, and a missing, revoked, or inactive entitlement returns 403. Those requests never reach the provider and model checks described here." },
    ],
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
    title: "8a · Can this process start another live-streaming response?",
    sub: "One live-streaming chat uses one temporary permission from this process's in-memory capacity limit.",
    blocks: [
      { type: "paragraph", text: "A streaming chat sends the answer to the client gradually. The client begins receiving words while the AI provider is still generating the rest of the response." },
      { type: "paragraph", text: "Because the answer remains open for that entire time, one streaming chat can occupy a client connection, a provider connection, memory, and background processing for much longer than a normal request." },
      { type: "paragraph", text: "Before starting another streaming answer, this process checks whether it has room to keep one more stream open." },

      { type: "heading", text: "What a streaming slot means" },
      { type: "paragraph", text: "A streaming slot is one temporary permission to run one live-streaming chat." },
      { type: "paragraph", text: "It is not a physical object and it does not contain the response. The process keeps a simple count of how many streaming chats it is currently responsible for." },
      { type: "paragraph", text: "Suppose the configured limit is 20. When the first streaming chat starts, the count becomes 1 and 19 slots remain. When the twentieth starts, the count becomes 20 and no more slots are available in this process." },
      { type: "paragraph", text: "The twenty-first streaming request is refused immediately." },

      { type: "heading", text: "How long one slot is held" },
      { type: "paragraph", text: "The slot is claimed before token capacity is reserved and before the AI provider stream is created." },
      { type: "paragraph", text: "If preparation fails, such as when token capacity cannot be reserved or the provider cannot be prepared, the slot is returned immediately." },
      { type: "paragraph", text: "If preparation succeeds, the slot remains occupied while the answer is being streamed. It is returned when:" },
      { type: "list", items: [
        { text: "The provider finishes the answer." },
        { text: "The provider reports an error." },
        { text: "The caller disconnects." },
        { text: "Stream cleanup finishes after an interruption." },
      ] },
      { type: "paragraph", text: "The release is protected so that two cleanup paths cannot accidentally return the same slot twice." },

      { type: "heading", text: "Why the default is 20" },
      { type: "paragraph", text: "The default limit allows this process to manage up to 20 live-streaming chats at the same time." },
      { type: "paragraph", text: "The service refuses to start if the streaming limit is greater than the number of outbound provider connections it can support. Advertising 50 streaming slots while the provider connection pool can support only 20 would create capacity that cannot actually be used." },

      { type: "heading", text: "The limit belongs to one process" },
      { type: "paragraph", text: "This counter exists only in the memory of the process handling the request. PostgreSQL and Redis are not involved." },
      { type: "paragraph", text: "Another running process has its own separate counter. One process may have all 20 slots occupied while another process still has room." },
      { type: "paragraph", text: "The supplied container starts one application process, so the process limit is also the container limit in the current deployment. If several application workers are started inside one container, each worker receives its own separate limit." },
      { type: "paragraph", text: "For example, four workers with a limit of 20 could admit up to 80 streaming chats in that container." },

      { type: "heading", text: "Why the request is not placed in a queue" },
      { type: "paragraph", text: "When every slot is occupied, the process returns **503 Service Unavailable** immediately with a short retry suggestion." },
      { type: "paragraph", text: "It does not keep the request waiting in a queue. A waiting streaming request would continue occupying a client connection while asking the process to wait for a resource that is already exhausted." },
      { type: "paragraph", text: "A retry may reach another process that still has an available slot." },

      { type: "heading", text: "Which requests use a slot" },
      { type: "paragraph", text: "Only live-streaming chat requests pass through this check." },
      { type: "paragraph", text: "A normal chat response that is returned all at once, an embedding request, and a reranking request do not claim a streaming slot. They continue directly to the shared token-capacity check." },
    ],
    paragraphs: [
      "Picture several people watching answers type out live from the same process. Each one holds a connection open until their answer finishes. Too many open at once and that process runs out of memory and sockets — so it counts them, and refuses a new one when it is full.",
      "Twenty at once is the default. The process refuses to even start if that number is set higher than the outbound HTTP connection pool can support, because a stream that cannot get a connection is not capacity at all.",
      "Scope, exactly: the count is a plain number in this Python process's memory, guarded by an asyncio lock — no Redis, no database, nothing shared. The code calls it \"intentionally process-local\", and the setting behind it is named stream_max_concurrent_per_worker. Every other running instance keeps its own separate count, so a refusal here says nothing about the fleet and a retry may simply land somewhere with room. Contrast 8b directly below: that one is shared by everybody.",
      "One caveat worth knowing, because \"process\" and \"container\" are not automatically the same thing: the shipped Dockerfile starts uvicorn with no --workers flag, so as built there is exactly one such process per container, and process-local and container-local mean the same thing here. Add --workers N and that stops being true — you would get N independent counters inside one container, each allowing its own 20.",
      "It never queues. Waiting would mean holding an open socket to say \"please wait\", which spends the very resource that has run out.",
    ],
  },
  DENYSLOT: {
    title: "This process has no room for another live-streaming response",
    sub: "503 · This process has reached its live-streaming limit. The response includes a short retry suggestion.",
    blocks: [
      { type: "paragraph", text: "This process is already handling its configured maximum number of live-streaming chat responses." },
      { type: "paragraph", text: "For example, when the limit is 20 and 20 responses are still open, the next streaming request cannot begin here. The service returns **503 Service Unavailable** immediately." },
      { type: "paragraph", text: "The response includes a `Retry-After` hint. The default is one second, although that value can be configured." },
      { type: "paragraph", text: "This refusal applies only to live-streaming chat. A normal chat response returned all at once, embedding, and reranking do not use a streaming slot and cannot reach this box." },
      { type: "paragraph", text: "The slot check happens before shared token capacity is reserved. If this process has a free streaming slot but the Token Manager later refuses the request, cannot be reached, or reports an integration problem, the temporarily claimed slot is returned before the error is sent to the caller." },
      { type: "paragraph", text: "That means a failed request does not leave the process believing it has one more live stream open than it actually does." },
      { type: "paragraph", text: "The request is not queued, no provider credential is read, and no AI provider is contacted. A retry may succeed if an existing stream finishes or if the next request reaches another process with available streaming capacity." },
    ],
    paragraphs: [
      "The refusal carries a Retry-After hint, one second by default.",
      "Note the ordering: this check runs BEFORE the token quota in 8b. If 8b then refuses, the streaming slot claimed here is handed straight back, so a refusal never leaves this count stuck high.",
    ],
  },
  RESV: {
    title: "8b · Is there enough shared token capacity for this request?",
    sub: "Every application instance shares this provider-capacity decision through the separate Token Manager service.",
    blocks: [
      { type: "paragraph", text: "A streaming slot in Step 8a protects one application process from becoming too busy. This step protects something different: the amount of AI-provider capacity that every running application instance is trying to use together." },
      { type: "paragraph", text: "AI providers measure usage in tokens. A token is a small piece of text. Both the text sent to the provider and the text returned by the provider consume tokens." },
      { type: "paragraph", text: "Before calling the AI provider, the service asks whether enough shared token capacity can be reserved for this request." },

      { type: "heading", text: "Why a separate service makes this decision" },
      { type: "paragraph", text: "Many copies of this application may be running at the same time. One copy cannot know how many tokens every other copy has already reserved." },
      { type: "paragraph", text: "For that reason, the request is sent over HTTP to a separate Token Manager service. That service keeps the shared record of reserved capacity and decides whether another request can begin." },
      { type: "paragraph", text: "Step 8a and Step 8b can therefore produce different answers. A process may have plenty of room for another live-streaming response while the shared token capacity is already exhausted. The opposite can also happen: shared token capacity may be available while this particular process has no free streaming slot." },

      { type: "heading", text: "How much capacity is reserved" },
      { type: "paragraph", text: "The service estimates the tokens in the request content, then adds the largest response the approved model is allowed to produce." },
      { type: "code", language: "text", text: "estimated tokens in the chat messages\n+\nmaximum response tokens from the fixed execution plan\n=\ntoken capacity requested" },
      { type: "paragraph", text: "Embedding and reranking requests do not generate a written response, so they reserve only the estimated tokens in their input." },
      { type: "paragraph", text: "Using the same maximum response length for both the reservation and the later provider call prevents a mismatch. The service does not reserve capacity for a small response and then permit the provider to generate a much larger one." },

      { type: "heading", text: "Which capacity pool is checked" },
      { type: "paragraph", text: "The Token Manager reads the selected deployment's token-capacity limit from its own deployment record." },
      { type: "paragraph", text: "Its fast shared counter is more broadly scoped. The counter is identified by:" },
      { type: "code", language: "text", text: "model name + provider endpoint address" },
      { type: "paragraph", text: "It does not include the tenant ID or deployment ID." },
      { type: "paragraph", text: "For example, if two tenants use the same model through the same provider endpoint, their requests contribute to the same fast shared counter. Their deployments may be different, but the current Redis counter treats them as users of the same provider capacity pool." },
      { type: "paragraph", text: "That is the current implementation. It is not a separate token pool for every tenant or deployment." },

      { type: "heading", text: "The endpoint safety check" },
      { type: "paragraph", text: "The fixed execution plan already contains the endpoint approved for this request." },
      { type: "paragraph", text: "The Token Manager selects a matching active deployment in its own records and returns the endpoint for which it reserved capacity. Before continuing, the service compares that returned endpoint with the endpoint in the approved plan." },
      { type: "paragraph", text: "If they differ, the request stops with **502 Bad Gateway**. The service refuses to reserve capacity for one destination and send the AI request to another." },

      { type: "heading", text: "What happens when capacity is available" },
      { type: "paragraph", text: "The Token Manager returns a reservation. The AI provider call may then begin." },
      { type: "paragraph", text: "When the request finishes, fails, or the caller disconnects, the service reports the final outcome and actual token usage. The reservation can then be settled using what was actually consumed." },

      { type: "heading", text: "When this step stops the request" },
      { type: "paragraph", text: "If the Token Manager says capacity is unavailable, or returns a waiting reservation instead of an approved one, the caller receives **429 Too Many Requests**. The AI provider is not contacted." },
      { type: "paragraph", text: "If the Token Manager cannot be reached, times out, or reports one of its own server failures, the caller receives **503 Service Unavailable**. The service does not assume capacity is available when it cannot confirm it." },
      { type: "paragraph", text: "If the Token Manager rejects this service's credentials, returns invalid response data, or returns an endpoint different from the approved endpoint, the caller receives **502 Bad Gateway**. Those outcomes mean the two services cannot safely trust their integration." },

      { type: "heading", text: "What happens next" },
      { type: "paragraph", text: "A successful reservation does not mean the AI provider has been called yet. It means the service has reserved room to make that call." },
      { type: "paragraph", text: "The next step retrieves the credential from Vault. Only then does the service contact the approved AI provider." },
    ],
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
    title: "Shared token capacity is currently unavailable",
    sub: "429 · The Token Manager responded but could not approve capacity for this request.",
    blocks: [
      { type: "paragraph", text: "The Token Manager responded, but it could not approve enough shared token capacity for this request." },
      { type: "paragraph", text: "This is different from a Token Manager outage. The service received a valid answer: the request cannot begin with the capacity available right now." },
      { type: "paragraph", text: "For a chat request, the service asks for enough capacity to cover the input text and the largest response the approved model may generate. If that amount does not fit in the shared capacity pool, the request stops with **429 Too Many Requests**." },
      { type: "paragraph", text: "The same response is also used when the Token Manager returns a waiting result rather than an approved reservation. The application does not wait for capacity to become available and does not send the AI request anyway." },
      { type: "paragraph", text: "No active capacity reservation is passed to the provider call. The service does not read a credential from Vault and does not contact the AI provider." },
      { type: "paragraph", text: "If this was a live-streaming chat, Step 8a may already have temporarily claimed a streaming slot. That slot is returned before the 429 response is sent, so this refused request does not reduce the number of slots available for other live-streaming responses." },
      { type: "paragraph", text: "The Token Manager may include a `Retry-After` hint. Retrying can help only after capacity has been released by another completed, failed, or expired request." },
      { type: "paragraph", text: "A **503** means something different: the Token Manager could not be reached or was unavailable. A **429** means it responded and declined this request." },
    ],
    paragraphs: [
      "This is a real refusal, not a sign that Token Manager is unwell. Nothing stays reserved for this call.",
      "The streaming slot claimed back in 8a is freed on the way out.",
    ],
  },
  RESVDOWN: {
    title: "Shared token capacity could not be confirmed",
    sub: "503 · The service could not obtain a usable capacity decision from the Token Manager.",
    blocks: [
      { type: "paragraph", text: "The service could not obtain a usable decision from the Token Manager." },
      { type: "paragraph", text: "This can happen when the Token Manager cannot be reached, takes too long to respond, or returns one of its own server errors. The service does not know whether enough shared token capacity is available, so it stops the request with **503 Service Unavailable**." },
      { type: "paragraph", text: "This is different from the **429** in the previous box." },
      { type: "paragraph", text: "A 429 means the Token Manager responded and declined the request because capacity was unavailable. A 503 means the service could not safely obtain a capacity decision at all." },
      { type: "paragraph", text: "The service does not continue on the assumption that capacity is available. It does not read a credential from Vault and does not contact the AI provider." },
      { type: "paragraph", text: "If this was a live-streaming chat, Step 8a may already have temporarily claimed a streaming slot. That local slot is returned before the 503 response is sent, so the failed Token Manager request does not occupy streaming capacity in this process." },

      { type: "heading", text: "An important timeout detail" },
      { type: "paragraph", text: "When the request times out, the service cannot know whether the Token Manager received it just before the connection failed." },
      { type: "paragraph", text: "The Token Manager may have received the request and temporarily reserved capacity, even though this service never received the approval response. In that case, the reservation is eventually cleared by the Token Manager's expiration process." },
      { type: "paragraph", text: "The AI provider is still never called because this service did not receive an approved reservation. A retry is safe from causing two provider calls, but it may temporarily find less shared capacity until any uncertain reservation expires." },
    ],
    paragraphs: [
      "The difference from the 429 above is worth holding on to: there, the shared quota is genuinely spent and retrying is pointless until it frees up. Here, we simply never heard back, and the quota may be entirely untouched.",
      "We never got a yes, so nothing is left reserved on Token Manager's side. The streaming slot claimed in 8a is still freed in this process.",
    ],
  },

  CRED: {
    title: "9 · Fetch the API key from Vault",
    sub: "The execution plan carries only the credential location. Vault provides the actual API key only when the provider needs it.",
    blocks: [
      { type: "paragraph", text: "Up to this point, the request has carried only a credential location, such as `secret/acme/openai/customer-support`. It has never carried the actual API key." },
      { type: "paragraph", text: "That distinction is deliberate. The execution plan can safely say where the key lives without exposing the key to PostgreSQL records, logs, browser responses, or earlier stages of the request." },

      { type: "heading", text: "When Vault is contacted" },
      { type: "paragraph", text: "Vault is contacted only after the tenant, entitlement, provider, model, streaming capacity, and shared token capacity have all been approved." },
      { type: "paragraph", text: "This order matters. There is no reason to retrieve a sensitive credential for a request that will later be rejected because the caller lacks access, the model cannot perform the requested operation, or capacity is unavailable." },
      { type: "paragraph", text: "Vault is also not necessarily contacted for every request. Once a provider instance has been created, the service caches that provider instance with its credential for a limited time. The default lifetime is five minutes." },
      { type: "paragraph", text: "A later request using the same saved route can reuse the cached provider instance during that period. When the cached instance expires, the service reads Vault again before rebuilding it." },

      { type: "heading", text: "What Vault returns" },
      { type: "paragraph", text: "The service sends the credential location to Vault using a read-only service identity." },
      { type: "paragraph", text: "Vault returns a secret record containing an `api_key` value. The service validates that the value exists and is not empty, then keeps it in a protected in-memory form while creating the provider connection." },
      { type: "paragraph", text: "The API key is never added to the execution plan and is never returned to the client." },
      { type: "paragraph", text: "The service uses a different Vault identity for credential management. The identity used for inference can read credentials but cannot create, replace, or delete them." },

      { type: "heading", text: "Some providers do not use an API key" },
      { type: "paragraph", text: "Not every provider needs an explicit API key." },
      { type: "paragraph", text: "Providers that use cloud identity signing, such as AWS request signing, or providers configured with no authentication, skip the Vault read completely. They continue with the authentication method defined for that provider." },
      { type: "paragraph", text: "This card describes the Vault-backed credential path shown in this flow." },

      { type: "heading", text: "If Vault cannot be used" },
      { type: "paragraph", text: "Vault requests have bounded timeouts and retry transient failures up to three times by default." },
      { type: "paragraph", text: "If Vault cannot be reached, takes too long to respond, or returns a server error, the request stops with **503 Service Unavailable**. The service does not fall back to another credential, another provider, or an environment variable." },
      { type: "paragraph", text: "Some Vault failures are currently less cleanly handled:" },
      { type: "list", items: [
        { text: "A missing credential location currently becomes an internal **500**." },
        { text: "A Vault permission denial currently becomes an internal **500**." },
        { text: "A Vault response that does not contain a usable `api_key` currently becomes an internal **500**." },
        { text: "A malformed Vault response is treated as a Vault availability failure and returns **503**." },
      ] },
      { type: "paragraph", text: "These are configuration or integration problems, not caller mistakes. The API key itself is never included in the error response." },

      { type: "heading", text: "What happens next" },
      { type: "paragraph", text: "Once the provider has the credential it needs, the service can make the approved AI-provider call." },
      { type: "paragraph", text: "The provider receives only the provider, model, endpoint, request settings, and credential needed for that one route. It does not reconsider authorization or select another deployment." },
    ],
    paragraphs: [
      "The Execution Plan names a secret location, not the secret. Only now is the real value fetched — and only for providers that need one. This identity can only read; a separate admin identity is the only writer.",
    ],
  },
  DENYVAULT: {
    title: "Vault could not provide the credential",
    sub: "503 · The service could not retrieve the approved API key, so the AI-provider call cannot start.",
    blocks: [
      { type: "paragraph", text: "The request passed access checks, route resolution, and capacity checks. The service then needed the API key required to contact the selected AI provider, but Vault could not provide it." },
      { type: "paragraph", text: "This happens when Vault cannot be reached, takes too long to respond, or returns a server-side failure. The service retries temporary Vault failures up to three times by default before returning **503 Service Unavailable**." },
      { type: "paragraph", text: "The service does not guess a credential, use a different provider, or fall back to another secret location. Without the approved credential, it cannot safely start the AI-provider call." },

      { type: "heading", text: "What happens to capacity already reserved" },
      { type: "paragraph", text: "By the time the service reaches Vault, shared token capacity has already been reserved." },
      { type: "paragraph", text: "The service marks that reservation as failed and attempts to release it through the Token Manager. This prevents a Vault outage from unnecessarily holding shared token capacity for requests that never reached an AI provider." },
      { type: "paragraph", text: "If this was a live-streaming chat, the process may also have claimed a streaming slot earlier. That local slot is returned even if the token-reporting call has its own problem." },
      { type: "paragraph", text: "No AI-provider request is sent." },

      { type: "heading", text: "What this response does and does not mean" },
      { type: "paragraph", text: "A 503 means Vault could not provide a usable response at this time. Retrying later may help." },
      { type: "paragraph", text: "It does not mean that the credential location is definitely wrong." },
      { type: "paragraph", text: "A missing credential location, a denied Vault read, or a Vault record without a usable API key currently produces an internal 500 instead. Those are configuration problems that retrying will not correct." },
    ],
    paragraphs: [
      "Without a credential the outbound call cannot start. Cleanup still runs so capacity is not left held.",
    ],
  },
  CALLBOX: {
    title: "10 · Send the request to the selected AI provider",
    sub: "The first step that sends the approved request outside this service to the selected AI provider.",
    blocks: [
      { type: "paragraph", text: "Everything before this point decided whether the request is allowed and prepared the exact route it must use. This is the first step that sends information outside this service to an AI provider such as OpenAI, Anthropic, Azure OpenAI, Bedrock, or a locally hosted model." },
      { type: "paragraph", text: "The service sends the request only to the provider, model, endpoint, and credential already selected by the earlier steps. It does not search for another deployment, substitute a cheaper model, or fall back to another provider if this call fails." },

      { type: "heading", text: "One request format inside, different formats outside" },
      { type: "paragraph", text: "The application has one common request format for chat, embedding, and reranking. Each provider has its own API format, authentication headers, endpoint paths, and response shape." },
      { type: "paragraph", text: "At this boundary, the service translates the common request into the selected provider's format." },
      { type: "paragraph", text: "For example, the service may send a chat request to OpenAI's chat endpoint, send the same conversation in Anthropic's messages format, or send a cloud-provider request using its own signing method. The caller does not need to know those differences." },
      { type: "paragraph", text: "When the provider responds, the service translates its provider-specific response back into the application's common response format." },

      { type: "heading", text: "What is sent to the provider" },
      { type: "paragraph", text: "The outbound request contains only the information needed for the approved AI call:" },
      { type: "list", items: [
        { text: "The selected model." },
        { text: "The client's input, such as chat messages, embedding text, or a reranking query and documents." },
        { text: "The approved provider endpoint." },
        { text: "The credential required by that provider." },
        { text: "Request settings such as temperature, maximum output length, stop sequences, and sampling settings." },
      ] },
      { type: "paragraph", text: "The provider receives no PostgreSQL connection details, no tenant membership data, no sign-in password, and no unrelated credentials." },

      { type: "heading", text: "An important current behavior" },
      { type: "paragraph", text: "The fixed execution plan supplies default temperature and maximum-output settings." },
      { type: "paragraph", text: "However, when a chat request includes its own temperature or maximum-output value, the provider adapters currently use the caller's value instead of the default from the plan." },
      { type: "paragraph", text: "The caller-provided maximum output is not currently capped against the plan's maximum output value. Meanwhile, the Token Manager reserves capacity using the plan's maximum output value." },
      { type: "paragraph", text: "This creates a known mismatch: a caller can ask the provider for a larger response than the shared token-capacity step reserved. The service should eventually enforce the plan's maximum as an upper limit, not merely use it as a default." },

      { type: "heading", text: "Protection when a provider is unhealthy" },
      { type: "paragraph", text: "Each application process keeps a separate circuit breaker for each provider." },
      { type: "paragraph", text: "A circuit breaker is like an electrical fuse. When a provider has repeatedly failed, the service temporarily stops sending it more requests. This prevents many slow failures from consuming connections and making the rest of the application less responsive." },
      { type: "paragraph", text: "By default, five consecutive provider failures open the circuit for 60 seconds. While the circuit is open, the service returns **503 Service Unavailable** with a retry hint and does not contact that provider." },
      { type: "paragraph", text: "A provider problem in one application process does not automatically open the circuit in every other process." },

      { type: "heading", text: "If the provider rejects or cannot complete the request" },
      { type: "paragraph", text: "The service translates provider failures into stable responses:" },
      { type: "list", items: [
        { label: "422", text: "A request the provider rejects as invalid, including an unavailable model." },
        { label: "429", text: "A provider rate limit." },
        { label: "504 Gateway Timeout", text: "A provider timeout." },
        { label: "503 Service Unavailable", text: "A provider outage or an open circuit." },
        { label: "502 Bad Gateway", text: "A rejected provider credential or an unexpected provider response." },
      ] },
      { type: "paragraph", text: "The service does not automatically retry the AI request or choose another provider. Repeating a generation request could create duplicate output and duplicate cost, so a retry must be a deliberate caller decision." },

      { type: "heading", text: "What happens after the call" },
      { type: "paragraph", text: "For a normal request, the complete provider response moves to the response step." },
      { type: "paragraph", text: "For a live-streaming chat, pieces of the response begin moving to the caller as the provider produces them. Once that streaming response begins, later provider failures must be reported inside the stream because the HTTP success response has already started." },
      { type: "paragraph", text: "Whether the call succeeds, fails, or the caller disconnects, the later cleanup step records the outcome and settles the token reservation." },
    ],
    paragraphs: [
      "This is the step that leaves the company's infrastructure. Every vendor dialect is translated behind one shared contract. A circuit breaker refuses further calls to a repeatedly failing provider for a cooling-off window.",
    ],
  },

  SEND: {
    title: "11 · Send the response to the client",
    sub: "A normal request returns one JSON response. A live-streaming chat uses Server-Sent Events to deliver pieces as they arrive.",
    blocks: [
      { type: "paragraph", text: "The provider has produced a response, and the service now delivers it to the caller in one of two formats." },
      { type: "paragraph", text: "A normal chat request, embedding request, or reranking request receives one complete JSON response. The caller waits until the provider has finished, then receives the entire result at once." },
      { type: "paragraph", text: "A live-streaming chat uses **SSE**, short for **Server-Sent Events**. SSE is still an HTTP response, but the service keeps that response open and sends small named events as new pieces of the provider's response become available." },

      { type: "heading", text: "What the client receives during a live stream" },
      { type: "paragraph", text: "The response has the content type `text/event-stream`." },
      { type: "paragraph", text: "As the provider generates text, the service sends `text_delta` events. Each event contains the newly available piece of text, along with:" },
      { type: "list", items: [
        { text: "The conversation identifier." },
        { text: "A sequence number that increases for each event." },
        { text: "The request identifier, when available." },
      ] },
      { type: "paragraph", text: "Some events carry stream metadata instead of text, such as usage or completion information." },
      { type: "paragraph", text: "When the provider is quiet for a while, the service sends an SSE heartbeat comment. A heartbeat is not new AI text. It simply keeps browsers, proxies, and load balancers aware that the connection is still alive. The default heartbeat interval is 15 seconds." },
      { type: "paragraph", text: "When the response finishes normally, the service sends one final `complete` event with a status of `completed`." },

      { type: "heading", text: "Why early failures look different" },
      { type: "paragraph", text: "The service prepares the route, checks capacity, and obtains the provider credential before it starts the SSE response." },
      { type: "paragraph", text: "If one of those earlier steps fails, the service can still return a normal HTTP error such as 403, 429, 503, or 504. No response has begun yet." },
      { type: "paragraph", text: "Once the SSE response begins, the HTTP success status has already been sent to the client. A later provider failure cannot replace that status with a new HTTP error response." },
      { type: "paragraph", text: "Instead, if the client is still connected, the service sends an SSE `error` event with a safe error code, then sends the final `complete` event with a status of `failed`." },
      { type: "paragraph", text: "If the client disconnects, it cannot receive those final events. The service still performs cleanup in the next step." },

      { type: "heading", text: "What happens next" },
      { type: "paragraph", text: "After a normal JSON response or the end of an SSE stream, the service closes provider work, settles token usage, and returns any streaming capacity that was reserved." },
    ],
    paragraphs: [
      "Streaming: pieces reach the caller as the provider produces them. Non-streaming: one complete reply. Everything upstream was resolved first so early failures could still be clean HTTP errors.",
      "Once headers and the first chunk are sent, a provider failure cannot change the status code — that is the mid-stream box below.",
    ],
  },
  MIDSTREAM: {
    title: "The provider failed while sending a live response",
    sub: "The client already received some live text, but the provider could not finish the response.",
    blocks: [
      { type: "paragraph", text: "A live-streaming response sends text to the client little by little." },
      { type: "paragraph", text: "For example, instead of waiting for the whole AI response, the client may receive:" },
      { type: "code", language: "text", text: "Hello\nHello, how can\nHello, how can I help you today?" },
      { type: "paragraph", text: "A mid-stream failure means something went wrong after some of that text had already been sent." },
      { type: "paragraph", text: "The client may have received part of the response, but the provider could not finish it." },

      { type: "heading", text: "Why the service cannot send a new HTTP error" },
      { type: "paragraph", text: "When streaming begins, the service has already told the client, \"I am starting a successful response.\"" },
      { type: "paragraph", text: "That successful HTTP response has already started. The service cannot later replace it with a normal error page or change it to a 503 response." },
      { type: "paragraph", text: "Instead, the service uses the open SSE connection to tell the client that the live response ended early." },

      { type: "heading", text: "What the client receives" },
      { type: "paragraph", text: "If the client is still connected, it receives:" },
      { type: "code", language: "text", text: "1. The text already received.\n2. An error event saying that the stream ended unexpectedly.\n3. A final complete event saying that the stream failed." },
      { type: "paragraph", text: "The text already received is not removed. However, it may be incomplete." },
      { type: "paragraph", text: "For example, the client may have received:" },
      { type: "code", language: "text", text: "The refund will be processed within" },
      { type: "paragraph", text: "but never receive the rest of the sentence." },
      { type: "paragraph", text: "The application showing this response can decide whether to display the partial text, hide it, or offer a Retry button." },

      { type: "heading", text: "What happens inside the service" },
      { type: "paragraph", text: "The service stops the unfinished provider response, records that the request failed, reports any known token usage, and returns the streaming capacity that this response was using." },
      { type: "paragraph", text: "If the client disconnected before the failure happened, it cannot receive the error message or final event. The service still performs the same cleanup." },

      { type: "heading", text: "Why the service does not retry automatically" },
      { type: "paragraph", text: "An automatic retry could create two different responses for the same request." },
      { type: "paragraph", text: "For example, the client may already have received part of one response, then receive a different second response after the retry. The provider may also charge for both attempts." },
      { type: "paragraph", text: "For that reason, the service stops the failed stream and lets the caller decide whether to send a new request." },

      { type: "heading", text: "When a normal error response is still possible" },
      { type: "paragraph", text: "This box applies only after live text has started reaching the client." },
      { type: "paragraph", text: "If the provider fails before streaming begins, the service can still return a normal HTTP error response such as 503 or 504." },
    ],
    paragraphs: [
      "HTTP status stays 200 — headers were already sent. If the client is still connected it receives an SSE error event, then complete with status failed. If it disconnected, it gets no final event; cleanup still records disconnected.",
      "Usage reconciliation reports the largest cumulative LLM-token counts observed before failure. If the provider had not emitted usage yet, exact partial usage is unknown and the gateway passes none — it cannot prove what the provider billed.",
    ],
  },
  DONE: {
    title: "12 · Close the request, settle token usage, and release capacity",
    sub: "Close provider work, report the final outcome, and return any local streaming capacity held for this request.",
    blocks: [
      { type: "paragraph", text: "Every AI request eventually reaches an ending point. It may complete successfully, fail while the provider is working, be cancelled, or lose its client connection." },
      { type: "paragraph", text: "This step closes the work opened earlier so that no provider stream, token reservation, or local streaming capacity remains unnecessarily held." },

      { type: "heading", text: "For a live-streaming response" },
      { type: "paragraph", text: "Step 11 already began the SSE response to the client." },
      { type: "paragraph", text: "When that stream ends, fails, or the client disconnects, the service closes the provider stream. This stops unnecessary provider work when nobody can receive the remaining response." },
      { type: "paragraph", text: "The service gives provider-stream cleanup a limited amount of time. If the provider does not close in time, the service records the problem and continues with the remaining cleanup. A broken provider cleanup must not permanently consume capacity in this application process." },
      { type: "paragraph", text: "The cleanup path is protected so that it runs once even when several events happen close together, such as a provider failure followed by an SSE connection closing." },

      { type: "heading", text: "Returning the streaming slot" },
      { type: "paragraph", text: "Only live-streaming chat claimed a streaming slot in Step 8a." },
      { type: "paragraph", text: "After the service has attempted to report the outcome, it returns that temporary slot to this process. The count of active live streams goes down by one." },
      { type: "paragraph", text: "The slot return is guaranteed to run even when token reporting fails. An accounting problem must not make the process appear permanently full." },
      { type: "paragraph", text: "Normal chat, embedding, and reranking requests never claimed a streaming slot, so they have no slot to return." },

      { type: "heading", text: "Settling token usage" },
      { type: "paragraph", text: "Before the provider call, the Token Manager received an estimate of the token capacity needed for this request." },
      { type: "paragraph", text: "After the request ends, the service reports the final outcome:" },
      { type: "list", items: [
        { label: "completed", text: "The provider finished normally." },
        { label: "failed", text: "The provider returned an error." },
        { label: "cancelled", text: "The request was cancelled." },
        { label: "disconnected", text: "The streaming client went away." },
      ] },
      { type: "paragraph", text: "When the provider reports token usage, the service also sends the actual input and output totals to the Token Manager." },
      { type: "paragraph", text: "A streaming provider can report cumulative usage more than once. The service keeps the largest input and output totals it observed. It does not add every update together because each later total may already include the earlier tokens." },
      { type: "paragraph", text: "If a provider never reports usage, the service records that the actual usage is unknown instead of inventing a number." },

      { type: "heading", text: "The cleanup order for a live stream" },
      { type: "code", language: "text", text: "1. Close the provider stream.\n2. Report the final outcome and known token usage.\n3. Return this process's streaming slot." },
      { type: "paragraph", text: "The third step runs even if the second one encounters an error." },
      { type: "paragraph", text: "For a successful non-streaming request, token reporting is part of completing the request. If that reporting fails, the service does not claim that the request completed cleanly." },

      { type: "heading", text: "Why this matters" },
      { type: "paragraph", text: "The shared token-capacity step reserved an estimate before the provider call. This step settles that reservation after the real outcome is known." },
      { type: "paragraph", text: "It prevents completed, failed, cancelled, and disconnected requests from leaving unnecessary provider work, token reservations, or live-streaming capacity behind." },
    ],
    paragraphs: [
      "If this was a streaming chat, this process's open-streaming-slot count goes down by one. Token Manager is told the real input and output sizes when known, and the reservation closes as completed, failed, cancelled, or disconnected.",
      "We reserved an estimate before the call; we settle the books after — whether the call went well or not.",
    ],
  },
  GAP: {
    title: "What the system does not verify yet",
    sub: "The system records operational activity, but it does not yet reconcile provider charges or evaluate whether AI responses are correct and useful.",
    blocks: [
      { type: "paragraph", text: "The service does measure some important things." },
      { type: "paragraph", text: "Before an AI call, it estimates token usage and reserves shared capacity. After the call, it records the token totals reported by the provider when those totals are available." },
      { type: "paragraph", text: "However, recording a provider's reported usage is not the same as independently proving what the provider later charged." },

      { type: "heading", text: "Provider charges are not reconciled" },
      { type: "paragraph", text: "The system does not currently compare its recorded token usage with a provider invoice, billing export, or usage dashboard." },
      { type: "paragraph", text: "For example, the service may record that a request used 1,200 tokens because the provider reported 1,200 tokens in its response. At the end of the month, there is no automated job that checks whether the provider's bill also recorded 1,200 tokens for that request or whether the total across all requests matches the invoice." },
      { type: "paragraph", text: "This means the system can manage its own capacity estimates, but it cannot yet automatically detect billing differences caused by missing provider usage, delayed reports, partial failures, provider-side counting differences, or an integration defect." },

      { type: "heading", text: "Response quality is not evaluated" },
      { type: "paragraph", text: "The system also does not decide whether an AI response was useful, correct, safe, complete, or relevant to the caller's question." },
      { type: "paragraph", text: "A response can arrive quickly, use the expected number of tokens, and contain no technical error while still being wrong." },
      { type: "paragraph", text: "For example, an AI model might confidently say that a refund takes three days when the real policy says ten days. The request would pass every step in this flow because the system currently checks whether the response was delivered, not whether its content was true." },

      { type: "heading", text: "Why this matters" },
      { type: "paragraph", text: "Operational success and response quality are different things." },
      { type: "code", language: "text", text: "Operational success:\nThe request was allowed, capacity was available, the provider responded,\nand cleanup completed.\n\nResponse quality:\nThe content was accurate, useful, safe, and appropriate for the task." },
      { type: "paragraph", text: "The first is measured by the current system. The second is not." },

      { type: "heading", text: "Possible future improvements" },
      { type: "paragraph", text: "Provider billing can be reconciled by periodically comparing recorded request usage with provider billing exports or usage APIs." },
      { type: "paragraph", text: "Response quality can be monitored by sampling selected responses, collecting user feedback, and evaluating results against task-specific expectations." },
      { type: "paragraph", text: "Neither capability exists in the current implementation. This card is a reminder that a technically successful AI request is not automatically a trustworthy or valuable response." },
    ],
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

function renderBubbleBlocks(blocks) {
  return blocks.map((block) => {
    if (block.type === "paragraph") return `<p>${escapeHtml(block.text)}</p>`;
    if (block.type === "heading") return `<h4>${escapeHtml(block.text)}</h4>`;
    if (block.type === "code") return `<pre><code>${escapeHtml(block.text)}</code></pre>`;
    const tag = block.type === "ordered" ? "ol" : "ul";
    const items = (block.items || []).map((item) => {
      if (typeof item === "string") return `<li>${escapeHtml(item)}</li>`;
      const separator = item.separator === undefined ? ":" : item.separator;
      return `<li><strong>${escapeHtml(item.label)}${escapeHtml(separator)}</strong> ${escapeHtml(item.text)}</li>`;
    }).join("");
    return `<${tag}>${items}</${tag}>`;
  }).join("");
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

  const body = detail.blocks?.length
    ? `<div class="full-flow-bubble-copy">${renderBubbleBlocks(detail.blocks)}</div>`
    : detail.paragraphs?.length
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
