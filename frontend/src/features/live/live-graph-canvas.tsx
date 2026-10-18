"use client";

import { useMemo, type KeyboardEvent } from "react";
import type { GraphEdge, GraphNode, WorkbenchGraph } from "@/lib/live/api-graph";
import { money } from "@/lib/live/format";
import { useMaskers } from "@/lib/privacy/store";

const COLORS = { edge: "#9aa7b9", highlight: "#DC2626", selected: "#2563EB", focus: "#F59E0B" };
const COL_W = 180;
const ROW_H = 66;
const PAD_X = 70;
const PAD_Y = 46;
const LABEL_LIMIT = 36; // above this many edges, only highlighted or selected edges carry an amount label

export type GraphSelection = { kind: "edge"; id: string } | { kind: "node"; id: string } | null;

interface Layout {
  pos: Map<string, { x: number; y: number; layer: number }>;
  width: number;
  height: number;
  layers: number[];
}

/**
 * Layered layout: the focus account sits in column 0, accounts it pays sit to the right (one column
 * per hop), accounts that pay it sit to the left. Server-provided layers win; anything else (merged
 * path or pattern results) is placed by a breadth-first walk from the focus over the shown edges.
 */
export function layeredLayout(graph: WorkbenchGraph, focus: string | null): Layout {
  const layer = new Map<string, number>();
  for (const n of graph.nodes) if (typeof n.layer === "number") layer.set(n.id, n.layer);
  const out = new Map<string, string[]>();
  const inc = new Map<string, string[]>();
  for (const e of graph.edges) {
    out.set(e.source, [...(out.get(e.source) ?? []), e.target]);
    inc.set(e.target, [...(inc.get(e.target) ?? []), e.source]);
  }
  const seeds = focus && graph.nodes.some((n) => n.id === focus) ? [focus] : graph.edges[0] ? [graph.edges[0].source] : [];
  for (const s of seeds) if (!layer.has(s)) layer.set(s, 0);
  const queue = [...layer.keys()];
  while (queue.length > 0) {
    const id = queue.shift()!;
    const at = layer.get(id)!;
    for (const [neighbors, step] of [[out.get(id) ?? [], 1], [inc.get(id) ?? [], -1]] as const) {
      for (const next of neighbors) {
        if (layer.has(next)) continue;
        layer.set(next, at + step);
        queue.push(next);
      }
    }
  }
  const max = Math.max(0, ...layer.values());
  for (const n of graph.nodes) if (!layer.has(n.id)) layer.set(n.id, max + 1);

  const ref = (id: string) => graph.nodes.find((n) => n.id === id)?.account_ref ?? id;
  const columns = new Map<number, string[]>();
  for (const [id, l] of layer) columns.set(l, [...(columns.get(l) ?? []), id]);
  const layers = [...columns.keys()].sort((a, b) => a - b);
  const tallest = Math.max(1, ...[...columns.values()].map((c) => c.length));
  const width = Math.max(720, PAD_X * 2 + (layers.length - 1) * COL_W);
  const height = Math.max(300, PAD_Y * 2 + (tallest - 1) * ROW_H + 30);
  const pos = new Map<string, { x: number; y: number; layer: number }>();
  layers.forEach((l, ci) => {
    const ids = columns.get(l)!.sort((a, b) => ref(a).localeCompare(ref(b)));
    const x = layers.length === 1 ? width / 2 : PAD_X + (ci * (width - PAD_X * 2)) / (layers.length - 1);
    const span = (ids.length - 1) * ROW_H;
    ids.forEach((id, i) => pos.set(id, { x, y: height / 2 - span / 2 + i * ROW_H - 10, layer: l }));
  });
  return { pos, width, height, layers };
}

function onActivate(handler: () => void) {
  return (event: KeyboardEvent) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      handler();
    }
  };
}

interface CanvasProps {
  graph: WorkbenchGraph;
  focus: string | null;
  selection: GraphSelection;
  onSelect: (selection: GraphSelection) => void;
  /** Edges of the active path or pattern finding, drawn in amber. */
  highlight: Set<string>;
  /** Accounts to ring (for example, the accounts on the alert the workbench was opened from). */
  marked?: Set<string>;
}

