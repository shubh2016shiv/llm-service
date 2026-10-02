/* Route Resolution LLD, traced from route_resolution.py and route_builder.py. */
import React from "react";
import { createRoot } from "react-dom/client";
import { ReactFlow, Background, Controls, Handle, Position, MarkerType } from "@xyflow/react";

const h = React.createElement;
const HANDLES = [
  h(Handle, { type: "target", position: Position.Top, id: "top", className: "rf-handle", key: "top" }),
  h(Handle, { type: "source", position: Position.Bottom, id: "bottom", className: "rf-handle", key: "bottom" }),
  h(Handle, { type: "source", position: Position.Right, id: "right", className: "rf-handle", key: "right" }),
  h(Handle, { type: "target", position: Position.Left, id: "left", className: "rf-handle", key: "left" }),
];

function StepNode({ data }) {
  return h("div", {
    className: `rf-node rf-node--${data.kind}`,
    role: "button", tabIndex: 0,
    "aria-label": `${data.title}. Press Enter for details.`,
    onClick: (event) => data.onOpen(event.currentTarget, data.id, true),
    onKeyDown: (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        data.onOpen(event.currentTarget, data.id, true);
      }
    },
  }, ...HANDLES,
  h("div", { className: "rf-node-main" },
    h("span", { className: "lld-step-number", "aria-hidden": "true" }, data.number),
    h("div", { className: "rf-node-copy" },
      h("p", { className: "rf-node-title" }, data.title),
      h("p", { className: "rf-node-sub" }, data.sub)
    )
  ));
}

function LaneNode({ data }) {
  return h("div", { className: `rf-lane rf-lane--${data.kind}` },
    h("span", { className: "rf-lane-label" },
      h("strong", null, data.title),
      h("span", { className: "rf-lane-annotation" }, ` — ${data.hint}`)
    )
  );
}

const LANES = [
  { id: "LIVE", x: 20, y: 20, w: 1170, h: 610, kind: "live", title: "1 · Read current state", hint: "PostgreSQL; exact approved grant" },
  { id: "POLICY", x: 20, y: 645, w: 1170, h: 475, kind: "policy", title: "2 · Apply policy and catalog", hint: "no provider or model substitution" },
  { id: "PLAN", x: 20, y: 1135, w: 1170, h: 365, kind: "plan", title: "3 · Freeze the execution plan", hint: "settings and identity for downstream work" },
];

const STEPS = [
  { id: "INPUT", x: 250, y: 78, number: "1", kind: "source", title: "Receive the approved grant and operation", sub: "Tenant, user, deployment, entitlement ID; chat, embed or rerank" },
  { id: "TENANT_READ", x: 250, y: 188, number: "2", kind: "gather", title: "Re-read tenant policy from PostgreSQL", sub: "Map the current SQL row into a validated TenantConfig" },
  { id: "TENANT_GATE", x: 250, y: 298, number: "3", kind: "gather", title: "Check tenant status", sub: "Only active or trial tenants continue" },
  { id: "ENTITLE_READ", x: 250, y: 408, number: "4", kind: "gather", title: "Re-read the exact entitlement", sub: "SQL binds tenant, user, deployment key and approved ID" },
  { id: "ENTITLE_GATE", x: 250, y: 518, number: "5", kind: "gather", title: "Check active status and the same ID", sub: "Refuse missing, inactive or mismatched grants" },
  { id: "ALLOW", x: 250, y: 700, number: "6", kind: "enrich", title: "Apply the tenant provider allow-list", sub: "Check the provider named by this entitlement" },
  { id: "PROVIDER", x: 250, y: 810, number: "7", kind: "enrich", title: "Find the provider in the loaded catalog", sub: "In-memory lookup; no request-path YAML read" },
  { id: "MODEL", x: 250, y: 920, number: "8", kind: "enrich", title: "Find this provider's model", sub: "Use the exact model named by the entitlement" },
  { id: "CAPABILITY", x: 250, y: 1030, number: "9", kind: "enrich", title: "Check the requested operation", sub: "Model must support chat, embed or rerank as requested" },
  { id: "BUILD", x: 250, y: 1190, number: "10", kind: "assemble", title: "Resolve settings and quota identity", sub: "Endpoint and secret reference; defaults; entitlement ID as quota key" },
  { id: "FINGERPRINT", x: 250, y: 1300, number: "11", kind: "fingerprint", title: "Hash the full route inputs", sub: "SHA-256 of deployment key, entitlement, provider and model" },
  { id: "OUTPUT", x: 250, y: 1410, number: "12", kind: "output", title: "Return an immutable ResolvedRoute", sub: "Complete plan for reservation and provider execution" },
  { id: "TENANT_FAIL", x: 850, y: 298, number: "!", kind: "neutral", title: "Tenant unavailable", sub: "Missing: 404 · suspended: 403" },
  { id: "ENTITLE_FAIL", x: 850, y: 518, number: "!", kind: "neutral", title: "Exact grant unavailable", sub: "Inactive/missing: 403 · wrong ID: server error" },
  { id: "POLICY_FAIL", x: 850, y: 700, number: "!", kind: "neutral", title: "Provider forbidden", sub: "Tenant policy denies this provider: 403" },
  { id: "PROVIDER_FAIL", x: 850, y: 810, number: "!", kind: "neutral", title: "Provider catalog drift", sub: "Provider from database is absent: server error" },
  { id: "MODEL_FAIL", x: 850, y: 920, number: "!", kind: "neutral", title: "Model catalog drift", sub: "Model from database is absent: server error" },
  { id: "CAPABILITY_FAIL", x: 850, y: 1030, number: "!", kind: "neutral", title: "Operation unsupported", sub: "Selected model lacks this capability: 422" },
];

