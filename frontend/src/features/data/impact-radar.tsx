"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { cn } from "@/lib/cn";
import { blastRadius, impactItems, impactSources, RING_OF, type ImpactItem } from "@/lib/domain/impact-radar";
import { useDemoState, useScoredAlerts } from "@/lib/store/hooks";
import { FeatureGate } from "@/components/shell/feature-gate";
import { buttonClasses } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { CardHeader, Panel } from "@/components/ui/card";
import { OptionCard } from "@/components/ui/controls";
import { KeyValueList } from "@/components/ui/key-value";
import { Pill } from "@/components/ui/pill";

const SIZE = 420;
const CENTER = SIZE / 2;
const RING_RADIUS = { 1: 70, 2: 125, 3: 180 } as const;
const RING_LABEL = { 1: "Features and links", 2: "Alerts and models", 3: "Cases and reports" } as const;
const STATUS_COLOR: Record<string, string> = { warn: "#B8860B", ok: "#0B7A70", info: "#1F4F8F", bad: "#B42318", mute: "#8a97ab", dark: "#0E1A2B", blue: "#1F4F8F" };

function placements(items: ImpactItem[]) {
  return ([1, 2, 3] as const).flatMap((ring) => {
    const inRing = items.filter((i) => RING_OF[i.category] === ring);
    // Offset each ring so labels on neighboring rings do not line up.
    const offset = ring * 0.6;
    return inRing.map((item, i) => {
      const angle = offset + (i / Math.max(inRing.length, 1)) * Math.PI * 2;
      return { item, x: CENTER + Math.cos(angle) * RING_RADIUS[ring], y: CENTER + Math.sin(angle) * RING_RADIUS[ring] };
    });
  });
}

