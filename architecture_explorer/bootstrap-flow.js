/* Bootstrap low-level design, traced from app/main.py and app/bootstrap.py. */
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

function LaneNode({ data }) {
  return h("div", { className: `rf-lane rf-lane--${data.kind}` },
    h("span", { className: "rf-lane-label" },
      h("strong", null, data.title),
      h("span", { className: "rf-lane-annotation" }, ` — ${data.hint}`)
    )
  );
}

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

/* Three phases, matching how the whole startup sequence is explained
   elsewhere: get the process ready to build anything (1-3), validate and
   open everything it depends on (4-6), then run and, on either a startup
   failure or a shutdown, unwind it (7-8). Step 8 sits in the Phase 3 row
   rather than beside step 6, because it belongs to teardown regardless of
   which phase actually triggered it. */
/* 58px between a lane's top edge and its first card, 24px below its last
   card before the lane ends: the same clearance routing-flow.js's lanes use.
   Phase 1 originally had only 28px of headroom and Phase 2/3 had 6px and 0px,
   so steps 4 and 7 (drawn on top, z-index 3) painted directly over the back
   half of their own lane's label instead of sitting below it. */
const LANES = [
  { id: "PHASE1", x: 20, y: 0, w: 1080, h: 398, kind: "phase1", title: "Phase 1 · Application initialization", hint: "load settings, build the app, start the lifespan routine" },
  { id: "PHASE2", x: 20, y: 398, w: 1080, h: 398, kind: "phase2", title: "Phase 2 · Validation & resource allocation", hint: "check AI provider configs, open shared connections, set up routing and safety" },
  { id: "PHASE3", x: 20, y: 796, w: 1080, h: 170, kind: "phase3", title: "Phase 3 · Execution & teardown", hint: "serve traffic, then close everything in reverse on failure or shutdown" },
];

const STEPS = [
  { id: "SETTINGS", x: 260, y: 58, number: "1", kind: "source", title: "Load configuration from environment variables", sub: "Database address, cache address, login-token lifetime, and environment (dev / staging / production)" },
  { id: "API", x: 260, y: 172, number: "2", kind: "source", title: "Build the FastAPI application", sub: "Attach settings, add the middleware every request passes through, then register the routes" },
  { id: "LIFESPAN", x: 260, y: 286, number: "3", kind: "gather", title: "Run the startup-and-shutdown routine", sub: "One routine runs before traffic starts and again at shutdown, tracking what must be closed" },
  { id: "CATALOG", x: 260, y: 456, number: "4", kind: "gather", title: "Check the AI provider configuration files", sub: "One file per AI provider plus one shared file, all checked before anything connects" },
  { id: "RESOURCES", x: 260, y: 570, number: "5", kind: "assemble", title: "Open the connections every request will share", sub: "Database pool, Redis, AI provider client, API-key store and quota-service client" },
  { id: "WIRE", x: 260, y: 684, number: "6", kind: "assemble", title: "Set up routing, failure protection and connection reuse", sub: "Who may use what, which provider and model to call, and the failure and streaming limits" },
  { id: "READY", x: 260, y: 854, number: "7", kind: "output", title: "Start serving requests", sub: "Startup is complete. Every request reuses the connections built above" },
  { id: "CLOSE", x: 820, y: 854, number: "8", kind: "neutral", title: "Close resources in reverse order", sub: "On failure or shutdown, everything opened is closed in reverse order" },
];

const LINKS = [
  ["SETTINGS", "API"], ["API", "LIFESPAN"], ["LIFESPAN", "CATALOG"],
  ["CATALOG", "RESOURCES"], ["RESOURCES", "WIRE"], ["WIRE", "READY"],
];

function BootstrapFlow() {
  const onOpen = React.useCallback((element, id, immediate) => {
    const detail = window.DIAGRAM_STAGES?.bootstrap?.details?.[id];
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
      style: { width: step.id === "CLOSE" ? 250 : 510, height: 88 },
      data: { ...step, onOpen }, draggable: false, selectable: false, zIndex: 3 })),
  ], [onOpen]);
  const edges = React.useMemo(() => [
    ...LINKS.map(([source, target]) => ({ id: `${source}-${target}`, source, target,
      sourceHandle: "bottom", targetHandle: "top", type: "smoothstep" })),
    /* These two used to both enter CLOSE's left side, a few pixels apart,
       which crowded their labels together once step 5 and step 8 ended up
       far apart vertically. Routing the failure path into the top of CLOSE
       instead keeps it visually distinct from the shutdown handoff, which
       stays a short same-row hop into the left side. */
    { id: "failure-cleanup", source: "RESOURCES", target: "CLOSE", sourceHandle: "right", targetHandle: "top", type: "smoothstep", label: "startup fails" },
    { id: "shutdown-cleanup", source: "READY", target: "CLOSE", sourceHandle: "right", targetHandle: "left", type: "smoothstep", label: "shutdown" },
  ].map((edge) => ({ ...edge,
    markerEnd: { type: MarkerType.ArrowClosed, width: 15, height: 15, color: "#829188" },
    style: { stroke: "#829188", strokeWidth: 1.7 },
    labelStyle: { fill: "#3f4a45", fontSize: 11, fontWeight: 700 },
    labelBgStyle: { fill: "#f4f1e9", fillOpacity: 0.96 }, labelBgPadding: [8, 5],
  })), []);
  return h(ReactFlow, { nodes, edges, nodeTypes: { step: StepNode, lane: LaneNode },
    fitView: true, fitViewOptions: { padding: 0.05 }, minZoom: 0.3, maxZoom: 1.5,
    nodesDraggable: false, nodesConnectable: false, elementsSelectable: false,
    zoomOnDoubleClick: false,
    onNodeMouseEnter: handleNodeEnter,
    onNodeMouseLeave: handleNodeLeave,
  }, h(Background, { gap: 22, size: 1, color: "rgba(16,22,20,.08)" }),
  h(Controls, { showInteractive: false }));
}

let root = null;
window.mountBootstrapReactFlow = function mountBootstrapReactFlow() {
  const container = document.getElementById("knowledgeReactFlow");
  if (!container) return;
  container.classList.add("rf-shell--knowledge", "rf-shell--bootstrap");
  if (!root) root = createRoot(container);
  root.render(h(BootstrapFlow));
};
window.unmountBootstrapReactFlow = function unmountBootstrapReactFlow() {
  if (!root) return;
  root.unmount();
  root = null;
};
