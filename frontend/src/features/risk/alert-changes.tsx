"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { changesForAlert, diffRecompute, versionChanges, type AlertDiff } from "@/lib/domain/alert-diff";
import { ROUTES } from "@/lib/routes";
import { useDemoState } from "@/lib/store/hooks";
import type { AlertRecompute } from "@/lib/store/state";
import type { AlertId } from "@/lib/types";
import { FeatureGate } from "@/components/shell/feature-gate";
import { buttonClasses } from "@/components/ui/button";
import { Card, CardHeader, Panel } from "@/components/ui/card";
import { OptionCard } from "@/components/ui/controls";
import { EmptyState } from "@/components/ui/misc";
import { Pill } from "@/components/ui/pill";
import { DataTable, Td, Th } from "@/components/ui/table";

function DiffBody({ diff }: { diff: AlertDiff }) {
  return (
    <div className="flex flex-col gap-2 text-[13px]">
      {diff.changes.length > 0 ? (
        <DataTable caption={`Changes to ${diff.alertId}`}>
          <thead>
            <tr>
              <Th>Field</Th>
              <Th>Before</Th>
              <Th>After</Th>
            </tr>
          </thead>
          <tbody>
            {diff.changes.map((c) => (
              <tr key={c.label}>
                <Td>{c.label}</Td>
                <Td className="text-muted line-through decoration-1">{c.before}</Td>
                <Td className="font-semibold">{c.after}</Td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      ) : null}
      {diff.typologyChanges.map((c) => (
        <span key={c.label}>
          <b>Policy applicability, {c.label}:</b> {c.before} → {c.after}
        </span>
      ))}
      {diff.explanationAdded.map((t) => (
        <span key={`+${t}`}>
          <Pill tone="ok">Explanation added</Pill> {t}
        </span>
      ))}
      {diff.explanationRemoved.map((t) => (
        <span key={`-${t}`}>
          <Pill tone="mute">Explanation removed</Pill> <span className="line-through">{t}</span>
        </span>
      ))}
    </div>
  );
}

function RecordHeader({ record }: { record: AlertRecompute }) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-2">
        <b className="font-mono text-[13px]">{record.id}</b>
        <Pill tone="info">{record.kind}</Pill>
        <span className="text-xs text-muted">
          {record.at} · {record.by}
        </span>
      </div>
      <span className="text-[13px]">Reason: {record.reason}</span>
    </div>
  );
}

/** Compact change log shown on the alert detail page. */
export function AlertChangeLog({ alertId }: { alertId: AlertId }) {
  const state = useDemoState();
  const entries = useMemo(() => changesForAlert(state.recomputes, alertId, state.caseWork.status), [state.recomputes, alertId, state.caseWork.status]);
  return (
    <Panel>
      <CardHeader
        title="Alert diff and change log"
        description="What changed in this alert each time data, policy or a threshold changed. Both sides are recomputed from recorded inputs."
        actions={
          <Link href={ROUTES.riskChanges} className={buttonClasses("ghost", "sm")}>
            All changes
          </Link>
        }
      />
      {entries.length === 0 ? <span className="text-[13.5px] text-muted">No recomputation has changed this alert yet. Apply the mapping fix or save new weights to see one.</span> : null}
      {entries.slice(0, 3).map(({ record, diff }) => (
        <div key={record.id} className="flex flex-col gap-2 border-t border-line-soft pt-3">
          <RecordHeader record={record} />
          <DiffBody diff={diff} />
        </div>
      ))}
    </Panel>
  );
}

export function AlertChanges() {
  const state = useDemoState();
  const records = state.recomputes;
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const record = records.find((r) => r.id === selectedId) ?? records[0];
  const diffs = useMemo(() => (record ? diffRecompute(record, state.caseWork.status) : []), [record, state.caseWork.status]);
  const changed = diffs.filter((d) => d.changed);

  return (
    <FeatureGate flag="alertDiff">
      {records.length === 0 ? (
        <Card>
          <EmptyState
            title="No recomputations yet."
            description="Each data fix, rollback, priority-weight change or review-band change is recorded here with before and after versions and a reason."
            action={
              <Link href={ROUTES.dataQuality} className={buttonClasses("ghost", "sm")}>
                Go to quality exceptions
              </Link>
            }
          />
        </Card>
      ) : (
        <div className="flex flex-wrap items-start gap-4">
          <Panel className="flex-[1_1_280px]">
            <CardHeader title="Decision change log" description="Newest first." />
            <div className="flex flex-col gap-2">
              {records.map((r) => (
                <OptionCard key={r.id} pressed={r.id === record?.id} onClick={() => setSelectedId(r.id)}>
                  <RecordHeader record={r} />
                </OptionCard>
              ))}
            </div>
          </Panel>
          {record ? (
            <div className="flex min-w-0 flex-[3_1_560px] flex-col gap-4">
              <Panel>
                <CardHeader title={`${record.id} · versions`} description={record.reason} />
                <DataTable caption="Version changes">
                  <thead>
                    <tr>
                      <Th>Version</Th>
                      <Th>Before</Th>
                      <Th>After</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {versionChanges(record).map((c) => (
                      <tr key={c.label}>
                        <Td>{c.label}</Td>
                        <Td className="font-mono text-xs">{c.before}</Td>
                        <Td className="font-mono text-xs font-semibold">{c.after}</Td>
                      </tr>
                    ))}
                  </tbody>
                </DataTable>
                <span className="text-xs text-muted">
                  {changed.length} of {diffs.length} alerts changed. Review queue order after this change: {diffs.map((d) => d.alertId).join(", ")}.
                </span>
              </Panel>
              {changed.map((diff) => (
                <Panel key={diff.alertId}>
                  <CardHeader
                    title={`${diff.alertId} · ${diff.typology}`}
                    actions={
                      <Link href={ROUTES.riskAlert(diff.alertId)} className={buttonClasses("ghost", "sm")}>
                        Open alert
                      </Link>
                    }
                  />
                  <DiffBody diff={diff} />
                </Panel>
              ))}
            </div>
          ) : null}
        </div>
      )}
    </FeatureGate>
  );
}