/** Money-flow graph for the live workbench. Every node and edge is focusable and selectable by keyboard. */
export function LiveGraphCanvas({ graph, focus, selection, onSelect, highlight, marked }: CanvasProps) {
  const layout = useMemo(() => layeredLayout(graph, focus), [graph, focus]);
  const mask = useMaskers();
  const label = (n: GraphNode | undefined, id: string) => mask.ref(n?.account_ref ?? id.slice(0, 8));
  const nodesById = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph]);

  // Parallel edges between the same pair fan out so each stays clickable.
  const offsets = useMemo(() => {
    const seen = new Map<string, number>();
    const result = new Map<string, number>();
    for (const e of graph.edges) {
      const pair = `${e.source}>${e.target}`;
      const i = seen.get(pair) ?? 0;
      seen.set(pair, i + 1);
      result.set(e.id, i);
    }
    return result;
  }, [graph]);

  const highlightedNodes = useMemo(() => {
    const ids = new Set<string>();
    for (const e of graph.edges) {
      if (!highlight.has(e.id)) continue;
      ids.add(e.source);
      ids.add(e.target);
    }
    return ids;
  }, [graph, highlight]);

  const showLabels = graph.edges.length <= LABEL_LIMIT;

  const curve = (e: GraphEdge) => {
    const a = layout.pos.get(e.source);
    const b = layout.pos.get(e.target);
    if (!a || !b) return null;
    const k = offsets.get(e.id) ?? 0;
    const bend = (k % 2 === 0 ? 1 : -1) * Math.ceil(k / 2) * 22;
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const len = Math.hypot(dx, dy) || 1;
    // Edges that run backward or stay in one column arc outward so they do not overlap forward edges.
    const arc = b.layer <= a.layer ? 60 + Math.min(len / 4, 80) : 0;
    const nx = -dy / len;
    const ny = dx / len;
    const mx = (a.x + b.x) / 2 + nx * (bend + arc);
    const my = (a.y + b.y) / 2 + ny * (bend + arc);
    // Stop short of the node circles.
    const r = 16;
    const sx = a.x + ((mx - a.x) / (Math.hypot(mx - a.x, my - a.y) || 1)) * r;
    const sy = a.y + ((my - a.y) / (Math.hypot(mx - a.x, my - a.y) || 1)) * r;
    const ex = b.x - ((b.x - mx) / (Math.hypot(b.x - mx, b.y - my) || 1)) * (r + 4);
    const ey = b.y - ((b.y - my) / (Math.hypot(b.x - mx, b.y - my) || 1)) * (r + 4);
    return { d: `M${sx} ${sy} Q ${mx} ${my} ${ex} ${ey}`, lx: (sx + 2 * mx + ex) / 4, ly: (sy + 2 * my + ey) / 4 };
  };

  return (
    <div className="max-h-[640px] overflow-auto rounded-xl border border-line bg-[#fbfcfd]">
      <svg
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        width={layout.width}
        height={layout.height}
        className="block min-w-full"
        role="group"
        aria-label="Money-flow graph. Accounts are nodes and transactions are arrows. The table view lists the same information."
      >
        <defs>
          {Object.entries(COLORS).map(([name, color]) => (
            <marker key={name} id={`lg-arrow-${name}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0 0L10 5L0 10z" fill={color} />
            </marker>
          ))}
        </defs>
        {graph.edges.map((e) => {
          const c = curve(e);
          if (!c) return null;
          const selected = selection?.kind === "edge" && selection.id === e.id;
          const lit = highlight.has(e.id);
          const color = selected ? COLORS.selected : lit ? COLORS.highlight : COLORS.edge;
          const dim = highlight.size > 0 && !lit && !selected;
          const from = label(nodesById.get(e.source), e.source);
          const to = label(nodesById.get(e.target), e.target);
          return (
            <g
              key={e.id}
              role="button"
              tabIndex={0}
              aria-pressed={selected}
              aria-label={`${e.transaction_ref ?? "Transaction"}, ${from} to ${to}, ${money(e.amount, e.currency)}`}
              className="cursor-pointer focus-visible:outline-2 focus-visible:outline-select"
              opacity={dim ? 0.35 : 1}
              onClick={() => onSelect({ kind: "edge", id: e.id })}
              onKeyDown={onActivate(() => onSelect({ kind: "edge", id: e.id }))}
            >
              <path d={c.d} stroke="transparent" strokeWidth={14} fill="none" />
              <path d={c.d} stroke={color} strokeWidth={selected ? 3.5 : lit ? 3 : 1.6} strokeDasharray={lit && !selected ? "8 5" : undefined} fill="none" markerEnd={`url(#lg-arrow-${selected ? "selected" : lit ? "highlight" : "edge"})`} />
              {showLabels || lit || selected ? (
                <text x={c.lx} y={c.ly - 5} textAnchor="middle" className="pointer-events-none fill-ink-2 text-[11px] font-semibold" paintOrder="stroke" stroke="#fbfcfd" strokeWidth={3}>
                  {money(e.amount, e.currency).replace(/\.00$/, "")}
                </text>
              ) : null}
            </g>
          );
        })}
        {graph.nodes.map((n) => {
          const p = layout.pos.get(n.id);
          if (!p) return null;
          const selected = selection?.kind === "node" && selection.id === n.id;
          const isFocus = n.id === focus;
          const dim = highlight.size > 0 && !highlightedNodes.has(n.id) && !isFocus && !selected;
          return (
            <g
              key={n.id}
              role="button"
              tabIndex={0}
              aria-pressed={selected}
              aria-label={`Account ${label(n, n.id)}${isFocus ? ", focus" : ""}, ${n.degree} transactions shown`}
              className="cursor-pointer outline-none"
              opacity={dim ? 0.4 : 1}
              onClick={() => onSelect({ kind: "node", id: n.id })}
              onKeyDown={onActivate(() => onSelect({ kind: "node", id: n.id }))}
            >
              {isFocus ? <circle cx={p.x} cy={p.y} r={21} fill="none" stroke={COLORS.focus} strokeWidth={3} /> : null}
              {highlightedNodes.has(n.id) && !isFocus ? <circle cx={p.x} cy={p.y} r={20} fill="none" stroke={COLORS.highlight} strokeWidth={2.5} /> : null}
              {marked?.has(n.id) && !isFocus ? <circle cx={p.x} cy={p.y} r={23} fill="none" stroke={COLORS.highlight} strokeWidth={2} strokeDasharray="3 3" /> : null}
              {selected ? <circle cx={p.x} cy={p.y} r={25} fill="none" stroke={COLORS.selected} strokeWidth={2} strokeDasharray="4 4" /> : null}
              <circle cx={p.x} cy={p.y} r={15} fill={n.is_internal === false ? "#FFFFFF" : "#0E1A2B"} stroke="#0E1A2B" strokeWidth={n.is_internal === false ? 3 : 1.5} />
              <text x={p.x} y={p.y + 32} textAnchor="middle" className="pointer-events-none fill-ink font-mono text-[11.5px] font-bold" paintOrder="stroke" stroke="#fbfcfd" strokeWidth={3}>
                {label(n, n.id)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