const NEXT = [
  ["INPUT", "TENANT_READ"], ["TENANT_READ", "TENANT_GATE"],
  ["TENANT_GATE", "ENTITLE_READ"], ["ENTITLE_READ", "ENTITLE_GATE"],
  ["ENTITLE_GATE", "ALLOW"], ["ALLOW", "PROVIDER"],
  ["PROVIDER", "MODEL"], ["MODEL", "CAPABILITY"],
  ["CAPABILITY", "BUILD"], ["BUILD", "FINGERPRINT"],
  ["FINGERPRINT", "OUTPUT"],
];
const STOPS = [
  ["TENANT_GATE", "TENANT_FAIL"], ["ENTITLE_GATE", "ENTITLE_FAIL"],
  ["ALLOW", "POLICY_FAIL"], ["PROVIDER", "PROVIDER_FAIL"],
  ["MODEL", "MODEL_FAIL"], ["CAPABILITY", "CAPABILITY_FAIL"],
];

function RoutingFlow() {
  const onOpen = React.useCallback((element, id, immediate) => {
    const detail = window.DIAGRAM_STAGES?.routing?.details?.[id];
    if (!detail) return;
    const target = element.closest(".react-flow__node") || element;
    if (immediate) window.showKnowledgeBubble?.(target, detail, { immediate: true, toggle: false });
    else window.scheduleKnowledgeBubble?.(target, detail);
  }, []);
  const onLeave = React.useCallback((element) => {
    window.hideKnowledgeBubble?.(element.closest(".react-flow__node") || element);
  }, []);
  /* Hover is wired through React Flow's own node handlers rather than the
     card div's, so the whole node wrapper is the hover target. */
  const handleNodeEnter = React.useCallback((event, node) => {
    if (node.type !== "step") return;
    onOpen(event.currentTarget || event.target, node.id, false);
  }, [onOpen]);
  const handleNodeLeave = React.useCallback((event, node) => {
    if (node.type !== "step") return;
    onLeave(event.currentTarget || event.target);
  }, [onLeave]);
  const nodes = React.useMemo(() => [
    ...LANES.map((lane) => ({ id: lane.id, type: "lane", position: { x: lane.x, y: lane.y },
      style: { width: lane.w, height: lane.h }, data: lane, draggable: false,
      selectable: false, focusable: false, zIndex: 0 })),
    ...STEPS.map((step) => ({ id: step.id, type: "step", position: { x: step.x, y: step.y },
      style: { width: step.number === "!" ? 310 : 530, height: 88 },
      data: { ...step, onOpen }, draggable: false, selectable: false, zIndex: 3 })),
  ], [onOpen]);
  const edges = React.useMemo(() => [
    ...NEXT.map(([source, target]) => ({ id: `${source}-${target}`, source, target,
      sourceHandle: "bottom", targetHandle: "top", type: "smoothstep" })),
    ...STOPS.map(([source, target]) => ({ id: `${source}-${target}`, source, target,
      sourceHandle: "right", targetHandle: "left", type: "smoothstep", label: "stop" })),
  ].map((edge) => ({ ...edge,
    markerEnd: { type: MarkerType.ArrowClosed, width: 15, height: 15, color: "#829188" },
    style: { stroke: "#829188", strokeWidth: 1.7 }, zIndex: 4,
    labelStyle: { fill: "#3f4a45", fontSize: 11, fontWeight: 700 },
    labelBgStyle: { fill: "#f4f1e9", fillOpacity: 0.96 }, labelBgPadding: [7, 4],
  })), []);
  return h(ReactFlow, { nodes, edges, nodeTypes: { step: StepNode, lane: LaneNode },
    fitView: true, fitViewOptions: { padding: 0.035 }, minZoom: 0.3, maxZoom: 1.5,
    nodesDraggable: false, nodesConnectable: false, elementsSelectable: false,
    zoomOnDoubleClick: false,
    onNodeMouseEnter: handleNodeEnter,
    onNodeMouseLeave: handleNodeLeave,
  }, h(Background, { gap: 22, size: 1, color: "rgba(16,22,20,.08)" }),
  h(Controls, { showInteractive: false }));
}

let root = null;
window.mountRoutingReactFlow = function mountRoutingReactFlow() {
  const container = document.getElementById("knowledgeReactFlow");
  if (!container) return;
  container.classList.add("rf-shell--knowledge", "rf-shell--routing");
  if (!root) root = createRoot(container);
  root.render(h(RoutingFlow));
};
window.unmountRoutingReactFlow = function unmountRoutingReactFlow() {
  if (!root) return;
  root.unmount();
  root = null;
};
