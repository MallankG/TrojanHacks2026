"use client";

import Link from "next/link";
import { useState } from "react";
import { errorMessage, useLive } from "@/lib/live/api";
import { dataOpsApi, type SourceFeed, type SourceStatus } from "@/lib/live/api-data-ops";
import { RUN_TONE, SEVERITY_TONE, utc, words } from "@/lib/live/format";
import { useLiveProfile } from "@/lib/live/session";
import { ROUTES } from "@/lib/routes";
import type { Tone } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Card, Panel } from "@/components/ui/card";
import { KeyValueList } from "@/components/ui/key-value";
import { Pill } from "@/components/ui/pill";
import { DataTable, Td, Th } from "@/components/ui/table";
import { LiveData } from "./live-state";

const SOURCE_STATUS: Record<SourceStatus, [string, Tone]> = {
  current: ["Current", "ok"],
  late: ["Late", "warn"],
  has_rejections: ["Has rejections", "info"],
  failed: ["Last run failed", "bad"],
  no_runs: ["No runs", "mute"],
};

function pct(rate: number | null): string {
  return rate === null ? "n/a" : `${(rate * 100).toFixed(1)}%`;
}

function age(hours: number | null): string {
  if (hours === null) return "Never";
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} min ago`;
  if (hours < 48) return `${Math.round(hours)} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

/** Live source feeds (`GET /ingestion/sources`), one card per source system. Renders nothing without a live session. */
export function LiveSourceCards() {
  const profile = useLiveProfile();
  const sources = useLive(async () => (profile ? dataOpsApi.sources() : null), "runs:sources");
  if (!profile) return null;
  return (
    <LiveData query={sources} label="source feeds">
      {(data) =>
        !data || data.items.length === 0 ? (
          <Card className="flex flex-col gap-1 px-4 py-3.5">
            <b>No live source feeds yet</b>
            <span className="text-xs text-muted">Upload a CSV batch to register its source system.</span>
          </Card>
        ) : (
          <>
            {data.items.map((source) => (
              <LiveSourceCard key={source.source_system} source={source} />
            ))}
          </>
        )
      }
    </LiveData>
  );
}

function LiveSourceCard({ source }: { source: SourceFeed }) {
  const [label, tone] = SOURCE_STATUS[source.status];
  return (
    <Card className="flex flex-col gap-2 px-4 py-3.5">
      <div className="flex items-start justify-between gap-2">
        <b className="break-words">{source.source_system}</b>
        <span className="flex shrink-0 gap-1">
          <Pill tone="ok">Live</Pill>
          <Pill tone={tone}>{label}</Pill>
        </span>
      </div>
      <span className="text-xs text-muted">Owner: {source.owners.join(", ") || "Not recorded"}</span>
      <span className="text-xs text-muted">
        Schema {source.schema_versions.join(", ") || "Not recorded"} · last upload {age(source.hours_since_upload)} · SLA {source.freshness_sla_hours} h
      </span>
      <span className="text-[12.5px]">
        {source.run_count} run{source.run_count === 1 ? "" : "s"} · {source.rows.toLocaleString("en-US")} rows · Accepted {pct(source.accepted_rate)} · Warnings {pct(source.warning_rate)} · Rejected{" "}
        {pct(source.rejected_rate)}
      </span>
      <span className="text-[12.5px]">
        {source.open_issue_count > 0 ? (
          <Link href={ROUTES.dataQuality} className="font-semibold text-brand-link underline underline-offset-2">
            {source.open_issue_count} open data-quality issue{source.open_issue_count === 1 ? "" : "s"}
          </Link>
        ) : (
          "No open data-quality issues"
        )}
      </span>
      {source.status_reasons.length > 0 ? <span className="text-xs text-muted">{source.status_reasons.join(" · ")}</span> : null}
    </Card>
  );
}

