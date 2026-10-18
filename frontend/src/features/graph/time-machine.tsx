"use client";

import { useMemo, useState } from "react";
import { typologyTone } from "@/lib/domain/alert-insights";
import { modelForEdges } from "@/lib/domain/graph";
import { checkpoints, diffSnapshots, type Checkpoint, leakageViolations, snapshotAt, type PointInTimeSnapshot } from "@/lib/domain/time-machine";
import { NODES } from "@/lib/seed/graph";
import { useDemoState } from "@/lib/store/hooks";
import { Callout } from "@/components/ui/callout";
import { Card, CardHeader, Panel } from "@/components/ui/card";
import { Chip } from "@/components/ui/controls";
import { CiteList } from "@/components/ui/cite";
import { KeyValueList } from "@/components/ui/key-value";
import { Pill } from "@/components/ui/pill";
import { GraphCanvas } from "./graph-canvas";

function SnapshotCard({ title, snapshot, highlighted }: { title: string; snapshot: PointInTimeSnapshot; highlighted: string[] }) {
  const model = useMemo(
    () => modelForEdges(snapshot.visible, { highlighted: snapshot.visible.filter((k) => highlighted.includes(k)), placeholder: snapshot.held }),
    [snapshot, highlighted],
  );
  const leaks = leakageViolations(snapshot);
  return (
    <Card className="flex min-w-0 flex-[1_1_420px] flex-col gap-3 p-3.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="eyebrow">{title}</div>
          <b className="text-[15px]">As of {snapshot.checkpoint.label} UTC</b>
          <div className="text-xs text-muted">{snapshot.checkpoint.note}</div>
        </div>
        <Pill tone={leaks.length ? "bad" : "ok"}>{leaks.length ? `${leaks.length} future reference` : "Point-in-time check passed"}</Pill>
      </div>
      <GraphCanvas model={model} fixed />
      <KeyValueList items={snapshot.metrics} />
      <ul className="flex flex-col gap-1">
        {snapshot.rules.map((r) => (
          <li key={r.name} className="flex flex-wrap items-center justify-between gap-2 text-[13px]">
            <span>{r.name}</span>
            <span className="flex items-center gap-2">
              <span className="text-xs text-muted">{r.basis}</span>
              <Pill tone={typologyTone(r.result)}>{r.result}</Pill>
            </span>
          </li>
        ))}
      </ul>
      <div className="flex flex-col gap-1.5 border-t border-line-soft pt-2">
        <span className="eyebrow">Explanation as it could be written then</span>
        {snapshot.explanation.length === 0 ? <span className="text-[13px] text-muted">Nothing received yet.</span> : null}
        {snapshot.explanation.map((p) => (
          <span key={p.text} className="text-[13px]">
            {p.text} <CiteList ids={p.cites} />
          </span>
        ))}
      </div>
      {snapshot.pending.length > 0 ? (
        <span className="text-xs text-muted">
          Excluded, not yet received: {snapshot.pending.map((p) => `${p.edge.tx} (${p.reason.toLowerCase()})`).join("; ")}.
        </span>
      ) : null}
    </Card>
  );
}

function Picker({ label, points, value, onChange }: { label: string; points: Checkpoint[]; value: number; onChange: (i: number) => void }) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap items-center gap-1.5">
      <span className="w-20 text-xs text-muted">{label}</span>
      {points.map((p, i) => (
        <Chip key={p.label} pressed={i === value} className="min-h-[30px] text-[11.5px]" onClick={() => onChange(i)}>
          {p.label}
        </Chip>
      ))}
    </div>
  );
}

/** Replays the flagged network as of any instant and compares two snapshots. */
export function TimeMachine() {
  const fixed = useDemoState().data.driftFixed;
  const points = useMemo(() => checkpoints(fixed), [fixed]);
  const [aIndex, setA] = useState(5);
  const [bIndex, setB] = useState(points.length - 1);
  const a = useMemo(() => snapshotAt(points[aIndex], fixed), [points, aIndex, fixed]);
  const b = useMemo(() => snapshotAt(points[bIndex], fixed), [points, bIndex, fixed]);
  const diff = useMemo(() => diffSnapshots(a, b, fixed), [a, b, fixed]);
  const order = aIndex <= bIndex;

  return (
    <div className="flex flex-col gap-4">
      <Panel>
        <CardHeader
          title="Money-flow time machine"
          description="A snapshot holds only what the system had received by that instant, not everything that had happened. Metrics, rules and sentences are computed from that set, so later data cannot leak into an earlier explanation."
        />
        <Picker label="Snapshot A" points={points} value={aIndex} onChange={setA} />
        <Picker label="Snapshot B" points={points} value={bIndex} onChange={setB} />
        {!order ? <Callout tone="warn">Snapshot B is earlier than A. The diff below shows what A knew that B did not.</Callout> : null}
      </Panel>

      <Panel>
        <CardHeader title={`What changed from A to B`} description={`${a.checkpoint.label} → ${b.checkpoint.label} UTC`} />
        {diff.added.length === 0 && diff.metricChanges.length === 0 ? <span className="text-[13.5px] text-muted">No difference between these snapshots.</span> : null}
        {diff.added.length > 0 ? (
          <ul className="flex flex-col">
            {diff.added.map(({ edge, late, note }) => (
              <li key={edge.key} className="flex flex-wrap items-center justify-between gap-2 border-t border-line-soft py-1.5 text-[13px]">
                <span>
                  <CiteList ids={[edge.tx]} /> {NODES[edge.from].id} to {NODES[edge.to].id}
                </span>
                <span className="flex items-center gap-2">
                  <span className="text-xs text-muted">{note}</span>
                  <Pill tone={late ? "warn" : "info"}>{late ? "Became visible later" : "New since A"}</Pill>
                </span>
              </li>
            ))}
          </ul>
        ) : null}
        {diff.ruleChanges.map((r) => (
          <span key={r.name} className="text-[13px]">
            <b>{r.name}:</b> {r.before} → {r.after}
          </span>
        ))}
        {diff.metricChanges.length > 0 ? (
          <KeyValueList items={diff.metricChanges.map((m): [string, string] => [m.label, `${m.before} → ${m.after}`])} />
        ) : null}
      </Panel>

      <div className="flex flex-wrap items-start gap-4">
        <SnapshotCard title="Snapshot A" snapshot={a} highlighted={[]} />
        <SnapshotCard title="Snapshot B · blue = not known at A" snapshot={b} highlighted={b.visible.filter((k) => !a.visible.includes(k))} />
      </div>
    </div>
  );
}
