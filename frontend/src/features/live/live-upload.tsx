"use client";

import { useRef, useState, type FormEvent } from "react";
import { ApiError } from "@/lib/api/client";
import { runToBatch } from "@/lib/live/adapters";
import { errorMessage, invalidateLive, liveApi, type UploadResult } from "@/lib/live/api";
import { dataOpsApi, type MappingPreview } from "@/lib/live/api-data-ops";
import { SEVERITY_TONE, words } from "@/lib/live/format";
import { closeModal, selectBatch, toast } from "@/lib/store/actions";
import type { Tone } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox, Field, Select, TextInput } from "@/components/ui/field";
import { Pill } from "@/components/ui/pill";
import { DataTable, Td, Th } from "@/components/ui/table";

type PreviewField = MappingPreview["fields"][number];

const MATCH_LABEL: Record<NonNullable<PreviewField["match"]>, string> = {
  exact: "Same name",
  alias: "Known alias",
  manual: "Set by you",
  unmapped: "Left unmapped",
};

const ROW_RESULT: Record<string, [string, Tone]> = {
  accepted: ["Accepted", "ok"],
  accepted_with_warnings: ["Accepted with warnings", "info"],
  rejected: ["Rejected", "bad"],
};

const UNMAPPED = "";

/**
 * Real CSV upload to the backend, opened from "Upload CSV batch" (PRD §23.1). Step 1 picks the file and
 * its owner; step 2 previews the column mapping (`POST /ingestion/preview-mapping`), lets the user
 * override it, dry-runs the data-quality rules on the first rows, then uploads with that exact mapping.
 */
