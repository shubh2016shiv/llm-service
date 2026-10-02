/* Capacity Reservation LLD, traced from InferenceService and TokenManagerClient. */
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
  { id: "LOCAL", x: 20, y: 20, w: 1170, h: 390, kind: "local", title: "1 · Check local limits", hint: "the worker slot applies only to streaming chat" },
  { id: "SHARED", x: 20, y: 425, w: 1170, h: 650, kind: "shared", title: "2 · Ask the token manager", hint: "every chat, embed and rerank call" },
  { id: "HANDOFF_LANE", x: 20, y: 1090, w: 1170, h: 160, kind: "handoff", title: "3 · Hand off ownership", hint: "execution and later finalization" },
];

const STEPS = [
  { id: "INPUT", x: 250, y: 70, number: "1", kind: "source", title: "Receive the resolved route and request", sub: "Provider, model, endpoint, quota key and operation are fixed" },
  { id: "CHAT_LIMIT", x: 250, y: 180, number: "2", kind: "gather", title: "For chat, check the model token ceiling", sub: "Explicit max_tokens must not exceed the resolved limit" },
  { id: "STREAM_SLOT", x: 250, y: 290, number: "3", kind: "gather", title: "If streaming, claim a worker slot", sub: "Fail fast at the per-worker concurrency limit" },
  { id: "PAYLOAD", x: 250, y: 475, number: "4", kind: "enrich", title: "Build the reservation request", sub: "Operation input, completion estimate and route identity" },
  { id: "SEND", x: 250, y: 585, number: "5", kind: "enrich", title: "POST to the token manager", sub: "Short-lived service token, request ID and configured timeout" },
  { id: "STATUS", x: 250, y: 695, number: "6", kind: "enrich", title: "Require an acquired allocation", sub: "Refused or waiting allocations never reach a provider" },
  { id: "BIND", x: 250, y: 805, number: "7", kind: "enrich", title: "Validate response and endpoint binding", sub: "Reserved endpoint must match the authorized route" },
  { id: "RESERVATION", x: 250, y: 915, number: "8", kind: "assemble", title: "Build an immutable TokenReservation", sub: "Reservation ID, counts, identities and optional expiry" },
  { id: "HANDOFF", x: 250, y: 1140, number: "9", kind: "output", title: "Start provider work under reservation", sub: "Service or streaming session later finalizes usage" },
  { id: "LIMIT_FAIL", x: 850, y: 180, number: "!", kind: "neutral", title: "Chat limit exceeded", sub: "Stop before reservation: 422" },
  { id: "SLOT_FAIL", x: 850, y: 290, number: "!", kind: "neutral", title: "Stream slots full", sub: "Fail fast with 503 and Retry-After" },
  { id: "MANAGER_FAIL", x: 850, y: 585, number: "!", kind: "neutral", title: "Manager unavailable", sub: "Timeout, connection error or 5xx: 503" },
  { id: "REJECTED", x: 850, y: 695, number: "!", kind: "neutral", title: "Capacity not acquired", sub: "Rejected or waiting: 429" },
  { id: "PROTOCOL_FAIL", x: 850, y: 805, number: "!", kind: "neutral", title: "Invalid manager contract", sub: "Malformed or mismatched response: 502" },
];

const NEXT = [
  ["INPUT", "CHAT_LIMIT"], ["CHAT_LIMIT", "STREAM_SLOT"],
  ["STREAM_SLOT", "PAYLOAD"], ["PAYLOAD", "SEND"],
  ["SEND", "STATUS"], ["STATUS", "BIND"],
  ["BIND", "RESERVATION"], ["RESERVATION", "HANDOFF"],
];

function ReservationFlow() {
  const onOpen = React.useCallback((element, id, immediate) => {
    const detail = window.DIAGRAM_STAGES?.quota?.details?.[id];
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
    ...[["CHAT_LIMIT", "LIMIT_FAIL"], ["STREAM_SLOT", "SLOT_FAIL"],
      ["SEND", "MANAGER_FAIL"], ["STATUS", "REJECTED"],
      ["BIND", "PROTOCOL_FAIL"]]
      .map(([source, target]) => ({ id: `${source}-${target}`, source, target,
        sourceHandle: "right", targetHandle: "left", type: "smoothstep", label: "stop" })),
    { id: "status-protocol", source: "STATUS", target: "PROTOCOL_FAIL", sourceHandle: "right", targetHandle: "left", type: "smoothstep", label: "bad reply" },
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
window.mountReservationReactFlow = function mountReservationReactFlow() {
  const container = document.getElementById("knowledgeReactFlow");
  if (!container) return;
  container.classList.add("rf-shell--knowledge", "rf-shell--reservation");
  if (!root) root = createRoot(container);
  root.render(h(ReservationFlow));
};
window.unmountReservationReactFlow = function unmountReservationReactFlow() {
  if (!root) return;
  root.unmount();
  root = null;
};
