"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { useLive } from "@/lib/live/api";
import { governanceApi, type LineageStep, type OfflineCard } from "@/lib/live/api-governance";
import { caseLabel, friendlyModel, friendlyPipeline, shortId, utc, words } from "@/lib/live/format";
import { useLiveProfile } from "@/lib/live/session";
import { ROUTES } from "@/lib/routes";
import { Button } from "@/components/ui/button";
import { CardHeader, Panel } from "@/components/ui/card";
import { TextInput } from "@/components/ui/field";
import { KeyValueList } from "@/components/ui/key-value";
import { EmptyState } from "@/components/ui/misc";
import { Pill } from "@/components/ui/pill";
import { DataTable, Td, Th, Tr } from "@/components/ui/table";
import { LiveData } from "./live-state";

/**
 * Live panels for the governance tabs and the detector comparison page. Each renders nothing without a
 * live session, so the seeded tab content below it stays the demo view.
 */

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "Not available" : `${(v * 100).toFixed(1)}%`);
const metric = (v: unknown) => (typeof v === "number" ? (Math.abs(v) < 1 ? v.toFixed(3) : v.toLocaleString("en-US")) : String(v));

function LivePanel({ title, description, children }: { title: string; description: string; children: React.ReactNode }) {
  if (!useLiveProfile()) return null;
  return (
    <Panel>
      <CardHeader eyebrow="Live backend" title={title} description={description} />
      {children}
    </Panel>
  );
}

export function LiveLineage() {
  const [ref, setRef] = useState("");
  const [submitted, setSubmitted] = useState("");
  const lookup = useLive(() => (submitted ? governanceApi.lookup(submitted) : Promise.resolve(null)), `lineage-lookup:${submitted}`);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    setSubmitted(ref.trim());
  };
  return (
    <LivePanel title="Lineage explorer" description="Source file → ingestion run → source row → transaction → signals → alert → case → decision, for any reference.">
      <form className="flex flex-wrap gap-2" onSubmit={submit}>
        <label className="sr-only" htmlFor="lineage-ref">
          Reference
        </label>
        <TextInput id="lineage-ref" className="max-w-[360px]" value={ref} onChange={(e) => setRef(e.target.value)} placeholder="TX-90001, AL-7 or CASE-0001" />
        <Button type="submit" variant="ghost" disabled={!ref.trim()}>
          Trace lineage
        </Button>
      </form>
      {submitted ? (
        <LiveData query={lookup} label="lineage">
          {(l) =>
            !l || !l.selected ? (
              <EmptyState title="No record matches that reference." description="Try a transaction reference, an alert number or a case number." />
            ) : (
              <div className="flex flex-col gap-2">
                <p className="text-[13.5px]">
                  <b>{l.selected.label}</b> <span className="text-muted">{l.selected.detail}</span>
                </p>
                <ol className="flex flex-col gap-1.5">
                  {l.chain.map((step) => (
                    <ChainStep key={step.key} step={step} />
                  ))}
                </ol>
              </div>
            )
          }
        </LiveData>
      ) : null}
    </LivePanel>
  );
}

function ChainStep({ step }: { step: LineageStep }) {
  const labels = step.items
    .slice(0, 6)
    .map((item) => String(item.label ?? item.transaction_ref ?? item.filename ?? item.signal_name ?? item.id ?? "").slice(0, 40))
    .filter(Boolean);
  return (
    <li className="flex flex-wrap items-center gap-2 rounded-[10px] border border-line-soft px-3 py-2 text-[13px]">
      <Pill tone={step.linked ? "ok" : "mute"}>{step.linked ? `${step.count} linked` : "Not linked"}</Pill>
      <b>{step.title}</b>
      <span className="font-mono text-xs text-muted">{labels.join(" · ")}</span>
      {step.note ? <span className="text-xs text-muted">{step.note}</span> : null}
    </li>
  );
}

function OfflineCards({ cards }: { cards: OfflineCard[] }) {
  if (cards.length === 0) return null;
  return (
    <div className="flex flex-col gap-1.5">
      <b className="text-[13px]">Offline training results (ML teammate&apos;s model cards, read-only)</b>
      {cards.map((c) => {
        const card = c.card ?? {};
        return (
          <KeyValueList
            key={c.artifact_set}
            items={[
              ["Artifact set", c.artifact_set],
              ["Model", `${String(card.selected_model ?? card.model_name ?? "Unknown")} ${String(card.model_version ?? "")}`],
              ["Feature version", String(card.feature_version ?? "Not recorded")],
              ["Labels", c.metrics_are_demonstration ? "Synthetic data: demonstration metrics, not real-world validation" : "As reported"],
              ...(c.card_error ? ([["Card", c.card_error]] as Array<[string, string]>) : []),
            ]}
          />
        );
      })}
    </div>
  );
}