/** File, schema, owner and field mapping of a live batch, with the original file and its exceptions one click away. */
export function LiveBatchFacts({ runId }: { runId: string }) {
  const detail = useLive(() => dataOpsApi.run(runId), `runs:detail:${runId}`);
  return (
    <LiveData query={detail} label="batch detail">
      {({ run }) => {
        const file = run.source_files;
        const mapping = Object.entries(run.field_mapping ?? {}).sort(([a], [b]) => a.localeCompare(b));
        return (
          <div className="flex flex-col gap-2.5">
            <KeyValueList
              items={[
                ["Run status", <Pill key="s" tone={RUN_TONE[run.status] ?? "mute"}>{words(run.status)}</Pill>],
                ["File", file?.filename ?? "Not recorded"],
                ["File hash (SHA-256)", <span key="h" className="font-mono text-xs break-all">{file?.checksum_sha256 ?? "Not recorded"}</span>],
                ["Schema", run.schema_version],
                ["Pipeline", run.pipeline_version ?? "Not recorded"],
                ["Source system", file?.source_system ?? "Not recorded"],
                ["Owner", file?.data_owner ?? "Not recorded"],
                ["Uploaded", utc(file?.uploaded_at)],
                ["Size", file?.byte_size != null ? `${file.byte_size.toLocaleString("en-US")} bytes` : "Not recorded"],
                ["Data", file ? (file.is_synthetic ? "Synthetic" : "Not marked synthetic") : "Not recorded"],
              ]}
            />
            <div className="flex flex-wrap gap-2">
              {file ? <DownloadOriginal fileId={file.id} /> : null}
              <Link href={ROUTES.dataQuality} className="self-center text-[13px] font-semibold text-brand-link underline underline-offset-2">
                Quality exceptions for this batch
              </Link>
            </div>
            <details>
              <summary className="cursor-pointer text-[13px] font-semibold">Field mapping used ({mapping.length} fields)</summary>
              <DataTable caption="Field mapping used by this run" className="mt-2">
                <thead>
                  <tr>
                    <Th>Canonical field</Th>
                    <Th>Source column</Th>
                  </tr>
                </thead>
                <tbody>
                  {mapping.map(([field, column]) => (
                    <tr key={field}>
                      <Td className="font-mono text-xs">{field}</Td>
                      <Td className="font-mono text-xs">{column}</Td>
                    </tr>
                  ))}
                </tbody>
              </DataTable>
            </details>
          </div>
        );
      }}
    </LiveData>
  );
}

