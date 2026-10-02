/* Provider Execution LLD, traced from InferenceService, ProviderRegistry and BaseProvider. */
import React from "react";
import { createRoot } from "react-dom/client";
import { ReactFlow, Background, Controls, Handle, Position, MarkerType } from "@xyflow/react";

const h = React.createElement;
const HANDLES = [
  h(Handle, { type: "target", position: Position.Top, id: "top", className: "rf-handle", key: "top" }),
  h(Handle, { type: "source", position: Position.Bottom, id: "bottom", className: "rf-handle", key: "bottom" }),
  h(Handle, { type: "source", position: Position.Right, id: "right", className: "rf-handle", key: "right" }),
  h(Handle, { type: "target", position: Position.Right, id: "right-in", className: "rf-handle", key: "right-in" }),
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
  { id: "LOOKUP_LANE", x: 20, y: 20, w: 1170, h: 850, kind: "lookup", title: "1 · Find or build the adapter", hint: "a cache hit skips construction" },
  { id: "CALL_LANE", x: 20, y: 885, w: 1170, h: 450, kind: "call", title: "2 · Execute through one contract", hint: "the vendor-specific call stays inside its adapter" },
  { id: "RESULT_LANE", x: 20, y: 1350, w: 1170, h: 170, kind: "result", title: "3 · Hand results to the service", hint: "settlement and streaming delivery follow" },
];

const STEPS = [
  { id: "INPUT", x: 245, y: 70, number: "1", kind: "source", title: "Receive route and acquired reservation", sub: "Provider, endpoint and credential reference are already fixed" },
  { id: "CACHE", x: 245, y: 180, number: "2", kind: "gather", title: "Look up the route fingerprint", sub: "Reuse a fresh adapter or share one in-progress build" },
  { id: "CLASS", x: 245, y: 290, number: "3", kind: "gather", title: "Choose an audited adapter class", sub: "OpenAI, Anthropic, vLLM, Azure OpenAI or Bedrock" },
  { id: "TRANSPORT", x: 245, y: 400, number: "4", kind: "gather", title: "Borrow a REST client or AWS session", sub: "The REST connection pool is shared by this worker" },
  { id: "BREAKER", x: 245, y: 510, number: "5", kind: "gather", title: "Get this provider's circuit breaker", sub: "One process-local breaker per provider name" },
  { id: "SECRET", x: 245, y: 620, number: "6", kind: "gather", title: "Read the tenant credential if needed", sub: "AWS SigV4 and no-auth routes skip this read" },
  { id: "ADAPTER", x: 245, y: 730, number: "7", kind: "assemble", title: "Construct and cache the adapter", sub: "Route, transport, breaker and optional secret" },
  { id: "OPERATION", x: 245, y: 940, number: "8", kind: "enrich", title: "Select chat, embed, rerank or stream", sub: "InferenceService uses one provider interface" },
  { id: "GUARD", x: 245, y: 1050, number: "9", kind: "enrich", title: "Call under breaker protection", sub: "Streams use a guarded producer and one-item queue" },
  { id: "VENDOR", x: 245, y: 1160, number: "10", kind: "enrich", title: "Translate and call the vendor API", sub: "Adapter handles payload, auth headers and response parsing" },
  { id: "OUTPUT", x: 245, y: 1400, number: "11", kind: "output", title: "Return typed result or stream chunks", sub: "The service or streaming session handles the next phase" },
  { id: "BUILD_FAIL", x: 850, y: 290, number: "!", kind: "neutral", title: "Adapter cannot be built", sub: "Unknown class or unavailable transport" },
  { id: "SECRET_FAIL", x: 850, y: 620, number: "!", kind: "neutral", title: "Credential read fails", sub: "Missing, invalid or denied secret" },
  { id: "OPEN", x: 850, y: 1050, number: "!", kind: "neutral", title: "Circuit already open", sub: "No upstream call; return a typed error" },
  { id: "FAILURE", x: 850, y: 1160, number: "!", kind: "neutral", title: "Provider call fails", sub: "Classify transport and SDK errors" },
];

const NEXT = [
  ["INPUT", "CACHE"], ["CACHE", "CLASS"], ["CLASS", "TRANSPORT"],
  ["TRANSPORT", "BREAKER"], ["BREAKER", "SECRET"], ["SECRET", "ADAPTER"],
  ["ADAPTER", "OPERATION"], ["OPERATION", "GUARD"],
  ["GUARD", "VENDOR"], ["VENDOR", "OUTPUT"],
];
const STOPS = [
  ["CLASS", "BUILD_FAIL"], ["TRANSPORT", "BUILD_FAIL"],
  ["SECRET", "SECRET_FAIL"], ["GUARD", "OPEN"], ["VENDOR", "FAILURE"],
];

function ExecutionFlow() {
  const onOpen = React.useCallback((element, id, immediate) => {
    const detail = window.DIAGRAM_STAGES?.execution?.details?.[id];
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
      style: { width: step.number === "!" ? 310 : 540, height: 88 },
      data: { ...step, onOpen }, draggable: false, selectable: false, zIndex: 3 })),
  ], [onOpen]);
  const edges = React.useMemo(() => [
    ...NEXT.map(([source, target]) => ({ id: `${source}-${target}`, source, target,
      sourceHandle: "bottom", targetHandle: "top", type: "smoothstep",
      label: source === "CACHE" ? "cache miss" : undefined })),
    { id: "cache-hit", source: "CACHE", target: "OPERATION",
      sourceHandle: "right", targetHandle: "right-in", type: "smoothstep",
      label: "cache hit · skip build", style: { strokeDasharray: "5 4" } },
    ...STOPS.map(([source, target]) => ({ id: `${source}-${target}`, source, target,
      sourceHandle: "right", targetHandle: "left", type: "smoothstep", label: "stop" })),
  ].map((edge) => ({ ...edge,
    markerEnd: { type: MarkerType.ArrowClosed, width: 15, height: 15, color: "#829188" },
    style: { stroke: "#829188", strokeWidth: 1.7, ...edge.style }, zIndex: 4,
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
window.mountExecutionReactFlow = function mountExecutionReactFlow() {
  const container = document.getElementById("knowledgeReactFlow");
  if (!container) return;
  container.classList.add("rf-shell--knowledge", "rf-shell--execution");
  if (!root) root = createRoot(container);
  root.render(h(ExecutionFlow));
};
window.unmountExecutionReactFlow = function unmountExecutionReactFlow() {
  if (!root) return;
  root.unmount();
  root = null;
};
