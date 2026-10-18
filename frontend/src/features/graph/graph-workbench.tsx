"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { buildGraphModel, edgeFlags, pathSummary } from "@/lib/domain/graph";
import { formatMoney } from "@/lib/format";
import { useMaskers } from "@/lib/privacy/store";
import { ROUTES } from "@/lib/routes";
import { ENTITIES } from "@/lib/seed/entities";
import { edgeByKey, NODES, TIMELINE, TIMELINE_MAX } from "@/lib/seed/graph";
import { openEvidence, pinMany, saveEvidenceSubgraph, setCopilotFocus, setGraphLens, toast, toggleExpand, toggleOverlay, togglePin, toggleRail, updateGraph } from "@/lib/store/actions";
import type { GraphView } from "@/lib/store/state";
import { useDemoState } from "@/lib/store/hooks";
import type { Rail } from "@/lib/types";
import { Button, buttonClasses } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Card, Panel } from "@/components/ui/card";
import { Chip, Segmented } from "@/components/ui/controls";
import { KeyValueList } from "@/components/ui/key-value";
import { EmptyState } from "@/components/ui/misc";
import { PageHeader } from "@/components/ui/page-header";
import { Pill } from "@/components/ui/pill";
import { MaskedRef, SensitiveRef, SensitiveText } from "@/components/ui/sensitive";
import { DataTable, Td, Th, Tr } from "@/components/ui/table";
import { useFlag } from "@/components/shell/feature-gate";
import { GraphCanvas } from "./graph-canvas";
import { Storyboard } from "./storyboard";
import { TimeMachine } from "./time-machine";

const PLAY_INTERVAL_MS = 850;
const RAILS: Rail[] = ["ACH", "P2P", "Wire"];

function Toolbar({ view, disabled }: { view: GraphView; disabled: boolean }) {
  return (
    <Card className="flex flex-wrap items-center gap-x-4 gap-y-3 px-4 py-3">
      <div className="flex items-center gap-1.5">
        <span className="text-xs text-muted">Depth</span>
        <Segmented<GraphView["depth"]> label="Hop depth" value={view.depth} disabled={disabled} onChange={(depth) => updateGraph({ depth })} options={[2, 3, 4, 5].map((d) => ({ value: d as GraphView["depth"], label: `${d} hops` }))} />
      </div>
      <div className="flex items-center gap-1.5">
        <span className="text-xs text-muted">Direction</span>
        <Segmented<GraphView["direction"]>
          label="Direction"
          value={view.direction}
          disabled={disabled}
          onChange={(direction) => updateGraph({ direction })}
          options={[
            { value: "back", label: "Backward" },
            { value: "both", label: "Both" },
            { value: "fwd", label: "Forward" },
          ]}
        />
      </div>
      <div role="group" aria-label="Rails" className="flex items-center gap-1.5">
        <span className="text-xs text-muted">Rails</span>
        {RAILS.map((rail) => (
          <Chip key={rail} pressed={view.rails[rail]} onClick={() => toggleRail(rail)}>
            {rail}
          </Chip>
        ))}
      </div>
      <div className="flex items-center gap-1.5">
        <span className="text-xs text-muted">Amount</span>
        <Segmented<GraphView["minAmount"]>
          label="Minimum amount"
          value={view.minAmount}
          onChange={(minAmount) => updateGraph({ minAmount })}
          options={[
            { value: 0, label: "Any amount" },
            { value: 10000, label: "At least $10k" },
            { value: 40000, label: "At least $40k" },
          ]}
        />
      </div>
      <div role="group" aria-label="Overlays" className="flex items-center gap-1.5">
        <span className="text-xs text-muted">Overlays</span>
        <Chip pressed={view.overlays.cycle} onClick={() => toggleOverlay("cycle")}>
          Round-trip cycle
        </Chip>
        <Chip pressed={view.overlays.token} onClick={() => toggleOverlay("token")}>
          Shared tokens
        </Chip>
      </div>
      <span className="basis-full font-mono text-[11.5px] text-muted">Query guard: max_hops {view.depth} · window Oct 6 to 9 · max 200 nodes · timeout 10 s · case scope CASE-0412</span>
    </Card>
  );
}

