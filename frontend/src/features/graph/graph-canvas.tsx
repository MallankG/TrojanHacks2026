"use client";

import type { GraphModel } from "@/lib/domain/graph";
import { cn } from "@/lib/cn";
import { formatMoneyShort } from "@/lib/format";
import { useMaskers } from "@/lib/privacy/store";
import { GRAPH_VIEWBOX, NODES } from "@/lib/seed/graph";
import { selectGraphNode, updateGraph } from "@/lib/store/actions";
import type { EdgeKey, GraphNode, NodeKey } from "@/lib/types";
import { MaskedRef } from "@/components/ui/sensitive";

const COLORS = {
  detected: "#DC2626", // Red lines for detected laundering path
  selected: "#2563EB", // Vibrant Royal Blue when selected
  hold: "#D97706",     // Amber dashed for quarantined batches
  side: "#7C3AED",     // Violet for device-linked side splits
  normal: "#64748B",
};

const DETECTED_EDGES = new Set<EdgeKey>(["e1", "e2", "e3", "e4", "e5", "e6"]);
const DETECTED_NODES = new Set<NodeKey>(["A", "B", "C", "D", "E", "F"]);

const pct = (value: number, total: number) => `${((value / total) * 100).toFixed(2)}%`;

function NodeShape({ node, opacity, isSelected }: { node: GraphNode; opacity: number; isSelected: boolean }) {
  const { x, y } = node;
  
  // Highlighting colors for investigating:
  // - Node B: Seed alerted account (Amber/Gold with vivid crimson pulse ring)
  // - Nodes C, D, E: Layering intermediaries in detected path (Vibrant alert crimson & coral)
  // - Node F: Cross-border exit target (Thick crimson border)
  // - Node A: Inflow source (Deep navy with alert border)
  // - Node G: Device collusion account (Purple)
  // - Node T: Device token (Hexagon gold)
  switch (node.shape) {
    case "square": {
      const isDetected = DETECTED_NODES.has(node.key);
      const fill = node.key === "E" ? "#B91C1C" : isDetected ? "#1E293B" : "#1F4F8F";
      return (
        <g opacity={opacity} filter="url(#node-shadow)">
          <rect
            x={x - 22}
            y={y - 22}
            width={44}
            height={44}
            rx={8}
            fill={fill}
            stroke={isDetected ? "#DC2626" : "#3B82F6"}
            strokeWidth={isDetected ? 2.5 : 1.5}
          />
          {node.key === "E" ? (
            <text x={x} y={y + 4} textAnchor="middle" fill="#FFFFFF" fontSize="10" fontWeight="bold">HOP 3</text>
          ) : null}
        </g>
      );
    }
    case "diamond": {
      // Node D: Intermediate hop 2 (Prepaid wallet)
      return (
        <g opacity={opacity} filter="url(#node-shadow)">
          <polygon
            points={`${x},${y - 26} ${x + 26},${y} ${x},${y + 26} ${x - 26},${y}`}
            fill="#EA580C"
            stroke="#DC2626"
            strokeWidth={2.5}
          />
          <text x={x} y={y + 3.5} textAnchor="middle" fill="#FFFFFF" fontSize="9" fontWeight="bold">HOP 2</text>
        </g>
      );
    }
    case "open": {
      // Node F: Cross-border exit account
      return (
        <g opacity={opacity} filter="url(#node-shadow)">
          <circle cx={x} cy={y} r={23} fill="#FEF2F2" stroke="#DC2626" strokeWidth={4} />
          <circle cx={x} cy={y} r={17} fill="none" stroke="#DC2626" strokeWidth={1.5} strokeDasharray="3 3" />
          <circle cx={x} cy={y} r={6} fill="#DC2626" />
        </g>
      );
    }
    case "hex": {
      // Node T: Device token
      return (
        <g opacity={opacity} filter="url(#node-shadow)">
          <polygon
            points={`${x},${y - 22} ${x + 19},${y - 11} ${x + 19},${y + 11} ${x},${y + 22} ${x - 19},${y + 11} ${x - 19},${y - 11}`}
            fill="#D97706"
            stroke="#F59E0B"
            strokeWidth={2}
          />
        </g>
      );
    }
    case "circle":
    default: {
      if (node.key === "B") {
        // Seed alerted account: ACC-2208 (Amber core with prominent crimson pulse aura)
        return (
          <g opacity={opacity} filter="url(#detected-glow)">
            {/* Outer alert beacon ring */}
            <circle cx={x} cy={y} r={33} fill="none" stroke="#DC2626" strokeWidth={2.5} strokeDasharray="5 3" />
            <circle cx={x} cy={y} r={25} fill="#D97706" stroke="#FEF3C7" strokeWidth={2.5} />
            <text x={x} y={y + 4} textAnchor="middle" fill="#FFFFFF" fontSize="10" fontWeight="bold">SEED</text>
          </g>
        );
      }
      if (node.key === "C") {
        // Node C: Layering hop 1
        return (
          <g opacity={opacity} filter="url(#node-shadow)">
            <circle cx={x} cy={y} r={23} fill="#EF4444" stroke="#B91C1C" strokeWidth={2.5} />
            <text x={x} y={y + 4} textAnchor="middle" fill="#FFFFFF" fontSize="9.5" fontWeight="bold">HOP 1</text>
          </g>
        );
      }
      if (node.key === "G") {
        // Node G: Collusion / Shared device checking account
        return (
          <g opacity={opacity} filter="url(#node-shadow)">
            <circle cx={x} cy={y} r={22} fill="#7C3AED" stroke="#C4B5FD" strokeWidth={2} />
          </g>
        );
      }
      return (
        <g opacity={opacity}>
          <circle cx={x} cy={y} r={22} fill="#0E1A2B" />
        </g>
      );
    }
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
    <div className="relative w-full overflow-hidden rounded-xl border border-red-500/20 bg-[#fafbfc] shadow-sm" style={{ aspectRatio: `${width} / ${height}` }}>
      <svg role="img" aria-label="Money-flow graph for CASE-0412. Accounts are nodes; transfers are arrows. Use the Table view for the same information." viewBox={`0 0 ${width} ${height}`} className="absolute inset-0 h-full w-full">
        <defs>
          <filter id="detected-glow" x="-30%" y="-30%" width="160%" height="160%">
            <feDropShadow dx="0" dy="0" stdDeviation="3.5" floodColor="#DC2626" floodOpacity="0.55" />
          </filter>
          <filter id="node-shadow" x="-20%" y="-20%" width="140%" height="140%">
            <feDropShadow dx="0" dy="2" stdDeviation="2.5" floodColor="#0F172A" floodOpacity="0.2" />
          </filter>
          <marker id="arrow-detected" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7.5" markerHeight="7.5" orient="auto-start-reverse">
            <path d="M0 0L10 5L0 10z" fill={COLORS.detected} />
          </marker>
          <marker id="arrow-selected" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
            <path d="M0 0L10 5L0 10z" fill={COLORS.selected} />
          </marker>
          <marker id="arrow-hold" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0 0L10 5L0 10z" fill={COLORS.hold} />
          </marker>
          <marker id="arrow-side" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0 0L10 5L0 10z" fill={COLORS.side} />
          </marker>
        </defs>
        <g opacity={model.tokenVisible ? 1 : 0}>
          <line x1={b.x} y1={b.y + 28} x2={token.x} y2={token.y - 24} stroke={COLORS.hold} strokeWidth={2.5} strokeDasharray="3 6" strokeLinecap="round" />
          <line x1={token.x + 21} y1={token.y + 6} x2={g.x - 25} y2={g.y - 7} stroke={COLORS.hold} strokeWidth={2.5} strokeDasharray="3 6" strokeLinecap="round" />
        </g>
        {edgeKeys.map((key) => {
          const e = model.edges[key];
          const isDetected = DETECTED_EDGES.has(key);
          const dashed = e.placeholder || Boolean(e.edge.cycle);
          const color = e.selected
            ? COLORS.selected
            : dashed
            ? COLORS.hold
            : key === "e7"
            ? COLORS.side
            : isDetected
            ? COLORS.detected
            : COLORS.normal;
          const marker = e.selected
            ? "url(#arrow-selected)"
            : dashed
            ? "url(#arrow-hold)"
            : key === "e7"
            ? "url(#arrow-side)"
            : "url(#arrow-detected)";
          const common = {
            stroke: color,
            strokeWidth: e.selected ? 5.5 : isDetected ? 3.8 : 2.5,
            strokeDasharray: dashed ? "9 7" : undefined,
            markerEnd: marker,
            opacity: e.visible ? 1 : 0,
            fill: "none",
            filter: isDetected && !dashed ? "url(#detected-glow)" : undefined,
          };
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
              <circle cx={node.x} cy={node.y} r={key === "T" ? 32 : 36} fill="none" stroke={COLORS.selected} strokeWidth={2.5} strokeDasharray="4 4" opacity={render.selected ? 1 : 0} />
              <NodeShape node={node} opacity={render.opacity} isSelected={render.selected} />
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
