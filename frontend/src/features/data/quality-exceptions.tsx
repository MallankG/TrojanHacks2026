"use client";

import { useMemo, useState } from "react";
import { BAD_ROWS } from "@/lib/seed/data-ops";
import { approveRepair, openModal, proposeRepair, rollbackRepair, routeRow } from "@/lib/store/actions";
import { useDemoState, useRole } from "@/lib/store/hooks";
import { ROLES, deniedMessage } from "@/lib/seed/roles";
import type { BadRowGroup, BadRowSeed, Repair, Tone } from "@/lib/types";
import { Button, buttonClasses } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Card, Panel } from "@/components/ui/card";
import { Chip } from "@/components/ui/controls";
import { Field, TextArea } from "@/components/ui/field";
import { KeyValueList } from "@/components/ui/key-value";
import { CodeBlock, EmptyState } from "@/components/ui/misc";
import { Pill } from "@/components/ui/pill";
import { DataTable, Td, Th, Tr } from "@/components/ui/table";
import { SensitiveText } from "@/components/ui/sensitive";

type Filter = "all" | BadRowGroup;

interface RowView extends Omit<BadRowSeed, "group"> {
  state: string;
  tone: Tone;
  group: BadRowGroup;
  resultText: string;
  normalizedText: string;
  repair?: Repair;
}

function describeRow(row: BadRowSeed, fixed: boolean, repair: Repair | undefined): RowView {
  let state = "Open";
  let tone: Tone = "warn";
  if (row.mode === "mapping") {
    state = fixed ? "Resolved by fix" : "Open";
    tone = fixed ? "ok" : "warn";
  } else if (row.mode === "auto") {
    state = "Auto-normalized";
    tone = "ok";
  } else if (repair) {
    const labels: Record<Repair["status"], [string, Tone]> = {
      proposed: ["Proposed", "info"],
      applied: ["Applied", "ok"],
      requested: ["Re-export requested", "info"],
      sent: ["In entity review", "info"],
      rolledback: ["Rolled back", "warn"],
    };
    [state, tone] = labels[repair.status];
  }
  const resolved = state === "Applied" || state === "Resolved by fix" || state === "Auto-normalized";
  const resultText = row.mode === "mapping" ? (fixed ? "Passed after mapping fix v4.1" : "Rejected: required field empty (schema drift)") : (row.result ?? "");
  const normalizedText =
    row.mode === "mapping" ? (fixed ? "ACC-4471" : "Not normalized") : row.mode === "repair" ? (repair?.status === "applied" ? "Derived version v2 (source row unchanged)" : (row.fixNote ?? "")) : (row.normalized ?? "");
  return { ...row, state, tone, group: resolved ? "resolved" : row.group, resultText, normalizedText, repair };
}

const FILTERS: Array<{ key: Filter; label: string }> = [
  { key: "all", label: "All" },
  { key: "rejected", label: "Rejected" },
  { key: "flagged", label: "Flagged or held" },
  { key: "resolved", label: "Resolved" },
];