/** Blast radius of one source-data issue, drawn as rings: what it feeds, what that feeds, and so on. */
export function ImpactRadar() {
  const state = useDemoState();
  const scored = useScoredAlerts();
  const sources = useMemo(() => impactSources(state), [state]);
  const [sourceKey, setSourceKey] = useState(sources[0].key);
  const source = sources.find((s) => s.key === sourceKey) ?? sources[0];
  const items = useMemo(() => impactItems(state, source), [state, source]);
  const radius = blastRadius(source, items, scored);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = items.find((i) => i.id === selectedId) ?? null;
  const placed = placements(items);

  return (
    <FeatureGate flag="impactRadar">
      <div className="flex flex-wrap items-start gap-4">
        <Panel className="flex-[1_1_280px]">
          <CardHeader title="Source issue" description="Late, malformed, missing or corrected records." />
          <div className="flex flex-col gap-2">
            {sources.map((s) => (
              <OptionCard
                key={s.key}
                pressed={s.key === source.key}
                onClick={() => {
                  setSourceKey(s.key);
                  setSelectedId(null);
                }}
              >
                <span className="flex flex-col">
                  <b className="text-[13px]">{s.label}</b>
                  <span className="text-xs text-muted">
                    {s.kind} · {s.field}
                  </span>
                </span>
                <Pill tone={s.resolved ? "ok" : "warn"}>{s.resolved ? "Resolved" : "Open"}</Pill>
              </OptionCard>
            ))}
          </div>
        </Panel>

        <Panel className="min-w-0 flex-[2_1_440px]">
          <CardHeader title={`Impact radar · ${source.label}`} description={source.detail} actions={<Pill tone={radius.tone}>Blast radius: {radius.level}</Pill>} />
          <div className="relative mx-auto w-full max-w-[460px]" style={{ aspectRatio: "1 / 1" }}>
            <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className="absolute inset-0 h-full w-full" role="img" aria-label={`Impact radar for ${source.label}. The list below has the same information.`}>
              {([3, 2, 1] as const).map((ring) => (
                <g key={ring}>
                  <circle cx={CENTER} cy={CENTER} r={RING_RADIUS[ring]} fill={ring === 1 ? "#f3f6f9" : "none"} stroke="#d5dce6" strokeDasharray={ring === 3 ? "4 6" : undefined} />
                  <text x={CENTER} y={CENTER - RING_RADIUS[ring] + 14} textAnchor="middle" fontSize="10" fill="#6b7789">
                    {RING_LABEL[ring]}
                  </text>
                </g>
              ))}
              {placed.map(({ item, x, y }) => (
                <line key={item.id} x1={CENTER} y1={CENTER} x2={x} y2={y} stroke={STATUS_COLOR[item.tone]} strokeOpacity={selected?.id === item.id ? 0.9 : 0.25} strokeWidth={selected?.id === item.id ? 2.5 : 1.5} />
              ))}
              <circle cx={CENTER} cy={CENTER} r={26} fill={source.resolved ? "#0B7A70" : "#B8860B"} />
              <text x={CENTER} y={CENTER + 4} textAnchor="middle" fontSize="11" fontWeight="700" fill="#fff">
                {source.key === "late-cash" ? "LATE" : source.key}
              </text>
            </svg>
            {placed.map(({ item, x, y }) => (
              <button
                key={item.id}
                type="button"
                aria-pressed={selected?.id === item.id}
                aria-label={`${item.category}: ${item.label}, ${item.status}`}
                onClick={() => setSelectedId(item.id)}
                className={cn("absolute size-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white shadow", selected?.id === item.id && "size-5 ring-2 ring-ink")}
                style={{ left: `${(x / SIZE) * 100}%`, top: `${(y / SIZE) * 100}%`, background: STATUS_COLOR[item.tone] }}
              />
            ))}
          </div>
          <span className="text-xs text-muted">Amber: stale, missing or under watch. Teal: updated. Blue: report to regenerate. Select a dot for its lineage.</span>
        </Panel>

        <div className="flex flex-[1_1_320px] flex-col gap-4">
          <Panel>
            <CardHeader title="Blast-radius estimate" />
            <p className="text-[13.5px]">{radius.summary}</p>
            <KeyValueList items={radius.facts} />
            {radius.untracked.length > 0 ? (
              <Callout tone="warn" title="Untracked lineage.">
                {radius.untracked.join("; ")}. This is an estimate, not a complete list.
              </Callout>
            ) : null}
          </Panel>
          <Panel>
            <CardHeader title={selected ? `${selected.category} · ${selected.label}` : "Drill-through lineage"} />
            {selected ? (
              <>
                <Pill tone={selected.tone} className="self-start">
                  {selected.status}
                </Pill>
                <ol aria-label="Lineage" className="flex flex-col gap-1">
                  {selected.lineage.map((step, i) => (
                    <li key={`${step}-${i}`} className="flex items-center gap-2 text-[13px]">
                      <span className="w-5 text-right text-xs text-muted">{i + 1}</span>
                      <span className={cn(i === selected.lineage.length - 1 && "font-semibold")}>{step}</span>
                    </li>
                  ))}
                </ol>
                {selected.href ? (
                  <Link href={selected.href} className={buttonClasses("ghost", "sm", "self-start")}>
                    Open {selected.category.toLowerCase()}
                  </Link>
                ) : null}
              </>
            ) : (
              <span className="text-[13.5px] text-muted">Select a dot on the radar, or an item below.</span>
            )}
          </Panel>
          <Panel>
            <CardHeader title="Affected items" description={`${items.length} recorded dependencies`} />
            {items.length === 0 ? <span className="text-[13.5px] text-muted">Nothing downstream depends on this record.</span> : null}
            <ul className="flex flex-col">
              {items.map((item) => (
                <li key={item.id} className="border-t border-line-soft">
                  <button type="button" aria-pressed={selected?.id === item.id} onClick={() => setSelectedId(item.id)} className="flex w-full items-center justify-between gap-2 py-1.5 text-left text-[13px] hover:bg-canvas">
                    <span>
                      <span className="text-xs text-muted">{item.category}:</span> {item.label}
                    </span>
                    <Pill tone={item.tone}>{item.status}</Pill>
                  </button>
                </li>
              ))}
            </ul>
          </Panel>
        </div>
      </div>
    </FeatureGate>
  );
}
