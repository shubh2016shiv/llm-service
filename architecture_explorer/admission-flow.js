/* Receive & Validate LLD, traced from the ASGI middleware and FastAPI handlers. */
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

const STEPS = [
  { id: "ARRIVE", x: 120, y: 28, number: "1", kind: "source", title: "Receive an HTTP request", sub: "ASGI receives the request and its body stream" },
  { id: "ID", x: 120, y: 142, number: "2", kind: "gather", title: "Keep or create a safe request ID", sub: "Validate X-Request-ID; otherwise generate a UUID" },
  { id: "WRAP", x: 120, y: 256, number: "3", kind: "gather", title: "Wrap the response and errors", sub: "CORS and the unexpected-error safety net surround the route" },
  { id: "SIZE", x: 120, y: 370, number: "4", kind: "assemble", title: "Enforce the body limit", sub: "Check Content-Length, then count bytes as chunks arrive" },
  { id: "PARSE", x: 120, y: 484, number: "5", kind: "assemble", title: "Match the route and validate input", sub: "FastAPI parses the request and checks declared fields" },
  { id: "NEXT", x: 120, y: 598, number: "6", kind: "output", title: "Continue through route dependencies", sub: "Authentication and later authorization decide access" },
  { id: "UNEXPECTED", x: 750, y: 244, number: "!", kind: "neutral", title: "Unexpected failure", sub: "Sanitized 500 before headers; re-raise afterward" },
  { id: "BODY_ERROR", x: 750, y: 358, number: "!", kind: "neutral", title: "400 or 413", sub: "Invalid length or body too large" },
  { id: "VALIDATION_ERROR", x: 750, y: 472, number: "!", kind: "neutral", title: "422 validation error", sub: "Return field diagnostics, never rejected values" },
];

const LINKS = [
  ["ARRIVE", "ID"], ["ID", "WRAP"], ["WRAP", "SIZE"],
  ["SIZE", "PARSE"], ["PARSE", "NEXT"],
];

function AdmissionFlow() {
  const onOpen = React.useCallback((element, id, immediate) => {
    const detail = window.DIAGRAM_STAGES?.admission?.details?.[id];
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
  const nodes = React.useMemo(() => STEPS.map((step) => ({
    id: step.id, type: "step", position: { x: step.x, y: step.y },
    style: { width: step.number === "!" ? 300 : 510, height: 88 },
    data: { ...step, onOpen }, draggable: false, selectable: false,
  })), [onOpen]);
  const edges = React.useMemo(() => [
    ...LINKS.map(([source, target]) => ({ id: `${source}-${target}`, source, target,
      sourceHandle: "bottom", targetHandle: "top", type: "smoothstep" })),
    { id: "size-error", source: "SIZE", target: "BODY_ERROR", sourceHandle: "right", targetHandle: "left", type: "smoothstep", label: "rejected" },
    { id: "validation-error", source: "PARSE", target: "VALIDATION_ERROR", sourceHandle: "right", targetHandle: "left", type: "smoothstep", label: "invalid" },
    { id: "unexpected-error", source: "WRAP", target: "UNEXPECTED", sourceHandle: "right", targetHandle: "left", type: "smoothstep", label: "uncaught inside" },
  ].map((edge) => ({ ...edge,
    markerEnd: { type: MarkerType.ArrowClosed, width: 15, height: 15, color: "#829188" },
    style: { stroke: "#829188", strokeWidth: 1.7 },
    labelStyle: { fill: "#3f4a45", fontSize: 11, fontWeight: 700 },
    labelBgStyle: { fill: "#f4f1e9", fillOpacity: 0.96 }, labelBgPadding: [7, 4],
  })), []);
  return h(ReactFlow, { nodes, edges, nodeTypes: { step: StepNode },
    fitView: true, fitViewOptions: { padding: 0.07 }, minZoom: 0.35, maxZoom: 1.5,
    nodesDraggable: false, nodesConnectable: false, elementsSelectable: false,
    zoomOnDoubleClick: false,
    onNodeMouseEnter: handleNodeEnter,
    onNodeMouseLeave: handleNodeLeave,
  }, h(Background, { gap: 22, size: 1, color: "rgba(16,22,20,.08)" }),
  h(Controls, { showInteractive: false }));
}

let root = null;
window.mountAdmissionReactFlow = function mountAdmissionReactFlow() {
  const container = document.getElementById("knowledgeReactFlow");
  if (!container) return;
  container.classList.add("rf-shell--knowledge", "rf-shell--admission");
  if (!root) root = createRoot(container);
  root.render(h(AdmissionFlow));
};
window.unmountAdmissionReactFlow = function unmountAdmissionReactFlow() {
  if (!root) return;
  root.unmount();
  root = null;
};
