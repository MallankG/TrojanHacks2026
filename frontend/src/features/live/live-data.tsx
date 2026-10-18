"use client";

import Link from "next/link";
import { useState, type ReactNode } from "react";
import { issuesToChecks, useLiveBatches } from "@/lib/live/adapters";
import { errorMessage, liveApi, useLive, type DqIssue } from "@/lib/live/api";
import { BAND_TONE, SEVERITY_TONE, alertLabel, words } from "@/lib/live/format";
import { LIVE_ROLES } from "@/lib/live/roles";
import { useLiveProfile } from "@/lib/live/session";
import { ROUTES } from "@/lib/routes";
import { useDemoState } from "@/lib/store/hooks";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { CardHeader, Panel } from "@/components/ui/card";
import { Chip } from "@/components/ui/controls";
import { KeyValueList } from "@/components/ui/key-value";
import { CodeBlock, EmptyState, Skeleton } from "@/components/ui/misc";
import { Pill } from "@/components/ui/pill";
import { DataTable, Td, Th, Tr } from "@/components/ui/table";
import { CheckRow } from "@/features/data/check-row";
import { LiveData } from "./live-state";
import { LiveAccountLink } from "./live-entity";

/** Contract checks for a live ingestion run, derived from its data-quality issues. */
export function LiveRunChecks({ runId }: { runId: string }) {
  const issues = useLive(() => liveApi.runIssues(runId), `issues:${runId}`);
  return (
    <LiveData query={issues} label="contract checks">
      {(page) => (
        <>
          {issuesToChecks(page.items).map((check) => (
            <CheckRow key={check.label} check={check} />
          ))}
          <CappedNote shown={page.items.length} total={page.total} />

          <Link href={ROUTES.dataQuality} className="text-[13px] font-semibold text-brand-link underline underline-offset-2">
            See row-level exceptions
          </Link>
        </>
      )}
    </LiveData>
  );
}

/** Row-level exceptions for live ingestion runs, shown above the seeded sample exceptions. */
export function LiveExceptions() {
  const profile = useLiveProfile();
  const runs = useLiveBatches();
  const [runId, setRunId] = useState<string | null>(null);
  const [rowId, setRowId] = useState<string | null>(null);
  const selectedBatch = useDemoState().data.selectedBatch;
  if (!profile) return null;
  const canSteward = LIVE_ROLES[profile.role].canSteward;
  // Opening this tab from a live batch ("Quality exceptions for this batch") starts on that batch's run.
  const active = runId ?? runs.data?.find((b) => b.name === selectedBatch)?.runId ?? runs.data?.[0]?.runId ?? null;

  return (
    <Panel>
      <CardHeader
        eyebrow="Live backend"
        title="Row-level exceptions"
        description="Findings from your uploaded files. Rejected rows never reach the transaction table; warnings are kept with a caveat."
      />
      {runs.error ? (
        <Callout tone="bad" role="alert" title="Could not load ingestion runs.">
          {runs.error}
        </Callout>
      ) : runs.loading ? (
        <Skeleton label="Loading runs" />
      ) : !runs.data || runs.data.length === 0 ? (
        <EmptyState title="No live uploads yet." description="Use Upload CSV batch to load a synthetic file." />
      ) : (
        <>
          <div role="group" aria-label="Ingestion run" className="flex flex-wrap gap-1.5">
            {runs.data.map((b) => (
              <Chip key={b.runId} mono pressed={b.runId === active} onClick={() => (setRunId(b.runId!), setRowId(null))}>
                {b.name}
              </Chip>
            ))}
          </div>
          {active ? <RunIssues runId={active} canSteward={canSteward} onSelectRow={setRowId} /> : null}
        </>
      )}
      {rowId ? <SourceRow rowId={rowId} /> : null}
    </Panel>
  );
}

