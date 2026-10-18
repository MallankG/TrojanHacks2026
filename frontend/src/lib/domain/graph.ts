import { EDGES, MAIN_PATH, NODES, UNREACHABLE_UNTIL_FIX } from "@/lib/seed/graph";
import type { GraphView } from "@/lib/store/state";
import type { EdgeKey, GraphEdge, NodeKey } from "@/lib/types";

export interface EdgeRender {
  edge: GraphEdge;
  visible: boolean;
  /** True for the quarantined hop drawn as a dashed placeholder. */
  placeholder: boolean;
  selected: boolean;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export interface NodeRender {
  key: NodeKey;
  opacity: number;
  /** Shown faded with "not reached" because the path breaks before it. */
  unreached: boolean;
  selected: boolean;
}

export interface GraphModel {
  edges: Record<EdgeKey, EdgeRender>;
  nodes: Record<NodeKey, NodeRender>;
  tokenVisible: boolean;
  visiblePath: GraphEdge[];
  heldShown: boolean;
  tableRows: Array<{ edge: GraphEdge; held: boolean }>;
}

const NODE_RADIUS_START = 30;
const NODE_RADIUS_END = 34;

function shorten(from: NodeKey, to: NodeKey) {
  const a = NODES[from];
  const b = NODES[to];
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const length = Math.hypot(dx, dy) || 1;
  const ux = dx / length;
  const uy = dy / length;
  return { x1: a.x + ux * NODE_RADIUS_START, y1: a.y + uy * NODE_RADIUS_START, x2: b.x - ux * NODE_RADIUS_END, y2: b.y - uy * NODE_RADIUS_END };
}

/**
 * Computes what the workbench shows for the current filters. The seed is ACC-2208 (B);
 * traversal is time-respecting and bounded by depth, direction, rail, amount and time.
 */
export function buildGraphModel(view: GraphView, fixed: boolean): GraphModel {
  const visible = {} as Record<EdgeKey, boolean>;
  for (const e of EDGES) {
    const inDepth = e.hop === -1 ? view.direction !== "fwd" : view.direction !== "back" && e.hop <= view.depth;
    const reachable = fixed || !UNREACHABLE_UNTIL_FIX.includes(e.key);
    const overlayOk = e.cycle ? view.overlays.cycle : e.side ? view.expanded : true;
    const filterOk = view.rails[e.rail] && e.amount >= view.minAmount && e.order <= view.time;
    visible[e.key] = inDepth && reachable && overlayOk && filterOk;
  }
  const heldShown = !fixed && view.direction !== "back" && view.depth >= 2 && view.time >= 3 && view.rails.ACH;

  const on = Object.fromEntries((Object.keys(NODES) as NodeKey[]).map((k) => [k, false])) as Record<NodeKey, boolean>;
  for (const e of EDGES) {
    if (visible[e.key]) {
      on[e.from] = true;
      on[e.to] = true;
    }
  }
  on.B = true;
  if (heldShown) on.C = true;
  const tokenVisible = view.overlays.token || view.expanded;
  if (tokenVisible) {
    on.T = true;
    on.G = true;
  }

  const selectedEdge = view.selection.kind === "edge" ? view.selection.key : null;
  const selectedNode = view.selection.kind === "node" ? view.selection.key : null;

  const edges = Object.fromEntries(
    EDGES.map((e) => {
      const placeholder = e.key === "e3" && !fixed && heldShown;
      return [e.key, { edge: e, visible: visible[e.key] || placeholder, placeholder, selected: selectedEdge === e.key, ...shorten(e.from, e.to) }];
    }),
  ) as Record<EdgeKey, EdgeRender>;

  const nodes = Object.fromEntries(
    (Object.keys(NODES) as NodeKey[]).map((k) => {
      const unreached = !fixed && !on[k] && (k === "D" || k === "E" || k === "F");
      return [k, { key: k, opacity: on[k] ? 1 : unreached ? 0.18 : 0, unreached, selected: selectedNode === k }];
    }),
  ) as Record<NodeKey, NodeRender>;

  const visiblePath = MAIN_PATH.map((k) => EDGES.find((e) => e.key === k) as GraphEdge).filter((e) => visible[e.key]);
  const tableRows = EDGES.filter((e) => edges[e.key].visible).map((edge) => ({ edge, held: edge.key === "e3" && !fixed }));

  return { edges, nodes, tokenVisible, visiblePath, heldShown, tableRows };
}

export function pathSummary(model: GraphModel, fixed: boolean): Array<[string, string]> {
  const n = model.visiblePath.length;
  const sideVisible = model.edges.e7.visible;
  const exitVisible = model.edges.e5.visible && !model.edges.e5.placeholder;
  return [
    ["Path length", `${n} of 5 hops${fixed ? "" : " (breaks at ACC-3315)"}`],
    ["Elapsed", fixed && n === 5 ? "1 d 1 h 6 m" : n >= 2 ? "1 h 48 m so far" : "n/a"],
    ["Entered path", "$58,200.00"],
    ["Left by cross-border wire", exitVisible ? "$46,800.00" : "Not visible"],
    ["Split to ACC-3391", sideVisible ? "$9,600.00" : "Hidden (expand ACC-2208)"],
    ["Stayed in intermediaries", fixed && exitVisible && sideVisible ? "$1,800.00" : "Incomplete"],
  ];
}

export function edgeFlags(key: EdgeKey, fixed: boolean): string[] {
  switch (key) {
    case "e1":
      return ["Large vs baseline", "New counterparty"];
    case "e2":
      return ["Rapid pass-through", "Rail switch ACH to P2P"];
    case "e3":
      return fixed ? ["Rail switch P2P to ACH"] : ["Data hold"];
    case "e5":
      return ["Cross-border exit"];
    case "e6":
      return ["Possible round trip"];
    case "e7":
      return ["Shared-device recipient"];
    default:
      return ["Rail switch to wire"];
  }
}

export interface LensModelOptions {
  /** Edges drawn emphasized (for example, newly visible in a snapshot or the current story hop). */
  highlighted?: EdgeKey[];
  /** The quarantined hop drawn as a dashed "On hold" placeholder. */
  placeholder?: EdgeKey | null;
  tokenVisible?: boolean;
}

/** Canvas model for an explicit set of edges, used by the time machine and the storyboard. */
export function modelForEdges(visibleKeys: EdgeKey[], options: LensModelOptions = {}): GraphModel {
  const highlighted = new Set(options.highlighted ?? []);
  const shown = new Set<EdgeKey>(visibleKeys);
  if (options.placeholder) shown.add(options.placeholder);
  const on = Object.fromEntries((Object.keys(NODES) as NodeKey[]).map((k) => [k, false])) as Record<NodeKey, boolean>;
  for (const e of EDGES) {
    if (shown.has(e.key)) {
      on[e.from] = true;
      on[e.to] = true;
    }
  }
  on.B = true;
  const tokenVisible = Boolean(options.tokenVisible);
  if (tokenVisible) {
    on.T = true;
    on.G = true;
  }
  const edges = Object.fromEntries(
    EDGES.map((e) => [e.key, { edge: e, visible: shown.has(e.key), placeholder: options.placeholder === e.key, selected: highlighted.has(e.key), ...shorten(e.from, e.to) }]),
  ) as Record<EdgeKey, EdgeRender>;
  const nodes = Object.fromEntries((Object.keys(NODES) as NodeKey[]).map((k) => [k, { key: k, opacity: on[k] ? 1 : 0, unreached: false, selected: false }])) as Record<NodeKey, NodeRender>;
  const visiblePath = MAIN_PATH.map((k) => EDGES.find((e) => e.key === k) as GraphEdge).filter((e) => visibleKeys.includes(e.key));
  const tableRows = EDGES.filter((e) => shown.has(e.key)).map((edge) => ({ edge, held: options.placeholder === edge.key }));
  return { edges, nodes, tokenVisible, visiblePath, heldShown: Boolean(options.placeholder), tableRows };
}
