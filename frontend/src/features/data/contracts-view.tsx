"use client";

import { useLiveProfile } from "@/lib/live/session";
import { CONTRACT_FIELDS } from "@/lib/seed/data-ops";
import { rollbackMapping } from "@/lib/store/actions";
import { useDemoState, useRole } from "@/lib/store/hooks";
import { Button } from "@/components/ui/button";
import { Card, Panel } from "@/components/ui/card";
import { KeyValueList } from "@/components/ui/key-value";
import { Pill } from "@/components/ui/pill";
import { DataTable, Td, Th } from "@/components/ui/table";
import { LiveContracts } from "@/features/live/live-sources";

export function ContractsView() {
  // Live session: the real canonical schema and rule catalog first, then the seeded contract as a sample.
  if (useLiveProfile()) {
    return (
      <div className="flex flex-col gap-4">
        <LiveContracts />
        <SampleContract sample />
      </div>
    );
  }
  return <SampleContract />;
}

function SampleContract({ sample = false }: { sample?: boolean }) {
  const fixed = useDemoState().data.driftFixed;
  const role = useRole();
  const versions = [
    { version: "v4.1", detail: "bene_acct_no to target_account", status: fixed ? "Active" : "Proposed", tone: fixed ? ("ok" as const) : ("info" as const) },
    { version: "v4.0", detail: "Original contract mapping", status: fixed ? "Superseded" : "Active", tone: fixed ? ("mute" as const) : ("ok" as const) },
  ];
  return (
    <div className="flex flex-wrap items-start gap-4">
      <Card className="min-w-0 flex-[2_1_560px] overflow-hidden">
        <div className="px-5 py-4">
          <h2 className="text-base font-semibold">
            Data contract ach.v4
            {sample ? <Pill className="ml-2 align-middle">Sample</Pill> : null}
          </h2>
          <span className="text-xs text-muted">Executable checks run at ingestion. Failures are quarantined, never silently coerced.</span>
        </div>
        <DataTable caption="Contract fields">
          <thead>
            <tr>
              <Th>Field</Th>
              <Th>Type</Th>
              <Th>Required</Th>
              <Th>Rule</Th>
            </tr>
          </thead>
          <tbody>
            {CONTRACT_FIELDS.map((f) => (
              <tr key={f.field}>
                <Td className="font-mono">{f.field}</Td>
                <Td>{f.type}</Td>
                <Td>{f.required}</Td>
                <Td className="text-xs text-muted">{f.rule}</Td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      </Card>
      <div className="flex flex-[1_1_340px] flex-col gap-4">
        <Panel>
          <h2 className="text-base font-semibold">Catalog entry</h2>
          <KeyValueList
            items={[
              ["Owner", "Payments Ops · P. Nair"],
              ["Purpose", "Transaction monitoring and investigation"],
              ["Sensitivity", "Restricted: tokenized identifiers"],
              ["Retention label", "[Retention class]"],
              ["Quality SLA", "99.5% valid within 30 min"],
              ["Last validation", "Oct 9, 13:20 UTC"],
            ]}
          />
        </Panel>
        <Panel>
          <h2 className="text-base font-semibold">Mapping versions</h2>
          {versions.map((v) => (
            <div key={v.version} className="flex justify-between gap-2 border-t border-line-soft pt-2">
              <div>
                <b className="font-mono">{v.version}</b>
                <div className="text-xs text-muted">{v.detail}</div>
              </div>
              <Pill tone={v.tone}>{v.status}</Pill>
            </div>
          ))}
          {fixed ? (
            <Button variant="ghost" size="sm" className="self-start" disabled={!role.canFixData} onClick={rollbackMapping}>
              Roll back to v4.0
            </Button>
          ) : null}
        </Panel>
      </div>
    </div>
  );
}