export function LiveUploadDialog() {
  const [file, setFile] = useState<File | null>(null);
  const [sourceSystem, setSourceSystem] = useState("core-banking-ach");
  const [owner, setOwner] = useState("Payments ops");
  const [synthetic, setSynthetic] = useState(true);
  const [step, setStep] = useState<"choose" | "map">("choose");
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<MappingPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<UploadResult | null>(null);
  const previewSeq = useRef(0);

  const missing = [!file && "choose a CSV file", !sourceSystem.trim() && "enter a source system", !owner.trim() && "enter a data owner"].filter(Boolean);

  const runPreview = async (nextOverrides: Record<string, string>) => {
    if (!file) return;
    const seq = ++previewSeq.current;
    const form = new FormData();
    form.set("file", file);
    if (Object.keys(nextOverrides).length > 0) form.set("field_mapping", JSON.stringify(nextOverrides));
    setPreviewing(true);
    setError(null);
    try {
      const next = await dataOpsApi.previewMapping(form);
      // A later change may have started another preview; only the newest one may update the screen.
      if (seq === previewSeq.current) {
        setPreview(next);
        setStep("map");
      }
    } catch (problem) {
      if (seq === previewSeq.current) setError(errorMessage(problem));
    } finally {
      if (seq === previewSeq.current) setPreviewing(false);
    }
  };

  const chooseFile = (next: File | null) => {
    setFile(next);
    setPreview(null);
    setOverrides({});
    setError(null);
  };

  const setColumn = (field: string, column: string) => {
    const next = { ...overrides, [field]: column };
    setOverrides(next);
    void runPreview(next);
  };

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!file || !preview || !preview.can_upload || missing.length > 0) return;
    // Send the mapping exactly as previewed, with every unmapped field explicit, so the run matches the preview.
    const fieldMapping = Object.fromEntries(preview.fields.map((f) => [f.field, f.column ?? UNMAPPED]));
    const form = new FormData();
    form.set("file", file);
    form.set("source_system", sourceSystem.trim());
    form.set("data_owner", owner.trim());
    form.set("is_synthetic", String(synthetic));
    form.set("field_mapping", JSON.stringify(fieldMapping));
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

  const footer = result ? (
    <Button onClick={closeModal}>Done</Button>
  ) : step === "choose" ? (
    <>
      <Button variant="ghost" onClick={closeModal}>
        Cancel
      </Button>
      <Button disabled={previewing || missing.length > 0} onClick={() => void runPreview(overrides)}>
        {previewing ? "Reading file" : "Preview mapping"}
      </Button>
    </>
  ) : (
    <>
      <Button variant="ghost" disabled={busy} onClick={() => setStep("choose")}>
        Back
      </Button>
      <Button disabled={busy || previewing || !preview?.can_upload} onClick={() => void submit()}>
        {busy ? "Validating" : "Upload and validate"}
      </Button>
    </>
  );

  return (
    <Dialog eyebrow={`Live backend · step ${step === "choose" ? 1 : 2} of 2`} title={step === "choose" ? "Upload CSV batch" : "Check the column mapping"} size={step === "map" ? "lg" : "md"} onClose={closeModal} footer={footer}>
      {step === "choose" ? (
        <form className="flex flex-col gap-3" onSubmit={(e) => (e.preventDefault(), void runPreview(overrides))}>
          <p className="text-[13.5px] text-muted">
            Next you will see how the columns map to the canonical schema and how the first rows fare against the data-quality rules. Nothing is written until you upload. Synthetic data only.
          </p>
          <Field label="CSV file" hint={file ? `${file.name} · ${file.size.toLocaleString("en-US")} bytes` : undefined}>
            {(props) => <TextInput {...props} type="file" accept=".csv,text/csv" onChange={(e) => chooseFile(e.target.files?.[0] ?? null)} />}
          </Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Source system">{(props) => <TextInput {...props} value={sourceSystem} onChange={(e) => setSourceSystem(e.target.value)} />}</Field>
            <Field label="Data owner">{(props) => <TextInput {...props} value={owner} onChange={(e) => setOwner(e.target.value)} />}</Field>
          </div>
          <Checkbox label="This file contains synthetic data only" checked={synthetic} onChange={(e) => setSynthetic(e.target.checked)} />
          {missing.length > 0 ? <p className="text-xs text-muted">To continue, {missing.join(", ")}.</p> : null}
        </form>
      ) : preview ? (
        <MappingStep preview={preview} previewing={previewing} locked={busy || result !== null} onChange={setColumn} />
      ) : null}
      {error ? (
        <Callout tone="bad" role="alert">
          {error}
        </Callout>
      ) : null}
      {result ? (
        <Callout tone={result.run.status === "completed" ? "ok" : "warn"} role="status" title={`Run ${words(result.run.status).toLowerCase()}.`}>
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

function MappingStep({ preview, previewing, locked, onChange }: { preview: MappingPreview; previewing: boolean; locked: boolean; onChange: (field: string, column: string) => void }) {
  const required = preview.fields.filter((f) => f.required);
  const mappedRequired = required.filter((f) => f.column && f.column_present).length;
  const counts = preview.rows.reduce<Record<string, number>>((acc, r) => (r.status ? { ...acc, [r.status]: (acc[r.status] ?? 0) + 1 } : acc), {});
  const blocking = preview.schema_issues.filter((i) => i.severity === "critical" || i.severity === "error");
  const sampleColumns = required.filter((f) => f.column);

  return (
    <div className="flex flex-col gap-3.5" aria-busy={previewing}>
      <p className="text-[13.5px] text-muted">
        <b className="text-ink">{preview.filename}</b> has {preview.headers.length} columns. {mappedRequired} of {required.length} required fields are mapped
        {preview.unmapped_columns.length > 0 ? `; not used: ${preview.unmapped_columns.join(", ")}` : ""}. Change a mapping with its select; the preview updates.
      </p>
      {blocking.length > 0 ? (
        <Callout tone="warn" role="alert" title="Upload is blocked until every required field has a column.">
          {blocking.map((i) => i.message).join(" ")}
        </Callout>
      ) : null}

      <DataTable caption="Column mapping to the canonical schema">
        <thead>
          <tr>
            <Th>Canonical field</Th>
            <Th>Type</Th>
            <Th>Source column</Th>
            <Th>How it was matched</Th>
          </tr>
        </thead>
        <tbody>
          {preview.fields.map((f) => (
            <tr key={f.field}>
              <Td>
                <span className="font-mono text-xs">{f.field}</span>
                {f.required ? (
                  <Pill tone="info" className="ml-1.5">
                    Required
                  </Pill>
                ) : null}
              </Td>
              <Td className="text-xs text-muted">{f.type}</Td>
              <Td className="min-w-[180px]">
                <Select aria-label={`Source column for ${f.field}`} title={f.description} value={f.column ?? UNMAPPED} disabled={locked} onChange={(e) => onChange(f.field, e.target.value)}>
                  <option value={UNMAPPED}>Not mapped</option>
                  {preview.headers.map((h) => (
                    <option key={h} value={h}>
                      {h}
                    </option>
                  ))}
                </Select>
              </Td>
              <Td className="text-xs">
                {f.match ? MATCH_LABEL[f.match] : f.required ? <span className="font-semibold text-danger-ink">Missing (required)</span> : <span className="text-muted">No matching column</span>}
              </Td>
            </tr>
          ))}
        </tbody>
      </DataTable>

      <div>
        <h3 className="text-[13.5px] font-semibold">Preview of the first {preview.sampled_rows} rows</h3>
        <p className="text-xs text-muted">
          {preview.can_upload
            ? `Dry run of the data-quality rules: ${Object.entries(counts)
                .map(([status, n]) => `${n} ${ROW_RESULT[status]?.[0].toLowerCase() ?? status}`)
                .join(", ")}. Duplicates within the rest of the file are only checked on upload.`
            : "Rows are checked once the required fields are mapped."}
        </p>
      </div>
      <DataTable caption="Preview rows with their dry-run result">
        <thead>
          <tr>
            <Th>Row</Th>
            <Th>Result</Th>
            {sampleColumns.map((f) => (
              <Th key={f.field} className="font-mono">
                {f.field}
              </Th>
            ))}
            <Th>Findings</Th>
          </tr>
        </thead>
        <tbody>
          {preview.rows.map((row) => {
            const [label, tone] = row.status ? ROW_RESULT[row.status] : ["Not checked", "mute" as Tone];
            return (
              <tr key={row.row_number}>
                <Td>{row.row_number}</Td>
                <Td>
                  <Pill tone={tone}>{label}</Pill>
                </Td>
                {sampleColumns.map((f) => (
                  <Td key={f.field} className="max-w-[160px] truncate font-mono text-xs">
                    {row.values[f.column!] ?? ""}
                  </Td>
                ))}
                <Td className="min-w-[200px] text-xs">
                  {row.issues.length === 0
                    ? "None"
                    : row.issues.map((i) => (
                        <span key={`${i.rule_id}-${i.field_name}`} className="block">
                          <Pill tone={SEVERITY_TONE[i.severity]} className="mr-1">
                            {words(i.severity)}
                          </Pill>
                          <span className="font-mono">{i.rule_id}</span> {i.message}
                        </span>
                      ))}
                </Td>
              </tr>
            );
          })}
        </tbody>
      </DataTable>
      {previewing ? (
        <p role="status" className="text-xs text-muted">
          Updating the preview…
        </p>
      ) : null}
    </div>
  );
}