function DownloadOriginal({ fileId }: { fileId: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const download = async () => {
    setBusy(true);
    setError(null);
    try {
      // Signed URLs live five minutes, so fetch one on demand rather than rendering a stale link.
      const { url } = await dataOpsApi.downloadUrl(fileId);
      window.location.assign(url);
    } catch (problem) {
      setError(errorMessage(problem));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <Button size="sm" variant="ghost" disabled={busy} onClick={() => void download()}>
        {busy ? "Preparing download" : "Download original file"}
      </Button>
      {error ? (
        <span role="alert" className="self-center text-xs font-semibold text-danger-ink">
          {error}
        </span>
      ) : null}
    </>
  );
}

/** The real canonical schema and data-quality rule catalog for the Contracts and catalog tab. */
export function LiveContracts() {
  const schema = useLive(() => dataOpsApi.schema(), "schema");
  const rules = useLive(() => dataOpsApi.rules(), "rules");
  const sources = useLive(() => dataOpsApi.sources(), "runs:sources");
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start gap-4">
        <Card className="min-w-0 flex-[2_1_560px] overflow-hidden">
          <div className="px-5 py-4">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-base font-semibold">Canonical schema {schema.data?.schema_version ?? ""}</h2>
              <Pill tone="ok">Live</Pill>
            </div>
            <span className="text-xs text-muted">Every upload is mapped to these fields and checked at ingestion. Rejected rows are kept as evidence and never reach transactions.</span>
          </div>
          <div className="px-5 pb-4">
            <LiveData query={schema} label="canonical schema">
              {(s) => (
                <DataTable caption="Canonical schema fields">
                  <thead>
                    <tr>
                      <Th>Field</Th>
                      <Th>Type</Th>
                      <Th>Required</Th>
                      <Th>Rule</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {s.fields.map((f) => (
                      <tr key={f.field}>
                        <Td className="font-mono">{f.field}</Td>
                        <Td>{f.type}</Td>
                        <Td>{f.required ? "Yes" : "No"}</Td>
                        <Td className="text-xs text-muted">
                          {f.description}
                          {f.rule_ids.length > 0 ? <span className="block font-mono">{f.rule_ids.join(", ")}</span> : null}
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </DataTable>
              )}
            </LiveData>
          </div>
        </Card>
        <div className="flex flex-[1_1_340px] flex-col gap-4">
          <Panel>
            <h2 className="text-base font-semibold">Catalog entry</h2>
            <LiveData query={sources} label="catalog entry">
              {(src) => (
                <KeyValueList
                  items={[
                    ["Schema", schema.data?.schema_version ?? "Loading"],
                    ["Rules version", rules.data?.rules_version ?? "Loading"],
                    ["Source feeds", src.total],
                    ["Owners", [...new Set(src.items.flatMap((s) => s.owners))].join(", ") || "None yet"],
                    ["Purpose", "Transaction monitoring and investigation (synthetic data)"],
                    ["Freshness SLA", `${src.freshness_sla_hours} h per source`],
                    ["Last upload", utc(src.items[0]?.last_upload_at)],
                  ]}
                />
              )}
            </LiveData>
          </Panel>
          {schema.data ? (
            <Panel>
              <h2 className="text-base font-semibold">Accepted values</h2>
              <KeyValueList
                items={[
                  ["Channels", schema.data.channels.join(", ")],
                  ["Currencies", schema.data.currencies.join(", ")],
                ]}
              />
            </Panel>
          ) : null}
        </div>
      </div>
      <Card className="overflow-hidden">
        <div className="px-5 py-4">
          <h2 className="text-base font-semibold">Data-quality rule catalog {rules.data ? `· ${rules.data.rules_version}` : ""}</h2>
          <span className="text-xs text-muted">Error and critical findings reject; warnings keep the row with a caveat; info is recorded only.</span>
        </div>
        <div className="px-5 pb-4">
          <LiveData query={rules} label="rule catalog">
            {(r) => (
              <DataTable caption="Data-quality rules">
                <thead>
                  <tr>
                    <Th>Rule</Th>
                    <Th>Checks</Th>
                    <Th>Applies to</Th>
                    <Th>Severity</Th>
                    <Th>On failure</Th>
                  </tr>
                </thead>
                <tbody>
                  {r.items.map((rule) => (
                    <tr key={rule.rule_id}>
                      <Td className="font-mono text-xs">{rule.rule_id}</Td>
                      <Td className="text-[13px]">
                        {rule.checks}
                        <span className="block text-xs text-muted">{words(rule.issue_type)}</span>
                      </Td>
                      <Td className="font-mono text-xs">{rule.scope === "file" ? "Whole file" : rule.fields.join(", ") || "Whole row"}</Td>
                      <Td>
                        <Pill tone={SEVERITY_TONE[rule.severity]}>{words(rule.severity)}</Pill>
                      </Td>
                      <Td className="text-xs">{rule.outcome}</Td>
                    </tr>
                  ))}
                </tbody>
              </DataTable>
            )}
          </LiveData>
        </div>
      </Card>
      <Callout tone="info" title="Below: the sample contract scenario.">
        The ach.v4 contract, its mapping versions and roll-back are seeded sample content. Live runs record the field mapping each one used; open a live batch on Sources and batches to see it.
      </Callout>
    </div>
  );
}
