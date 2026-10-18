"use client";

import { useState } from "react";
import { Callout } from "@/components/ui/callout";
import { Card, Panel } from "@/components/ui/card";
import { Segmented } from "@/components/ui/controls";
import { DataTable, Td, Th } from "@/components/ui/table";

const TYPOLOGIES = ["Layering", "Rapid pass-through", "Round trip", "Structuring-like", "Funnel", "Fan-out", "Dormant reactivation", "Mule cluster"];
const PLACEHOLDER = "[__]";

/** Evaluation results stay as placeholders until the chronological run is measured. */
export function DetectorComparison() {
  const [capacity, setCapacity] = useState<10 | 25 | 50>(25);
  const metrics = ["PR-AUC", `Precision@${capacity}`, `Recall@${capacity}`, "False alerts per 1,000", "Latency p95"];

  return (
    <>
      <Callout tone="info" title="Placeholders until the evaluation run finishes.">
        Every cell shows {PLACEHOLDER} until measured on the chronological split. Synthetic metrics do not predict performance at a real bank.
      </Callout>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[13.5px] text-muted">Review capacity</span>
        <Segmented
          label="Review capacity"
          value={capacity}
          onChange={setCapacity}
          options={[
            { value: 10, label: "K = 10" },
            { value: 25, label: "K = 25" },
            { value: 50, label: "K = 50" },
          ]}
        />
      </div>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(340px,1fr))] gap-4">
        <Card className="overflow-hidden">
          <div className="px-5 py-4">
            <h2 className="text-base font-semibold">Detector comparison</h2>
            <span className="text-xs text-muted">Same chronological split, history-only graph snapshots.</span>
          </div>
          <DataTable caption="Detector comparison">
            <thead>
              <tr>
                <Th>Metric</Th>
                <Th>Rules v3.2</Th>
                <Th>XGBoost v1.4.2</Th>
                <Th>Anomaly v0.9</Th>
                <Th>Fusion v3.1</Th>
              </tr>
            </thead>
            <tbody>
              {metrics.map((m) => (
                <tr key={m}>
                  <Td className="font-bold">{m}</Td>
                  <Td>{PLACEHOLDER}</Td>
                  <Td>{PLACEHOLDER}</Td>
                  <Td>{PLACEHOLDER}</Td>
                  <Td>{PLACEHOLDER}</Td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        </Card>
        <Card className="overflow-hidden">
          <div className="px-5 py-4">
            <h2 className="text-base font-semibold">Recall by typology</h2>
            <span className="text-xs text-muted">Seeded scenarios A to J. Labels used only for scoring.</span>
          </div>
          <DataTable caption="Recall by typology">
            <thead>
              <tr>
                <Th>Typology</Th>
                <Th>Rules</Th>
                <Th>XGBoost</Th>
                <Th>Anomaly</Th>
              </tr>
            </thead>
            <tbody>
              {TYPOLOGIES.map((t) => (
                <tr key={t}>
                  <Td>{t}</Td>
                  <Td>{PLACEHOLDER}</Td>
                  <Td>{PLACEHOLDER}</Td>
                  <Td>{PLACEHOLDER}</Td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        </Card>
      </div>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(340px,1fr))] gap-4">
        <Panel>
          <h2 className="text-base font-semibold">Alert overlap</h2>
          <div className="grid grid-cols-3 gap-2.5">
            {[
              ["Rules only", "bg-select-tint"],
              ["Both", "bg-brand-tint"],
              ["XGBoost only", "bg-attention-tint"],
            ].map(([label, bg]) => (
              <div key={label} className={`rounded-xl p-3 ${bg}`}>
                <div className="text-xs text-muted">{label}</div>
                <b className="text-xl">{PLACEHOLDER}</b>
              </div>
            ))}
          </div>
        </Panel>
        <Panel>
          <h2 className="text-base font-semibold">Leakage guards</h2>
          <ul className="flex list-disc flex-col gap-1.5 pl-[18px] text-[13.5px]">
            <li>Train up to Sep 15; test Sep 16 to Oct 9 (synthetic)</li>
            <li>Scores at time T use only data available by T</li>
            <li>No future edges in graph features</li>
            <li>No post-investigation labels in features</li>
          </ul>
        </Panel>
      </div>
    </>
  );
}