export function LiveModelRegistry() {
  const registry = useLive(() => governanceApi.registry(), "registry");
  return (
    <LivePanel title="Model registry" description="Every model and rule set that produced signals in this tenant, with its versions and recorded metrics.">
      <LiveData query={registry} label="model registry">
        {(r) => (
          <>
            {r.models.length === 0 ? (
              <EmptyState title="No model runs yet." description="Run detection from Risk intelligence to register the rule engine, anomaly baseline and ranker." />
            ) : (
              <DataTable caption="Registered models">
                <thead>
                  <tr>
                    <Th>Model</Th>
                    <Th>Kind</Th>
                    <Th>Version</Th>
                    <Th>Runs</Th>
                    <Th>Status</Th>
                    <Th>Headline metrics</Th>
                  </tr>
                </thead>
                <tbody>
                  {r.models.map((m) => (
                    <Tr key={`${m.model_name}-${m.model_version}`}>
                      <Td className="text-xs font-semibold">{friendlyModel(m.model_name)}</Td>
                      <Td>{words(m.model_kind)}</Td>
                      <Td className="font-mono text-xs">{m.model_version}</Td>
                      <Td>
                        {m.completed_run_count} of {m.run_count}
                      </Td>
                      <Td>
                        <Pill tone={m.status === "completed" ? "ok" : m.status === "failed" ? "bad" : "info"}>{words(m.status)}</Pill>
                      </Td>
                      <Td className="text-xs">
                        {Object.entries(m.headline_metrics)
                          .map(([k, v]) => `${words(k)} ${metric(v)}`)
                          .join(" · ") || "None recorded"}
                      </Td>
                    </Tr>
                  ))}
                </tbody>
              </DataTable>
            )}
            <OfflineCards cards={r.offline_model_cards} />
          </>
        )}
      </LiveData>
    </LivePanel>
  );
}

export function LivePipelines() {
  const runs = useLive(() => governanceApi.pipelines(), "pipelines");
  return (
    <LivePanel title="Pipeline runs" description="Ingestion and model runs in one timeline, newest first.">
      <LiveData query={runs} label="pipeline runs">
        {(p) =>
          p.items.length === 0 ? (
            <EmptyState title="No pipeline runs yet." />
          ) : (
            <DataTable caption="Pipeline runs">
              <thead>
                <tr>
                  <Th>Started</Th>
                  <Th>Pipeline</Th>
                  <Th>Name</Th>
                  <Th>Status</Th>
                  <Th>Duration</Th>
                  <Th>Rows</Th>
                </tr>
              </thead>
              <tbody>
                {p.items.map((item) => (
                  <Tr key={`${item.kind}-${item.id}`}>
                    <Td className="whitespace-nowrap text-xs">{utc(item.started_at)}</Td>
                    <Td>{item.kind === "ingestion" ? "Ingestion Pipeline" : words(item.model_kind ?? item.kind)}</Td>
                    <Td className="text-xs">{item.kind === "ingestion" ? friendlyPipeline(item.name) : friendlyModel(item.name)}</Td>
                    <Td>
                      <Pill tone={item.status === "failed" ? "bad" : item.status.startsWith("completed") ? "ok" : "info"}>{words(item.status)}</Pill>
                      {item.error_message ? <span className="block text-xs text-danger-ink">{item.error_message}</span> : null}
                    </Td>
                    <Td>{item.duration_seconds === null ? "Running" : `${item.duration_seconds.toFixed(1)} s`}</Td>
                    <Td className="text-xs">
                      {Object.entries(item.rows)
                        .filter(([, v]) => v !== null)
                        .map(([k, v]) => `${words(k)} ${v}`)
                        .join(" · ")}
                    </Td>
                  </Tr>
                ))}
              </tbody>
            </DataTable>
          )
        }
      </LiveData>
    </LivePanel>
  );
}