function TimelineScrubber({ time, disabled }: { time: number; disabled: boolean }) {
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    // Replay depends on the graph service; an outage stops it.
    if (!playing || disabled) return;
    const timer = setInterval(() => {
      const next = Math.min(TIMELINE_MAX, time + 1);
      updateGraph({ time: next });
      if (next >= TIMELINE_MAX) setPlaying(false);
    }, PLAY_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [playing, disabled, time]);

  const isPlaying = playing && !disabled;

  const togglePlay = () => {
    if (disabled) {
      toast("Replay needs the graph service. Saved snapshots are still available.");
      return;
    }
    if (isPlaying) {
      setPlaying(false);
      return;
    }
    if (time >= TIMELINE_MAX) updateGraph({ time: 0 });
    setPlaying(true);
  };

  return (
    <div className="flex flex-col gap-2 border-t border-line-soft pt-3">
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="dark" size="sm" onClick={togglePlay} disabled={disabled}>
          {isPlaying ? "Pause" : "Play"}
        </Button>
        <label className="flex flex-[1_1_300px] items-center gap-2.5">
          <span className="text-xs text-muted">Time</span>
          <input
            type="range"
            min={0}
            max={TIMELINE_MAX}
            step={1}
            value={time}
            aria-label="Timeline position"
            aria-valuetext={`Up to ${TIMELINE[time]} UTC`}
            className="flex-1"
            onChange={(e) => {
              setPlaying(false);
              updateGraph({ time: Number(e.target.value) });
            }}
          />
        </label>
        <Pill tone="info" mono>
          Up to {TIMELINE[time]} UTC
        </Pill>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {TIMELINE.map((label, i) => (
          <Chip
            key={label}
            pressed={i === time}
            className="min-h-[30px] text-[11.5px]"
            onClick={() => {
              setPlaying(false);
              updateGraph({ time: i });
            }}
          >
            {label}
          </Chip>
        ))}
      </div>
    </div>
  );
}

function Inspector({ view, fixed, graphDown }: { view: GraphView; fixed: boolean; graphDown: boolean }) {
  const router = useRouter();

  if (view.selection.kind === "node") {
    const key = view.selection.key;
    const node = NODES[key];
    const entity = key === "T" ? null : ENTITIES[key];
    const fields: Array<[string, string]> = entity
      ? [
          ["Account", node.id],
          ["Type", node.kind],
          ["Role", node.role],
          ["Opened", entity.opened],
          ["Observed", entity.observed[0]],
          ["Flow ratio", entity.flowRatio],
        ]
      : [
          ["Token", "DV-77"],
          ["Linked accounts", "ACC-2208, ACC-3391"],
          ["Logins", "4 on Oct 5 to 6"],
          ["Caution", "Households also share devices"],
        ];
    return (
      <Panel>
        <div className="eyebrow">Selected node</div>
        <h2 className="font-mono text-base font-semibold"><SensitiveRef value={node.id} /></h2>
        <KeyValueList items={fields} />
        <div className="flex flex-wrap gap-2">
          {key === "B" && !graphDown ? (
            <Button variant="ghost" size="sm" onClick={toggleExpand}>
              {view.expanded ? "Collapse neighbors" : "Expand neighbors"}
            </Button>
          ) : null}
          <Link href={ROUTES.entity(entity ? node.id : "ACC-3391")} className={buttonClasses("ghost", "sm")}>
            Open entity profile
          </Link>
        </div>
      </Panel>
    );
  }

  const edge = edgeByKey(view.selection.key);
  const held = edge.key === "e3" && !fixed;
  const pinned = view.pinned.includes(edge.tx);
  return (
    <Panel>
      <div className="eyebrow">Selected transfer</div>
      <h2 className="font-mono text-base font-semibold">{edge.tx}</h2>
      {held ? <Callout tone="warn">Raw values from a quarantined batch. Not normalized yet.</Callout> : null}
      <KeyValueList
        items={[
          ["Transaction", edge.tx],
          ["From", NODES[edge.from].id],
          ["To", NODES[edge.to].id],
          ["Amount", held ? `${formatMoney(edge.amount)} (raw, not normalized)` : `${formatMoney(edge.amount)} USD`],
          ["Rail", `${edge.rail}${edge.crossBorder ? ", cross-border (synthetic)" : ""}`],
          ["Initiated", `${edge.initiated} UTC`],
          ["Settled", `${edge.settled} UTC`],
          ["Time gap", edge.gap],
          ["Hop from seed", edge.hop === -1 ? "1 backward" : `${edge.hop} forward`],
          ["Source", `${edge.batch}, row ${edge.row}`],
          ["Ingestion run", `IR-${(edge.row % 97) + 400}`],
          ["Lineage ref", `LN-${edge.tx.slice(3)}`],
        ]}
      />
      <div className="flex flex-wrap gap-1.5">
        {edgeFlags(edge.key, fixed).map((flag) => (
          <Pill key={flag} tone="info">
            {flag}
          </Pill>
        ))}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button variant="ghost" size="sm" onClick={() => openEvidence(edge.tx)}>
          Open source row
        </Button>
        <Button variant="ghost" size="sm" onClick={() => togglePin(edge.tx)}>
          {pinned ? "Unpin from evidence" : "Pin to evidence"}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            setCopilotFocus(edge.tx);
            toast(`Copilot context refreshed: ${edge.tx} is now selected.`);
            router.push(ROUTES.copilot);
          }}
        >
          Ask copilot
        </Button>
      </div>
    </Panel>
  );
}

