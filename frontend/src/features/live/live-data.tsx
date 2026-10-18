"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { ApiError } from "@/lib/api/client";
import { issuesToChecks, runToBatch, useLiveBatches } from "@/lib/live/adapters";
import { errorMessage, invalidateLive, liveApi, useLive, type DqIssue, type UploadResult } from "@/lib/live/api";
import { BAND_TONE, SEVERITY_TONE, alertLabel, words } from "@/lib/live/format";
import { LIVE_ROLES } from "@/lib/live/roles";
import { useLiveProfile } from "@/lib/live/session";
import { ROUTES } from "@/lib/routes";
import { closeModal, selectBatch, toast } from "@/lib/store/actions";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { CardHeader, Panel } from "@/components/ui/card";
import { Chip } from "@/components/ui/controls";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox, Field, TextInput } from "@/components/ui/field";
import { KeyValueList } from "@/components/ui/key-value";
import { CodeBlock, EmptyState, Skeleton } from "@/components/ui/misc";
import { Pill } from "@/components/ui/pill";
import { DataTable, Td, Th, Tr } from "@/components/ui/table";
import { CheckRow } from "@/features/data/check-row";
import { LiveData } from "./live-state";

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

/** Real CSV upload to the backend (`POST /ingestion/upload`), opened from "Upload CSV batch". */
export function LiveUploadDialog() {
  const [file, setFile] = useState<File | null>(null);
  const [sourceSystem, setSourceSystem] = useState("core-banking-ach");
  const [owner, setOwner] = useState("Payments ops");
  const [synthetic, setSynthetic] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<UploadResult | null>(null);

  const missing = [!file && "choose a CSV file", !sourceSystem.trim() && "enter a source system", !owner.trim() && "enter a data owner"].filter(Boolean);

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!file || missing.length > 0) return;
    const form = new FormData();
    form.set("file", file);
    form.set("source_system", sourceSystem.trim());
    form.set("data_owner", owner.trim());
    form.set("is_synthetic", String(synthetic));
    setBusy(true);
    setError(null);
    try {
      const uploaded = await liveApi.upload(form);
      setResult(uploaded);
      invalidateLive("runs");
      invalidateLive("issues");
      selectBatch(runToBatch(uploaded.run).name);
      toast(`Run ${words(uploaded.run.status).toLowerCase()}: ${uploaded.run.accepted_count ?? 0} accepted, ${uploaded.run.rejected_count ?? 0} rejected.`);
    } catch (problem) {
      setError(
        problem instanceof ApiError && problem.status === 409
          ? "This exact file was already ingested (same checksum), so it was not loaded twice. Its run is in the Batches table; change the file to upload it again."
          : errorMessage(problem),
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      eyebrow="Live backend"
      title="Upload CSV batch"
      onClose={closeModal}
      footer={
        result ? (
          <Button onClick={closeModal}>Done</Button>
        ) : (
          <>
            <Button variant="ghost" onClick={closeModal}>
              Cancel
            </Button>
            <Button disabled={busy || missing.length > 0} onClick={() => void submit()}>
              {busy ? "Validating" : "Upload and validate"}
            </Button>
          </>
        )
      }
    >
      <form className="flex flex-col gap-3" onSubmit={submit}>
        <p className="text-[13.5px] text-muted">
          Columns are mapped to the canonical schema automatically, then every row runs the data-quality rules. Rejected rows never reach the transaction table. Synthetic data only.
        </p>
        <Field label="CSV file">{(props) => <TextInput {...props} type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />}</Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Source system">{(props) => <TextInput {...props} value={sourceSystem} onChange={(e) => setSourceSystem(e.target.value)} />}</Field>
          <Field label="Data owner">{(props) => <TextInput {...props} value={owner} onChange={(e) => setOwner(e.target.value)} />}</Field>
        </div>
        <Checkbox label="This file contains synthetic data only" checked={synthetic} onChange={(e) => setSynthetic(e.target.checked)} />
        {missing.length > 0 && !busy && !result ? <p className="text-xs text-muted">To upload, {missing.join(", ")}.</p> : null}
      </form>
      {error ? (
        <Callout tone="bad" role="alert" className="mt-3">
          {error}
        </Callout>
      ) : null}
      {result ? (
        <Callout tone={result.run.status === "completed" ? "ok" : "warn"} role="status" title={`Run ${words(result.run.status).toLowerCase()}.`} className="mt-3">
          {result.run.accepted_count ?? 0} accepted, {result.run.warning_count ?? 0} with warnings, {result.run.rejected_count ?? 0} rejected of {result.run.row_count ?? 0} rows.
          {Object.keys(result.issue_counts_by_type).length > 0
            ? ` Issues: ${Object.entries(result.issue_counts_by_type)
                .map(([type, n]) => `${words(type)} ${n}`)
                .join(", ")}.`
            : " No data-quality issues."}
        </Callout>
      ) : null}
    </Dialog>
  );
}

/** Row-level exceptions for live ingestion runs, shown above the seeded sample exceptions. */
export function LiveExceptions() {
  const profile = useLiveProfile();
  const runs = useLiveBatches();
  const [runId, setRunId] = useState<string | null>(null);
  const [rowId, setRowId] = useState<string | null>(null);
  if (!profile) return null;
  const canSteward = LIVE_ROLES[profile.role].canSteward;
  const active = runId ?? runs.data?.[0]?.runId ?? null;

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