function RunIssues({ runId, canSteward, onSelectRow }: { runId: string; canSteward: boolean; onSelectRow: (id: string) => void }) {
  const issues = useLive(() => liveApi.runIssues(runId), `issues:${runId}`);
  return (
    <LiveData query={issues} label="data-quality issues">
      {(page) =>
        page.items.length === 0 ? (
          <EmptyState title="This run has no data-quality issues." />
        ) : (
          <>
          <CappedNote shown={page.items.length} total={page.total} />
          <DataTable caption="Data-quality issues">
            <thead>
              <tr>
                <Th>Row</Th>
                <Th>Rule</Th>
                <Th>Severity</Th>
                <Th>Field</Th>
                <Th>Finding</Th>
                <Th>Status</Th>
                {canSteward ? <Th>Remediation</Th> : null}
              </tr>
            </thead>
            <tbody>
              {page.items.map((issue) => (
                <Tr key={issue.id}>
                  <Td>
                    {issue.source_row_id ? (
                      <Button variant="link" onClick={() => onSelectRow(issue.source_row_id!)}>
                        Row {issue.source_rows?.row_number ?? "?"}
                      </Button>
                    ) : (
                      "File"
                    )}
                  </Td>
                  <Td className="font-mono text-xs">{issue.rule_id}</Td>
                  <Td>
                    <Pill tone={SEVERITY_TONE[issue.severity]}>{words(issue.severity)}</Pill>
                  </Td>
                  <Td className="font-mono text-xs">{issue.field_name ?? "None"}</Td>
                  <Td className="max-w-[360px]">
                    {issue.message}
                    {issue.observed_value ? <span className="block font-mono text-xs text-muted">Observed: {issue.observed_value}</span> : null}
                  </Td>
                  <Td>
                    <Pill tone={issue.status === "open" ? "warn" : "ok"}>{words(issue.status)}</Pill>
                  </Td>
                  {canSteward ? (
                    <Td>
                      <IssueActions issue={issue} onChanged={issues.reload} />
                    </Td>
                  ) : null}
                </Tr>
              ))}
            </tbody>
          </DataTable>
          </>
        )
      }
    </LiveData>
  );
}

function IssueActions({ issue, onChanged }: { issue: DqIssue; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = async (status: DqIssue["status"]) => {
    setBusy(true);
    setError(null);
    try {
      await liveApi.updateIssue(issue.id, { status, remediation_note: `Marked ${status} from the quality exceptions screen.` });
      onChanged();
    } catch (problem) {
      setError(errorMessage(problem));
    } finally {
      setBusy(false);
    }
  };
  if (issue.status === "remediated" || issue.status === "waived") return <span className="text-xs text-muted">{issue.remediation_note ?? "Resolved"}</span>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {issue.status === "open" ? (
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => set("acknowledged")}>
          Acknowledge
        </Button>
      ) : null}
      <Button size="sm" variant="ghost" disabled={busy} onClick={() => set("waived")}>
        Waive
      </Button>
      {error ? <span className="text-xs font-semibold text-danger-ink">{error}</span> : null}
    </div>
  );
}

function SourceRow({ rowId }: { rowId: string }) {
  const row = useLive(() => liveApi.sourceRow(rowId), `row:${rowId}`);
  return (
    <div className="flex flex-col gap-2 border-t border-line-soft pt-3">
      <h3 className="text-[13.5px] font-semibold">Source row and downstream impact</h3>
      <LiveData query={row} label="source row">
        {(d) => (
          <div className="grid gap-4 md:grid-cols-2">
            <div className="flex flex-col gap-2">
              <KeyValueList
                items={[
                  ["Row", d.source_row.row_number],
                  ["Validation", words(d.source_row.status)],
                  ["File", d.source_row.ingestion_runs?.source_files?.filename ?? "Not recorded"],
                  ["Schema", d.source_row.ingestion_runs?.schema_version ?? "Not recorded"],
                  ["Canonical transaction", d.transaction ? d.transaction.transaction_ref : "None (row rejected)"],
                  ...(d.transaction
                    ? ([
                        ["Sender", <LiveAccountLink key="o" id={d.transaction.origin_account_id} label="Open account profile" />],
                        ["Receiver", <LiveAccountLink key="d" id={d.transaction.destination_account_id} label="Open account profile" />],
                      ] as Array<[string, ReactNode]>)
                    : []),
                ]}
              />
              <b className="mt-1 text-[13px]">Original values</b>
              <CodeBlock>{JSON.stringify(d.source_row.raw_data, null, 2)}</CodeBlock>
            </div>
            <div className="flex flex-col gap-2">
              <b className="text-[13px]">Downstream</b>
              <KeyValueList
                items={[
                  ["Features", d.downstream.features.length],
                  ["Risk signals", d.downstream.risk_signals.length],
                  ["Alerts", d.downstream.alerts.length],
                ]}
              />
              {d.downstream.alerts.length > 0 ? (
                <ul className="flex flex-wrap gap-1.5">
                  {d.downstream.alerts.map(({ alerts: a }) => (
                    <li key={a.id}>
                      <Link href={ROUTES.riskAlert(a.id)}>
                        <Pill tone={BAND_TONE[a.priority_band]}>{alertLabel(a.alert_number)}</Pill>
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-muted">Nothing downstream yet. Scores and alerts appear after the model worker runs.</p>
              )}
            </div>
          </div>
        )}
      </LiveData>
    </div>
  );
}

/** Says so when a run has more issues than were loaded, so counts are never silently partial. */
function CappedNote({ shown, total }: { shown: number; total: number | null }) {
  if (total === null || total <= shown) return null;
  return (
    <p className="text-xs font-semibold text-attention-ink">
      Showing the first {shown.toLocaleString("en-US")} of {total.toLocaleString("en-US")} issues; counts above cover the loaded issues only.
    </p>
  );
}
