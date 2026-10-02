/* Delivery and Settlement LLD, traced from InferenceService and the streaming lifecycle. */
import React from "react";
import { createRoot } from "react-dom/client";
import { ReactFlow, Background, Controls, Handle, Position, MarkerType } from "@xyflow/react";

const h = React.createElement;
const HANDLES = [
  h(Handle, { type: "target", position: Position.Top, id: "top", className: "rf-handle", key: "top" }),
  h(Handle, { type: "source", position: Position.Bottom, id: "bottom", className: "rf-handle", key: "bottom" }),
  h(Handle, { type: "source", position: Position.Right, id: "right", className: "rf-handle", key: "right" }),
  h(Handle, { type: "source", position: Position.Left, id: "left-out", className: "rf-handle", key: "left-out" }),
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
  { id: "JSON_LANE", x: 20, y: 180, w: 550, h: 610, kind: "json", title: "JSON · chat, embed, rerank", hint: "settle before returning" },
  { id: "SSE_LANE", x: 605, y: 180, w: 585, h: 1080, kind: "sse", title: "SSE · streaming chat", hint: "deliver chunks, then clean up" },
];

const STEPS = [
  { id: "START", x: 340, y: 60, number: "1", kind: "source", title: "Continue under an acquired reservation", sub: "Non-streaming result or prepared streaming session" },
  { id: "N_RESULT", x: 55, y: 240, number: "2a", kind: "gather", title: "Receive the typed provider response", sub: "Chat, embedding or rerank result" },
  { id: "N_USAGE", x: 55, y: 350, number: "3a", kind: "gather", title: "Read any reported token usage", sub: "Embed completion count is zero when usage exists" },
  { id: "N_FINALIZE", x: 55, y: 460, number: "4a", kind: "assemble", title: "Finalize the reservation", sub: "PUT completed usage; wait before JSON success" },
  { id: "N_JSON", x: 55, y: 570, number: "5a", kind: "output", title: "Return one JSON response", sub: "Only after completed accounting succeeds" },
  { id: "N_ERROR", x: 55, y: 680, number: "!", kind: "neutral", title: "Provider fails or request is cancelled", sub: "Finalize failed or cancelled; preserve the cause" },
  { id: "S_SESSION", x: 645, y: 240, number: "2b", kind: "gather", title: "Own the stream, quota and worker slot", sub: "A closable StreamingInferenceSession" },
  { id: "S_RESPONSE", x: 645, y: 350, number: "3b", kind: "gather", title: "Create a managed SSE response", sub: "Response owns the source before body iteration" },
  { id: "S_READ", x: 645, y: 460, number: "4b", kind: "enrich", title: "Read one provider chunk at a time", sub: "Fixed deadline and cumulative usage snapshots" },
  { id: "S_EVENT", x: 645, y: 570, number: "5b", kind: "enrich", title: "Send text, metadata and heartbeats", sub: "Thread ID, sequence and optional request ID" },
  { id: "S_TERMINAL", x: 645, y: 680, number: "6b", kind: "enrich", title: "Classify the ending", sub: "Completed, failed or disconnected" },
  { id: "S_CLOSE", x: 645, y: 790, number: "7b", kind: "assemble", title: "Close the provider iterator", sub: "Bound cleanup, then continue even if close fails" },
  { id: "S_FINALIZE", x: 645, y: 900, number: "8b", kind: "assemble", title: "Finalize reserved token usage", sub: "Send terminal status and observed counts" },
  { id: "S_RELEASE", x: 645, y: 1010, number: "9b", kind: "assemble", title: "Release the worker stream slot", sub: "Finally block; lease release is idempotent" },
  { id: "S_END", x: 645, y: 1120, number: "10b", kind: "output", title: "End the SSE response", sub: "Connected: complete; failure: error then complete" },
];

const JSON_PATH = [["N_RESULT", "N_USAGE"], ["N_USAGE", "N_FINALIZE"], ["N_FINALIZE", "N_JSON"]];
const STREAM_PATH = [
  ["S_SESSION", "S_RESPONSE"], ["S_RESPONSE", "S_READ"], ["S_READ", "S_EVENT"],
  ["S_EVENT", "S_TERMINAL"], ["S_TERMINAL", "S_CLOSE"],
  ["S_CLOSE", "S_FINALIZE"], ["S_FINALIZE", "S_RELEASE"], ["S_RELEASE", "S_END"],
];

function DeliveryFlow() {
  const onOpen = React.useCallback((element, id, immediate) => {
    const detail = window.DIAGRAM_STAGES?.delivery?.details?.[id];
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
      style: { width: step.id === "START" ? 520 : step.id.startsWith("N_") ? 480 : 500, height: 88 },
      data: { ...step, onOpen }, draggable: false, selectable: false, zIndex: 3 })),
  ], [onOpen]);
  const edges = React.useMemo(() => [
    { id: "start-json", source: "START", target: "N_RESULT", sourceHandle: "left-out", targetHandle: "top", type: "smoothstep", label: "JSON" },
    { id: "start-stream", source: "START", target: "S_SESSION", sourceHandle: "right", targetHandle: "top", type: "smoothstep", label: "stream" },
    ...[...JSON_PATH, ...STREAM_PATH].map(([source, target]) => ({ id: `${source}-${target}`, source, target,
      sourceHandle: "bottom", targetHandle: "top", type: "smoothstep" })),
    { id: "json-failure", source: "START", target: "N_ERROR", sourceHandle: "left-out", targetHandle: "left", type: "smoothstep", label: "call fails" },
    { id: "stream-no-chunk", source: "S_RESPONSE", target: "S_TERMINAL", sourceHandle: "left-out", targetHandle: "left", type: "smoothstep", label: "close before first chunk" },
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
window.mountDeliveryReactFlow = function mountDeliveryReactFlow() {
  const container = document.getElementById("knowledgeReactFlow");
  if (!container) return;
  container.classList.add("rf-shell--knowledge", "rf-shell--delivery");
  if (!root) root = createRoot(container);
  root.render(h(DeliveryFlow));
};
window.unmountDeliveryReactFlow = function unmountDeliveryReactFlow() {
  if (!root) return;
  root.unmount();
  root = null;
};