export function GraphWorkbench() {
  const mask = useMaskers();
  const state = useDemoState();
  const view = state.graph;
  const fixed = state.data.driftFixed;
  const graphDown = state.sim.graph;
  const model = useMemo(() => buildGraphModel(view, fixed), [view, fixed]);
  const cycleOn = view.overlays.cycle;
  const timeMachineOn = useFlag("timeMachine");
  const storyboardOn = useFlag("storyboard");
  const diffOn = useFlag("snapshotDiff");
  // A lens whose flag was turned off falls back to the explorer.
  const lens = (view.lens === "timeMachine" && timeMachineOn) || (view.lens === "storyboard" && storyboardOn) ? view.lens : "none";
  const lensOptions: Array<{ value: GraphView["lens"]; label: string }> = [
    { value: "none", label: "Explore" },
    ...(timeMachineOn ? [{ value: "timeMachine" as const, label: "Time machine" }] : []),
    ...(storyboardOn ? [{ value: "storyboard" as const, label: "Storyboard" }] : []),
  ];

  return (
    <>
      <PageHeader
        title="Graph investigation workbench"
        description="Seed ACC-2208. Time-respecting paths only: each hop happens after the one before it."
        actions={
          <>
            {lensOptions.length > 1 ? <Segmented<GraphView["lens"]> label="Lens" value={lens} onChange={setGraphLens} options={lensOptions} /> : null}
            {lens === "none" ? (
              <>
                <Segmented<GraphView["view"]>
                  label="View"
                  value={view.view}
                  onChange={(next) => updateGraph({ view: next })}
                  options={[
                    { value: "graph", label: "Graph" },
                    { value: "table", label: "Table" },
                  ]}
                />
                {diffOn ? (
                  <Button variant="ghost" size="sm" aria-pressed={view.compare} onClick={() => updateGraph({ compare: !view.compare })}>
                    {view.compare ? "Hide snapshot diff" : "Compare snapshots"}
                  </Button>
                ) : null}
                <Button size="sm" onClick={saveEvidenceSubgraph}>
                  Save evidence subgraph
                </Button>
              </>
            ) : null}
          </>
        }
      />
      {lens === "timeMachine" ? <TimeMachine /> : null}
      {lens === "storyboard" ? <Storyboard /> : null}
      {lens === "none" ? (
        <>
          <Toolbar view={view} disabled={graphDown} />

          {view.compare && diffOn ? (
            <Panel>
              <div className="flex flex-wrap items-center gap-2.5">
                <h2 className="text-base font-semibold">Snapshot comparison</h2>
                <Pill tone="mute">GS-0412-a · before fix</Pill>
                <span aria-hidden="true">→</span>
                <Pill tone="info">{fixed ? "GS-0412-b · after fix" : "GS-0412-b · not created yet"}</Pill>
              </div>
              <KeyValueList
                items={
                  fixed
                    ? [
                        ["Edges", "+1 TX-88237 (ACC-3315 to ACC-4471)"],
                        ["Reachable accounts", "4 to 8"],
                        ["AL-1027 XGBoost", "0.71 to 0.91"],
                        ["AL-1027 priority", "Review next to Review first"],
                        ["Graph version", "v183 to v184"],
                      ]
                    : [["Status", "No newer snapshot yet. Fix the data hold to create GS-0412-b."]]
                }
              />
            </Panel>
          ) : null}

          <div className="flex flex-wrap items-start gap-4">
            <Card className="flex min-w-0 flex-[3_1_600px] flex-col gap-3 p-3.5">
              {view.view === "graph" ? (
                <>
                  <GraphCanvas model={model} fixed={fixed} />
                  <div className="flex flex-wrap gap-3.5 text-xs font-semibold text-ink-2">
                    <span>■ Business account</span>
                    <span>● Personal account (amber ring = seed)</span>
                    <span>◆ Prepaid wallet</span>
                    <span>○ External bank</span>
                    <span>⬢ Device token</span>
                    <span>Dashed amber = on hold or round-trip lead</span>
                  </div>
                </>
              ) : (
                <>
                  <DataTable caption="Visible transfers">
                    <thead>
                      <tr>
                        <Th>Transaction</Th>
                        <Th>From</Th>
                        <Th>To</Th>
                        <Th>Amount</Th>
                        <Th>Rail</Th>
                        <Th>Time (UTC)</Th>
                        <Th>Gap</Th>
                      </tr>
                    </thead>
                    <tbody>
                      {model.tableRows.map(({ edge, held }) => {
                        const selected = view.selection.kind === "edge" && view.selection.key === edge.key;
                        return (
                          <Tr key={edge.key} selected={selected}>
                            <Td>
                              <button type="button" aria-pressed={selected} className={buttonClasses("link", "md", "font-mono")} onClick={() => updateGraph({ selection: { kind: "edge", key: edge.key } })}>
                                {edge.tx}
                              </button>
                            </Td>
                            <Td className="font-mono"><MaskedRef value={NODES[edge.from].id} /></Td>
                            <Td className="font-mono"><MaskedRef value={NODES[edge.to].id} /></Td>
                            <Td>{held ? "On hold" : formatMoney(edge.amount)}</Td>
                            <Td>{edge.rail}</Td>
                            <Td>{edge.initiated}</Td>
                            <Td className="text-xs text-muted">{edge.gap}</Td>
                          </Tr>
                        );
                      })}
                    </tbody>
                  </DataTable>
                  {model.tableRows.length === 0 ? <EmptyState title="No transfers match the current filters." /> : null}
                </>
              )}
              <TimelineScrubber time={view.time} disabled={graphDown} />
              {!fixed ? (
                <Callout tone="warn" action={<Link href={ROUTES.data} className={buttonClasses("link")}>Resolve the data hold</Link>}>
                  The path breaks at ACC-3315. Its next transfer is in quarantined batch ach_2026_10_06_b.
                </Callout>
              ) : null}
              <Callout tone="info" title="Investigative context, not proof.">
                Money in a shared balance is mixed, so a path shows where funds plausibly moved. Cycle search is bounded (3 to 10 hops, 7 days) and never claims to be complete.
              </Callout>
            </Card>

            <div className="flex flex-[1_1_320px] flex-col gap-4">
              <Inspector view={view} fixed={fixed} graphDown={graphDown} />
              <Panel>
                <h2 className="text-base font-semibold">Path summary</h2>
                <KeyValueList items={pathSummary(model, fixed)} />
                {cycleOn && fixed ? (
                  <Callout tone="warn" title="Possible round trip (lead only).">
                    6 hops, 3 d 3 h 33 m elapsed, repeated once. $12,000.00 returned to ACC-1104 of $58,200.00 sent.
                  </Callout>
                ) : null}
                {cycleOn && !fixed ? <Callout tone="mute">Cycle search needs the complete path. Resolve the data hold first.</Callout> : null}
              </Panel>
              <Panel>
                <div className="flex items-center justify-between gap-2">
                  <h2 className="text-base font-semibold">Pinned evidence</h2>
                  <Button variant="ghost" size="sm" onClick={() => pinMany(model.visiblePath.map((e) => e.tx))}>
                    Pin visible path
                  </Button>
                </div>
                {view.pinned.length === 0 ? <span className="text-xs text-muted">Nothing pinned yet.</span> : null}
                <div className="flex flex-wrap gap-1.5">
                  {view.pinned.map((id) => (
                    <Pill key={id} tone="info" mono>
                      <SensitiveText value={id} />
                      <button type="button" aria-label={`Unpin ${mask.text(id)}`} onClick={() => togglePin(id)} className="min-h-5 px-0.5 font-bold">
                        ×
                      </button>
                    </Pill>
                  ))}
                </div>
              </Panel>
            </div>
          </div>
        </>
      ) : null}
    </>
  );
}
