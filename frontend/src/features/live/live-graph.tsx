"use client";

import { useMemo, useState } from "react";
import type { GraphPayload } from "@/lib/live/api";
import { money, utc, words } from "@/lib/live/format";
import { KeyValueList } from "@/components/ui/key-value";
import { EmptyState } from "@/components/ui/misc";

const W = 720;
const H = 360;

/**
 * Money-flow graph for live data. Accounts are placed in columns by their first appearance in time,
 * so funds read left to right. Path tracing is investigative context, not proof of where funds went.
 */
export function LiveGraph({ graph }: { graph: GraphPayload }) {
  const [selected, setSelected] = useState<string | null>(graph.edges[0]?.id ?? null);

  const layout = useMemo(() => {
    const order = new Map<string, number>();
    // Edges arrive in time order: an account's column is one past the sender that first paid it.
    for (const e of graph.edges) {
      if (!order.has(e.source)) order.set(e.source, 0);
      if (!order.has(e.target)) order.set(e.target, order.get(e.source)! + 1);
    }
    for (const n of graph.nodes) if (!order.has(n.id)) order.set(n.id, 0);
    const columns = new Map<number, string[]>();
    for (const [id, col] of order) columns.set(col, [...(columns.get(col) ?? []), id]);
    const colCount = Math.max(1, columns.size);
    const pos = new Map<string, { x: number; y: number }>();
    [...columns.keys()].sort((a, b) => a - b).forEach((col, ci) => {
      const ids = columns.get(col)!;
      ids.forEach((id, i) => pos.set(id, { x: 60 + (ci * (W - 120)) / Math.max(1, colCount - 1), y: ((i + 1) * H) / (ids.length + 1) }));
    });
    return pos;
  }, [graph]);

  if (graph.edges.length === 0) return <EmptyState title="No transactions linked to this alert." />;

  const nodeLabel = (id: string) => graph.nodes.find((n) => n.id === id)?.account_ref ?? id.slice(0, 8);
  const edge = graph.edges.find((e) => e.id === selected) ?? null;

  return (
    <div className="flex flex-col gap-3">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full rounded-xl border border-line bg-[#fbfcfd]" role="img" aria-label="Transaction graph for this alert">
        <defs>
          <marker id="live-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill="#6b7a90" />
          </marker>
        </defs>
        {graph.edges.map((e) => {
          const a = layout.get(e.source);
          const b = layout.get(e.target);
          if (!a || !b) return null;
          const active = e.id === selected;
          return (
            <g key={e.id} className="cursor-pointer" onClick={() => setSelected(e.id)}>
              <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={active ? "var(--color-brand)" : "#9aa7b9"} strokeWidth={active ? 3 : 1.6} markerEnd="url(#live-arrow)" />
              <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 6} textAnchor="middle" className="fill-ink-2 text-[11px] font-semibold">
                {money(e.amount, e.currency).replace(/\.00$/, "")}
              </text>
            </g>
          );
        })}
        {graph.nodes.map((n) => {
          const p = layout.get(n.id);
          if (!p) return null;
          return (
            <g key={n.id}>
              <circle cx={p.x} cy={p.y} r={n.role === "focus" || n.role === "primary" ? 15 : 11} fill={n.role ? "var(--color-attention)" : "#fff"} stroke="#0e1a2b" strokeWidth={1.5} />
              <text x={p.x} y={p.y + 28} textAnchor="middle" className="fill-ink font-mono text-[11px]">
                {nodeLabel(n.id)}
              </text>
            </g>
          );
        })}
      </svg>
      {graph.truncated ? <p className="text-xs text-muted">The graph was capped at the server edge limit; not every edge is shown.</p> : null}
      {edge ? (
        <KeyValueList
          items={[
            ["Transaction", <span key="t" className="font-mono">{edge.transaction_ref}</span>],
            ["From", nodeLabel(edge.source)],
            ["To", nodeLabel(edge.target)],
            ["Amount", money(edge.amount, edge.currency)],
            ["Rail", words(edge.channel)],
            ["Time", utc(edge.occurred_at)],
          ]}
        />
      ) : null}
    </div>
  );
}
