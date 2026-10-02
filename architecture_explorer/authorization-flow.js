/* Inference Authorization LLD, traced from the live source-of-truth path. */
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
  { id: "INPUT", x: 120, y: 28, number: "1", kind: "source", title: "Receive caller and route identifiers", sub: "Authenticated user, X-Tenant-ID and X-Deployment-Key" },
  { id: "TENANT", x: 120, y: 142, number: "2", kind: "gather", title: "Gate 1 · Check the tenant", sub: "Tenant exists and status is active or trial" },
  { id: "MEMBER", x: 120, y: 256, number: "3", kind: "gather", title: "Gate 2 · Check membership and role", sub: "Active member with an inference-eligible tenant role" },
  { id: "DEPLOY", x: 120, y: 370, number: "4", kind: "assemble", title: "Gate 3 · Check the deployment", sub: "Exact tenant + key exists and deployment is active" },
  { id: "ENTITLE", x: 120, y: 484, number: "5", kind: "assemble", title: "Gate 4 · Check the entitlement", sub: "Active grant for this user, deployment, provider and model" },
  { id: "CONTEXT", x: 120, y: 598, number: "6", kind: "output", title: "Build a frozen access context", sub: "Carry the exact approved identifiers; no credentials" },
  { id: "ROUTE", x: 120, y: 712, number: "7", kind: "output", title: "Pass to route resolution", sub: "The next phase resolves this approved entitlement" },
  { id: "TENANT_FAIL", x: 760, y: 142, number: "!", kind: "neutral", title: "Tenant rejected", sub: "Missing: 404 · suspended: 403" },
  { id: "MEMBER_FAIL", x: 760, y: 256, number: "!", kind: "neutral", title: "Membership denied", sub: "Missing, inactive or ineligible role: 403" },
  { id: "DEPLOY_FAIL", x: 760, y: 370, number: "!", kind: "neutral", title: "Deployment rejected", sub: "Missing: 404 · inactive: 422" },
  { id: "ENTITLE_FAIL", x: 760, y: 484, number: "!", kind: "neutral", title: "Entitlement denied", sub: "No active exact grant: 403" },
];

const LINKS = [
  ["INPUT", "TENANT"], ["TENANT", "MEMBER"], ["MEMBER", "DEPLOY"],
  ["DEPLOY", "ENTITLE"], ["ENTITLE", "CONTEXT"], ["CONTEXT", "ROUTE"],
];

function AuthorizationFlow() {
  const onOpen = React.useCallback((element, id, immediate) => {
    const detail = window.DIAGRAM_STAGES?.authorization?.details?.[id];
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
    style: { width: step.number === "!" ? 320 : 510, height: 88 },
    data: { ...step, onOpen }, draggable: false, selectable: false,
  })), [onOpen]);
  const edges = React.useMemo(() => [
    ...LINKS.map(([source, target]) => ({ id: `${source}-${target}`, source, target,
      sourceHandle: "bottom", targetHandle: "top", type: "smoothstep" })),
    ...[["TENANT", "TENANT_FAIL"], ["MEMBER", "MEMBER_FAIL"],
      ["DEPLOY", "DEPLOY_FAIL"], ["ENTITLE", "ENTITLE_FAIL"]]
      .map(([source, target]) => ({ id: `${source}-${target}`, source, target,
        sourceHandle: "right", targetHandle: "left", type: "smoothstep", label: "fails" })),
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
window.mountAuthorizationReactFlow = function mountAuthorizationReactFlow() {
  const container = document.getElementById("knowledgeReactFlow");
  if (!container) return;
  container.classList.add("rf-shell--knowledge", "rf-shell--authorization");
  if (!root) root = createRoot(container);
  root.render(h(AuthorizationFlow));
};
window.unmountAuthorizationReactFlow = function unmountAuthorizationReactFlow() {
  if (!root) return;
  root.unmount();
  root = null;
};