export function QualityExceptions() {
  const state = useDemoState();
  const role = useRole();
  const fixed = state.data.driftFixed;
  const [filter, setFilter] = useState<Filter>("all");
  const [selectedRow, setSelectedRow] = useState(BAD_ROWS[0].row);
  const [reason, setReason] = useState("");

  const rows = useMemo(() => BAD_ROWS.map((r) => describeRow(r, fixed, state.data.repairs[r.row])), [fixed, state.data.repairs]);
  const visible = rows.filter((r) => filter === "all" || r.group === filter);
  const row = rows.find((r) => r.row === selectedRow) ?? rows[0];
  const repairDone = row.repair?.status === "applied" || fixed;

  const impact = [
    ...row.features.map((v) => ({ kind: "Feature", value: v, status: repairDone ? "Re-computed" : "Stale" })),
    ...row.alerts.map((v) => ({ kind: "Alert", value: v, status: repairDone ? "Re-scored" : "Stale" })),
    ...row.links.map((v) => ({ kind: "Graph or entity", value: v, status: fixed ? "Restored" : "Missing" })),
    ...row.cases.map((v) => ({ kind: "Case", value: v, status: fixed ? "Evidence updated" : "Evidence partial" })),
  ];
  const lineage = ["ach_2026_10_06_b.csv", `Raw row ${row.row}`, row.mode === "mapping" && !fixed ? "No canonical record" : "Canonical record", "Features", row.alerts.length ? row.alerts.join(", ") : "No alerts"];

  const canPropose = row.mode === "repair" && (!row.repair || row.repair.status === "rolledback");
  const canApprove = row.mode === "repair" && row.repair?.status === "proposed";
  const canRollback = row.mode === "repair" && row.repair?.status === "applied";

  return (
    <div className="flex flex-wrap items-start gap-4">
      <Card aria-label="Quality exceptions" className="min-w-0 flex-[2_1_520px] overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-2.5 px-5 py-4">
          <div>
            <h2 className="text-base font-semibold">Quality exceptions · ach_2026_10_06_b</h2>
            <span className="text-xs text-muted">Source rows are never edited. Repairs create a derived version.</span>
          </div>
          <div role="group" aria-label="Filter exceptions" className="flex flex-wrap gap-1.5">
            {FILTERS.map((f) => (
              <Chip key={f.key} pressed={filter === f.key} onClick={() => setFilter(f.key)}>
                {f.label}
              </Chip>
            ))}
          </div>
        </div>
        <DataTable caption="Rejected and flagged rows">
          <thead>
            <tr>
              <Th>Row</Th>
              <Th>Issue</Th>
              <Th>Field</Th>
              <Th>Original value</Th>
              <Th>State</Th>
            </tr>
          </thead>
          <tbody>
            {visible.map((r) => (
              <Tr key={r.row} selected={r.row === row.row}>
                <Td>
                  <button type="button" aria-pressed={r.row === row.row} className={buttonClasses("link", "md", "font-mono")} onClick={() => setSelectedRow(r.row)}>
                    {r.row}
                  </button>
                </Td>
                <Td>{r.type}</Td>
                <Td className="font-mono">{r.field}</Td>
                <Td className="text-xs text-muted"><SensitiveText value={r.original} /></Td>
                <Td>
                  <Pill tone={r.tone}>{r.state}</Pill>
                </Td>
              </Tr>
            ))}
          </tbody>
        </DataTable>
        {visible.length === 0 ? <EmptyState title="No exceptions match this filter." description="Try another filter." action={<Button variant="ghost" size="sm" onClick={() => setFilter("all")}>Show all</Button>} /> : null}
      </Card>

      <Panel aria-label="Row detail" className="flex-[1_1_380px]">
        <div>
          <div className="eyebrow">
            Row {row.row} · {row.field}
          </div>
          <h2 className="mt-1 text-lg font-semibold">{row.type}</h2>
          <Pill tone={row.tone} className="mt-1.5">
            {row.state}
          </Pill>
        </div>
        <KeyValueList
          items={[
            ["Source row", `ach_2026_10_06_b.csv, row ${row.row}`],
            ["Original value", <SensitiveText key="o" className="font-mono" value={row.original} />],
            ["Validation result", row.resultText],
            ["Normalized value", row.normalizedText],
          ]}
        />
        <div>
          <h3 className="mb-1.5 text-[13.5px] font-semibold">Lineage</h3>
          <ol className="flex flex-wrap items-center gap-1.5" aria-label="Row lineage">
            {lineage.map((step, i) => (
              <li key={step} className="flex items-center gap-1.5">
                <Pill tone="mute">{step}</Pill>
                {i < lineage.length - 1 ? <span aria-hidden="true" className="text-xs text-muted">›</span> : null}
              </li>
            ))}
          </ol>
        </div>
        <div>
          <h3 className="mb-1.5 text-[13.5px] font-semibold">Downstream impact</h3>
          {impact.length === 0 ? <span className="text-xs text-muted">No downstream outputs depend on this row.</span> : null}
          {impact.map((item) => (
            <div key={`${item.kind}-${item.value}`} className="flex justify-between gap-2 border-t border-line-soft py-1.5 text-[13px]">
              <span>
                <span className="text-xs text-muted">{item.kind}:</span> <SensitiveText value={item.value} />
              </span>
              <Pill tone={["Stale", "Missing", "Evidence partial"].includes(item.status) ? "warn" : "ok"}>{item.status}</Pill>
            </div>
          ))}
        </div>

        {row.mode === "mapping" && !fixed ? (
          <Callout tone="warn" action={<Button variant="link" disabled={!role.canFixData} onClick={() => openModal({ kind: "mappingFix" })}>Review mapping fix v4.1</Button>}>
            This row is fixed by the batch-level mapping fix.
          </Callout>
        ) : null}

        {row.mode === "repair" ? (
          <div className="grid grid-cols-2 gap-2">
            <div>
              <div className="mb-1 text-xs text-muted">Before (source)</div>
              <CodeBlock>{row.before}</CodeBlock>
            </div>
            <div>
              <div className="mb-1 text-xs text-muted">After (derived v2)</div>
              <CodeBlock>{row.after}</CodeBlock>
            </div>
          </div>
        ) : null}

        {row.mode === "repair" && row.repair ? (
          <span className="text-xs text-muted">
            Proposed by {ROLES[row.repair.proposedBy].name}
            {row.repair.approvedBy ? `; approved by ${ROLES[row.repair.approvedBy].name}` : ""}
          </span>
        ) : null}

        {canPropose ? (
          <>
            <Field label="Reason for the repair" hint={!role.canFixData ? deniedMessage(role) : undefined}>
              {(props) => <TextArea {...props} rows={2} value={reason} disabled={!role.canFixData} onChange={(e) => setReason(e.target.value)} placeholder="What is wrong and why this fix is right" />}
            </Field>
            <Button
              disabled={!role.canFixData}
              onClick={() => {
                if (proposeRepair(row.row, row.fixNote ?? "", reason)) setReason("");
              }}
            >
              Propose repair
            </Button>
          </>
        ) : null}
        {canApprove ? (
          <>
            <Callout tone="info">Four-eyes rule: a different steward or BSA officer must approve. Switch the Role menu to try it.</Callout>
            <Button onClick={() => approveRepair(row.row)}>Approve and re-score</Button>
          </>
        ) : null}
        {canRollback ? (
          <Button variant="ghost" onClick={() => rollbackRepair(row.row)}>
            Roll back to prior version
          </Button>
        ) : null}
        {row.mode === "owner" && !row.repair ? (
          <Button variant="ghost" onClick={() => routeRow(row.row, "requested")}>
            Request corrected export from owner
          </Button>
        ) : null}
        {row.mode === "entity" && !row.repair ? (
          <Button variant="ghost" onClick={() => routeRow(row.row, "sent")}>
            Send to entity review
          </Button>
        ) : null}
      </Panel>
    </div>
  );
}
