"use client";

import type { GraphModel } from "@/lib/domain/graph";
import { cn } from "@/lib/cn";
import { formatMoneyShort } from "@/lib/format";
import { useMaskers } from "@/lib/privacy/store";
import { GRAPH_VIEWBOX, NODES } from "@/lib/seed/graph";
import { selectGraphNode, updateGraph } from "@/lib/store/actions";
import type { EdgeKey, GraphNode, NodeKey } from "@/lib/types";
import { MaskedRef } from "@/components/ui/sensitive";

const COLORS = { path: "#0B7A70", selected: "#1F4F8F", hold: "#B8860B" };

const pct = (value: number, total: number) => `${((value / total) * 100).toFixed(2)}%`;

function NodeShape({ node, opacity }: { node: GraphNode; opacity: number }) {
  const { x, y } = node;
  switch (node.shape) {
    case "square":
      return <rect x={x - 22} y={y - 22} width={44} height={44} rx={8} fill="#1F4F8F" opacity={opacity} />;
    case "diamond":
      return <polygon points={`${x},${y - 26} ${x + 26},${y} ${x},${y + 26} ${x - 26},${y}`} fill="#0B7A70" opacity={opacity} />;
    case "open":
      return <circle cx={x} cy={y} r={22} fill="#FFFFFF" stroke="#0E1A2B" strokeWidth={4} opacity={opacity} />;
    case "hex":
      return <polygon points={`${x},${y - 22} ${x + 19},${y - 11} ${x + 19},${y + 11} ${x},${y + 22} ${x - 19},${y + 11} ${x - 19},${y - 11}`} fill="#F2B84B" opacity={opacity} />;
    case "circle":
    default:
      return (
        <g opacity={opacity}>
          <circle cx={x} cy={y} r={node.key === "B" ? 24 : 22} fill="#0E1A2B" />
          {node.key === "B" ? <circle cx={x} cy={y} r={30} fill="none" stroke="#F2B84B" strokeWidth={3} /> : null}
        </g>
      );
  }
}

/** SVG draws shapes; HTML buttons on top make every node and edge keyboard-selectable. */
export function GraphCanvas({ model, fixed }: { model: GraphModel; fixed: boolean }) {
  const { width, height } = GRAPH_VIEWBOX;
  const mask = useMaskers();
  const edgeKeys = Object.keys(model.edges) as EdgeKey[];
  const nodeKeys = Object.keys(NODES) as NodeKey[];
  const token = NODES.T;
  const g = NODES.G;
  const b = NODES.B;

  return (
    <div className="relative w-full overflow-hidden rounded-xl bg-[#fafbfc]" style={{ aspectRatio: `${width} / ${height}` }}>
      <svg role="img" aria-label="Money-flow graph for CASE-0412. Accounts are nodes; transfers are arrows. Use the Table view for the same information." viewBox={`0 0 ${width} ${height}`} className="absolute inset-0 h-full w-full">
        <defs>
          {Object.entries(COLORS).map(([name, color]) => (
            <marker key={name} id={`arrow-${name}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0 0L10 5L0 10z" fill={color} />
            </marker>
          ))}
        </defs>
        <g opacity={model.tokenVisible ? 1 : 0}>
          <line x1={b.x} y1={b.y + 28} x2={token.x} y2={token.y - 24} stroke={COLORS.hold} strokeWidth={2.5} strokeDasharray="3 6" strokeLinecap="round" />
          <line x1={token.x + 21} y1={token.y + 6} x2={g.x - 25} y2={g.y - 7} stroke={COLORS.hold} strokeWidth={2.5} strokeDasharray="3 6" strokeLinecap="round" />
        </g>
        {edgeKeys.map((key) => {
          const e = model.edges[key];
          const dashed = e.placeholder || Boolean(e.edge.cycle);
          const color = dashed ? COLORS.hold : e.selected ? COLORS.selected : COLORS.path;
          const marker = dashed ? "url(#arrow-hold)" : e.selected ? "url(#arrow-selected)" : "url(#arrow-path)";
          const common = { stroke: color, strokeWidth: e.selected ? 5 : 3, strokeDasharray: dashed ? "9 7" : undefined, markerEnd: marker, opacity: e.visible ? 1 : 0, fill: "none" };
          if (e.edge.cycle) {
            const from = NODES[e.edge.from];
            const to = NODES[e.edge.to];
            return <path key={key} d={`M${from.x} ${from.y + 30} C ${from.x + 60} 430 ${to.x + 50} 445 ${to.x + 6} ${to.y + 32}`} {...common} />;
          }
          return <line key={key} x1={e.x1} y1={e.y1} x2={e.x2} y2={e.y2} {...common} />;
        })}
        {nodeKeys.map((key) => {
          const node = NODES[key];
          const render = model.nodes[key];
          return (
            <g key={key}>
              <circle cx={node.x} cy={node.y} r={key === "T" ? 32 : 36} fill="none" stroke={COLORS.selected} strokeWidth={2} strokeDasharray="4 4" opacity={render.selected ? 1 : 0} />
              <NodeShape node={node} opacity={render.opacity} />
            </g>
          );
        })}
      </svg>

      {edgeKeys.map((key) => {
        const e = model.edges[key];
        if (!e.visible) return null;
        const from = NODES[e.edge.from];
        const to = NODES[e.edge.to];
        const x = e.edge.cycle ? 575 : (from.x + to.x) / 2;
        const y = e.edge.cycle ? 383 : (from.y + to.y) / 2;
        return (
          <button
            key={key}
            type="button"
            aria-pressed={e.selected}
            aria-label={`${e.edge.tx}, ${mask.ref(from.id)} to ${mask.ref(to.id)}`}
            onClick={() => updateGraph({ selection: { kind: "edge", key } })}
            className={cn(
              "absolute flex min-h-8 -translate-x-1/2 -translate-y-1/2 flex-col justify-center whitespace-nowrap rounded-lg border bg-white px-2 py-0.5 text-center text-[11.5px] font-semibold leading-tight",
              e.selected ? "border-2 border-select bg-select-tint" : "border-line-strong hover:border-select",
            )}
            style={{ left: pct(x, width), top: pct(y, height) }}
          >
            <span>{e.placeholder ? "On hold" : formatMoneyShort(e.edge.amount)}</span>
            <span className="text-[10.5px] font-normal text-muted">{e.placeholder ? "Batch quarantined" : e.edge.initiated}</span>
          </button>
        );
      })}

      {nodeKeys.map((key) => {
        const node = NODES[key];
        const render = model.nodes[key];
        if (render.opacity === 0) return null;
        const above = node.y < 200;
        return (
          <button
            key={key}
            type="button"
            aria-pressed={render.selected}
            onClick={() => selectGraphNode(key)}
            className={cn("absolute flex min-h-[26px] -translate-x-1/2 flex-col items-center whitespace-nowrap rounded-md px-1.5 py-0.5 font-mono text-[11.5px] font-bold leading-tight", render.selected ? "bg-ink text-white" : "bg-white/85")}
            style={{ left: pct(node.x, width), top: pct(above ? node.y - (render.unreached && !fixed ? 74 : 62) : node.y + 30, height) }}
          >
            <MaskedRef value={node.id} />
            {render.unreached && !fixed ? <span className="font-sans text-[10.5px] font-medium">not reached</span> : null}
          </button>
        );
      })}
    </div>
  );
}