export function LiveDataHealth() {
  const health = useLive(() => governanceApi.health(), "health");
  return (
    <LivePanel title="Data health trend" description="Rejected and warning rates per ingestion run. A trend needs at least two runs.">
      <LiveData query={health} label="data health">
        {(h) => (
          <>
            <KeyValueList
              items={[
                ["Trend", h.rejected_rate_trend.status === "ok" ? `${pct(h.rejected_rate_trend.baseline_rejected_rate)} → ${pct(h.rejected_rate_trend.latest_rejected_rate)}` : "Not enough runs"],
                ["Runs compared", h.rejected_rate_trend.runs_available],
                ["Issues by type", Object.entries(h.issue_totals).map(([k, v]) => `${words(k)} ${v}`).join(" · ") || "None"],
              ]}
            />
            {h.runs.length > 0 ? (
              <DataTable caption="Ingestion run health">
                <thead>
                  <tr>
                    <Th>File</Th>
                    <Th>Started</Th>
                    <Th>Rows</Th>
                    <Th>Rejected</Th>
                    <Th>Warnings</Th>
                  </tr>
                </thead>
                <tbody>
                  {h.runs.map((r) => (
                    <Tr key={r.run_id}>
                      <Td className="font-mono text-xs">{r.filename}</Td>
                      <Td className="whitespace-nowrap text-xs">{utc(r.started_at)}</Td>
                      <Td>{r.row_count}</Td>
                      <Td>{pct(r.rejected_rate)}</Td>
                      <Td>{pct(r.warning_rate)}</Td>
                    </Tr>
                  ))}
                </tbody>
              </DataTable>
            ) : null}
          </>
        )}
      </LiveData>
    </LivePanel>
  );
}

export function LiveExports() {
  const exports = useLive(() => governanceApi.exports(), "exports");
  return (
    <LivePanel title="Evidence exports" description="Case evidence packages exported from the backend, with their SHA-256 hashes. Case-support artifacts, not FinCEN filings.">
      <LiveData query={exports} label="exports">
        {(e) =>
          e.items.length === 0 ? (
            <EmptyState title="No evidence packages exported yet." description="Export one from a live case's report tab." />
          ) : (
            <DataTable caption="Evidence exports">
              <thead>
                <tr>
                  <Th>When</Th>
                  <Th>Case</Th>
                  <Th>By</Th>
                  <Th>Manifest SHA-256</Th>
                </tr>
              </thead>
              <tbody>
                {e.items.map((x) => (
                  <Tr key={x.event_id}>
                    <Td className="whitespace-nowrap text-xs">{utc(x.occurred_at)}</Td>
                    <Td>
                      <Link className="font-mono font-semibold text-brand-link underline underline-offset-2" href={ROUTES.case(x.case_id)}>
                        {caseLabel(x.case_number)}
                      </Link>
                      <span className="block text-xs text-muted">{x.case_title}</span>
                    </Td>
                    <Td>
                      {x.actor_name ?? "Unknown"} ({words(x.actor_role)})
                    </Td>
                    <Td className="font-mono text-xs">{x.manifest_sha256 ? `${x.manifest_sha256.slice(0, 16)}…` : "Not hashed (legacy JSON export)"}</Td>
                  </Tr>
                ))}
              </tbody>
            </DataTable>
          )
        }
      </LiveData>
    </LivePanel>
  );
}

export function LiveDetectorComparison() {
  const data = useLive(() => governanceApi.detectors(), "detectors");
  return (
    <LivePanel title="Detector comparison" description="Each detector's runs on this tenant's data: signals, alerts and typology coverage. Metrics on synthetic data are demonstrations.">
      <LiveData query={data} label="detector comparison">
        {(d) => (
          <>
            {d.detectors.length === 0 ? (
              <EmptyState title="No detector runs yet." description="Run detection from Risk intelligence." />
            ) : (
              <DataTable caption="Detector runs">
                <thead>
                  <tr>
                    <Th>Detector</Th>
                    <Th>Kind</Th>
                    <Th>Completed</Th>
                    <Th>Signals fired</Th>
                    <Th>Alerts</Th>
                    <Th>Typologies</Th>
                  </tr>
                </thead>
                <tbody>
                  {d.detectors.map((r) => (
                    <Tr key={r.model_run_id}>
                      <Td className="font-mono text-xs">
                        {r.model_name} {r.model_version}
                        <span className="block text-muted">run {shortId(r.model_run_id)}</span>
                      </Td>
                      <Td>{words(r.model_kind)}</Td>
                      <Td className="whitespace-nowrap text-xs">{utc(r.completed_at)}</Td>
                      <Td>
                        {r.triggered_count} of {r.signal_count}
                      </Td>
                      <Td>{r.alert_count}</Td>
                      <Td className="text-xs">
                        {Object.entries(r.typologies)
                          .map(([k, v]) => `${words(k)} ${v}`)
                          .join(" · ") || "None"}
                      </Td>
                    </Tr>
                  ))}
                </tbody>
              </DataTable>
            )}
            <KeyValueList
              items={Object.entries(d.overlap).map(([k, v]) => [`Alerts: ${words(k)}`, v] as [string, number])}
            />
            <OfflineCards cards={d.offline_model_cards} />
          </>
        )}
      </LiveData>
    </LivePanel>
  );
}
