"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { outcomeTone, pinnedRuns, replaySteps, replaySummary, type ReplayPin } from "@/lib/domain/replay";
import { recordReplay } from "@/lib/store/actions";
import { useDemoState, useRole } from "@/lib/store/hooks";
import { FeatureGate } from "@/components/shell/feature-gate";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Card, CardHeader, Panel } from "@/components/ui/card";
import { OptionCard } from "@/components/ui/controls";
import { KeyValueList } from "@/components/ui/key-value";
import { Pill } from "@/components/ui/pill";
import { DataTable, Td, Th } from "@/components/ui/table";

const STEP_MS = 280;

/** Re-runs the bounded tools of a past run against its pinned versions and reports what reproduced. */
export function ReplayView() {
  const state = useDemoState();
  const role = useRole();
  const runs = useMemo(() => pinnedRuns(state), [state]);
  const [pin, setPin] = useState<ReplayPin>("SR-2291");
  const run = runs.find((r) => r.pin === pin) ?? runs[0];
  const steps = useMemo(() => replaySteps(state, run.pin), [state, run.pin]);
  const summary = replaySummary(steps);
  // -1 = not started; steps.length = finished.
  const [progress, setProgress] = useState(-1);
  const recorded = useRef(false);
  const finished = progress >= steps.length;

  useEffect(() => {
    if (progress < 0 || finished) return;
    const timer = setTimeout(() => setProgress((p) => p + 1), STEP_MS);
    return () => clearTimeout(timer);
  }, [progress, finished]);

  useEffect(() => {
    if (finished && !recorded.current) {
      recorded.current = true;
      recordReplay(run.pin, summary.reproduced, summary.nonDeterministic, summary.unavailable);
    }
  }, [finished, run.pin, summary]);

  const start = () => {
    recorded.current = false;
    setProgress(0);
  };

  const choose = (next: ReplayPin) => {
    setPin(next);
    setProgress(-1);
    recorded.current = false;
  };

  return (
    <FeatureGate flag="replay">
      <div className="flex flex-wrap items-start gap-4">
        <Panel className="flex-[1_1_300px]">
          <CardHeader title="Pinned run" description="The replay uses the versions recorded with the run, not today's data, graph or policy." />
          <div className="flex flex-col gap-2">
            {runs.map((r) => (
              <OptionCard key={r.pin} pressed={r.pin === run.pin} disabled={!r.available || (progress >= 0 && !finished)} onClick={() => choose(r.pin)}>
                <span className="flex flex-col">
                  <b>{r.label}</b>
                  <span className="text-xs text-muted">{r.available ? r.at : r.unavailableReason}</span>
                </span>
              </OptionCard>
            ))}
          </div>
          <KeyValueList items={run.versions} />
          <Button className="self-start" disabled={!run.available || role.readOnly || (progress >= 0 && !finished)} onClick={start}>
            {finished ? "Replay again" : "Replay investigation"}
          </Button>
          {role.readOnly ? <span className="text-xs text-muted">Auditors can read replay results but not start one.</span> : null}
        </Panel>

        <div className="flex min-w-0 flex-[3_1_560px] flex-col gap-4">
          <Card className="overflow-hidden">
            <div className="px-5 py-4">
              <CardHeader title={`Replay of ${run.pin}`} description="Each bounded tool runs again. Output hashes are compared with the ones recorded at run time." />
            </div>
            <DataTable caption="Replay steps">
              <thead>
                <tr>
                  <Th>Step</Th>
                  <Th>Result</Th>
                  <Th>Recorded</Th>
                  <Th>Replayed</Th>
                  <Th>Note</Th>
                </tr>
              </thead>
              <tbody>
                {steps.map((step, i) => {
                  const done = progress > i;
                  const running = progress === i;
                  return (
                    <tr key={step.name}>
                      <Td>
                        <b className="text-[13px]">{step.name}</b>
                        <div className="font-mono text-[11px] text-muted">{step.tool}</div>
                      </Td>
                      <Td>{done ? <Pill tone={outcomeTone(step.outcome)}>{step.outcome}</Pill> : <Pill tone="mute">{running ? "Running" : "Waiting"}</Pill>}</Td>
                      <Td className="font-mono text-xs">{step.original}</Td>
                      <Td className="font-mono text-xs">{done ? step.replayed : "…"}</Td>
                      <Td className="min-w-[220px] text-xs text-muted">{done ? step.note : ""}</Td>
                    </tr>
                  );
                })}
              </tbody>
            </DataTable>
          </Card>
          {finished ? (
            <Callout tone={summary.exact ? "ok" : "warn"} role="status" title={summary.exact ? "Exact replay." : "Not an exact replay."}>
              {summary.text}
            </Callout>
          ) : null}
          {state.replays.length > 0 ? (
            <Panel>
              <CardHeader title="Replay history" description="Each replay is written to the audit trail." />
              <ul className="flex flex-col">
                {state.replays.map((r) => (
                  <li key={r.id} className="flex flex-wrap justify-between gap-2 border-t border-line-soft py-1.5 text-[13px]">
                    <span>
                      <b className="font-mono">{r.id}</b> · {r.pin} · {r.by}
                    </span>
                    <span className="text-xs text-muted">
                      {r.reproduced} reproduced, {r.nonDeterministic} non-deterministic, {r.unavailable} unavailable · {r.at}
                    </span>
                  </li>
                ))}
              </ul>
            </Panel>
          ) : null}
        </div>
      </div>
    </FeatureGate>
  );
}
