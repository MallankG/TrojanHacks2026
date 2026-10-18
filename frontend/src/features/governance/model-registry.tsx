"use client";

import { useState } from "react";
import { MODELS } from "@/lib/seed/governance";
import type { ModelEntry } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, Panel } from "@/components/ui/card";
import { KeyValueList } from "@/components/ui/key-value";
import { Pill } from "@/components/ui/pill";
import { DataTable, Td, Th, Tr } from "@/components/ui/table";

export function ModelRegistry() {
  const [selected, setSelected] = useState<ModelEntry["key"]>("xgb");
  const model = MODELS.find((m) => m.key === selected) ?? MODELS[0];

  return (
    <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
      <Card className="overflow-hidden lg:flex-[1_1_380px]">
        <div className="px-5 py-4">
          <CardHeader title="Model registry" description="Every alert links to the exact artifact that scored it." />
        </div>
        <DataTable caption="Registered models">
          <thead>
            <tr>
              <Th>Model</Th>
              <Th>Version</Th>
              <Th>Status</Th>
            </tr>
          </thead>
          <tbody>
            {MODELS.map((m) => (
              <Tr key={m.key} selected={m.key === selected}>
                <Td>
                  <Button variant="link" className="font-mono" aria-pressed={m.key === selected} onClick={() => setSelected(m.key)}>
                    {m.name}
                  </Button>
                </Td>
                <Td className="font-mono">{m.version}</Td>
                <Td>
                  <Pill tone={m.tone}>{m.status}</Pill>
                </Td>
              </Tr>
            ))}
          </tbody>
        </DataTable>
      </Card>
      <Panel className="lg:flex-[2_1_460px]">
        <CardHeader
          title={
            <span className="font-mono">
              {model.name} {model.version}
            </span>
          }
          actions={<Pill tone={model.tone}>{model.status}</Pill>}
        />
        <KeyValueList
          items={[
            ["Training data", model.data],
            ["Code commit", <span key="c" className="font-mono">{model.commit}</span>],
            ["Feature schema", model.schema],
            ["Threshold", model.threshold],
            ["Artifact checksum", <span key="s" className="font-mono">{model.checksum}</span>],
            ["Split", "Train to Sep 15, test Sep 16 to Oct 9 (chronological)"],
            ["Metrics", "PR-AUC [__] · Precision@K [__] · Recall@K [__]"],
            ["Intended use", model.use],
            ["Limitations", model.limits],
          ]}
        />
        <span className="text-xs text-muted">A challenger never replaces the champion without an approved comparison on the same split. Metric cells stay [__] until measured.</span>
      </Panel>
    </div>
  );
}
