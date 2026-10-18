"use client";

import { useMemo } from "react";
import { agentRun, agentTone, graphSyncRuns } from "@/lib/domain/governance";
import { useDemoState } from "@/lib/store/hooks";
import { Card, CardHeader } from "@/components/ui/card";
import { Pill } from "@/components/ui/pill";
import { DataTable, Td, Th } from "@/components/ui/table";

export function PipelinesView() {
  const state = useDemoState();
  const agents = useMemo(() => agentRun(state), [state]);
  const syncs = graphSyncRuns(state.sim.graph);

  return (
    <div className="grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(340px,1fr))]">
      <Card className="overflow-hidden">
        <div className="px-5 py-4">
          <CardHeader title="Sample agent workflow · AR-5521 · CASE-0412" description="Illustrative workflow only. Live jobs appear in Pipeline runs above; open a case or Copilot for its saved steps and receipts." />
        </div>
        <DataTable caption="Agent steps">
          <thead>
            <tr>
              <Th>Agent</Th>
              <Th>Status</Th>
              <Th>Output</Th>
              <Th>Time</Th>
            </tr>
          </thead>
          <tbody>
            {agents.map((agent) => (
              <tr key={agent.name}>
                <Td>{agent.name}</Td>
                <Td>
                  <Pill tone={agentTone(agent.status)}>{agent.status}</Pill>
                </Td>
                <Td className="text-xs text-muted">{agent.output}</Td>
                <Td className="whitespace-nowrap text-xs text-muted">{agent.duration}</Td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      </Card>
      <Card className="self-start overflow-hidden">
        <div className="px-5 py-4">
          <CardHeader title="Sample graph sync history" description="Illustrative synchronization history. The live graph engine queries canonical transactions from Supabase on request." />
        </div>
        <DataTable caption="Graph sync runs">
          <thead>
            <tr>
              <Th>Run</Th>
              <Th>Status</Th>
              <Th>Watermark</Th>
              <Th>Lag</Th>
            </tr>
          </thead>
          <tbody>
            {syncs.map((run) => (
              <tr key={run.id}>
                <Td className="font-mono">{run.id}</Td>
                <Td>
                  <Pill tone={run.tone}>{run.status}</Pill>
                </Td>
                <Td className="text-xs text-muted">{run.watermark}</Td>
                <Td>{run.lag}</Td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      </Card>
    </div>
  );
}
