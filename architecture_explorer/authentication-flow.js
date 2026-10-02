/* Authentication LLD, traced from get_current_user and validate_access_token. */
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
  { id: "DEPENDENCY", x: 120, y: 28, number: "1", kind: "source", title: "Ask for the current user", sub: "The inference route declares Depends(get_current_user)" },
  { id: "BEARER", x: 120, y: 142, number: "2", kind: "gather", title: "Extract a Bearer token", sub: "HTTPBearer reads the Authorization header" },
  { id: "SIGNATURE", x: 120, y: 256, number: "3", kind: "assemble", title: "Verify the signed JWT", sub: "Signature, algorithm, issuer, audience and time claims" },
  { id: "CONTRACT", x: 120, y: 370, number: "4", kind: "assemble", title: "Check the access-token contract", sub: "Required claims, kind, role, lifetime and identifiers" },
  { id: "IDENTITY", x: 120, y: 484, number: "5", kind: "output", title: "Return a frozen caller identity", sub: "user_id, platform role, token ID and token times" },
  { id: "MISSING", x: 760, y: 142, number: "!", kind: "neutral", title: "401: no Bearer token", sub: "The request stops before JWT decoding" },
  { id: "INVALID_JWT", x: 760, y: 256, number: "!", kind: "neutral", title: "401: verification failed", sub: "Invalid, malformed or expired JWT" },
  { id: "BAD_CLAIMS", x: 760, y: 370, number: "!", kind: "neutral", title: "401: unusable contents", sub: "Signed token fails the service contract" },
];

const LINKS = [
  ["DEPENDENCY", "BEARER"], ["BEARER", "SIGNATURE"],
  ["SIGNATURE", "CONTRACT"], ["CONTRACT", "IDENTITY"],
];

function AuthenticationFlow() {
  const onOpen = React.useCallback((element, id, immediate) => {
    const detail = window.DIAGRAM_STAGES?.identity?.details?.[id];
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
    { id: "missing-token", source: "BEARER", target: "MISSING", sourceHandle: "right", targetHandle: "left", type: "smoothstep", label: "missing" },
    { id: "invalid-jwt", source: "SIGNATURE", target: "INVALID_JWT", sourceHandle: "right", targetHandle: "left", type: "smoothstep", label: "invalid" },
    { id: "bad-claims", source: "CONTRACT", target: "BAD_CLAIMS", sourceHandle: "right", targetHandle: "left", type: "smoothstep", label: "unusable" },
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
window.mountAuthenticationReactFlow = function mountAuthenticationReactFlow() {
  const container = document.getElementById("knowledgeReactFlow");
  if (!container) return;
  container.classList.add("rf-shell--knowledge", "rf-shell--authentication");
  if (!root) root = createRoot(container);
  root.render(h(AuthenticationFlow));
};
window.unmountAuthenticationReactFlow = function unmountAuthenticationReactFlow() {
  if (!root) return;
  root.unmount();
  root = null;
};
