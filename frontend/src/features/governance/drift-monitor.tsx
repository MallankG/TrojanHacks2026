"use client";

import { useLayoutEffect, useState } from "react";
import sample from "@/lib/seed/enterprise-demo.json";
import { apiRequest } from "@/lib/api/client";
import { Button, ButtonLink } from "@/components/ui/button";
import { CardHeader, Panel } from "@/components/ui/card";
import { Field, TextArea, TextInput } from "@/components/ui/field";
import { Callout } from "@/components/ui/callout";
import { Pill } from "@/components/ui/pill";

type Scenario = keyof typeof sample.drift;
type Report = (typeof sample.drift)[Scenario];
const percent = (v: number | null) => v === null ? "Unknown" : `${(v * 100).toFixed(1)}%`;

export function DriftMonitor() {
  const [scenario, setScenario] = useState<Scenario>("shifted");
  const [report, setReport] = useState<Report>(sample.drift.shifted);
  const [token, setToken] = useState("");
  const [input, setInput] = useState(JSON.stringify(sample.drift_input, null, 2));
  const [live, setLive] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useLayoutEffect(() => () => { setToken(""); setLive(false); setReport(sample.drift.shifted); }, []);
  const performance = report.performance;
  return <div className="flex flex-col gap-4">
    <Callout tone="info">{live ? "Computed from the submitted snapshots." : "Fixed synthetic monitoring example."} Input and prediction drift are review signals. Measured performance needs mature, reviewed labels.</Callout>
    <Panel>
      <CardHeader title="Data and model monitoring" description={`Compare ${report.reference_snapshot} with ${report.current_snapshot}. Model ${report.model_version} · ${report.feature_version}.`} />
      <div className="flex flex-wrap gap-2">
        {([["stable", "Stable inputs"], ["shifted", "Inputs and performance changed"], ["delayed", "Labels still pending"]] as const).map(([key, label]) => <Button key={key} variant={!live && scenario === key ? "dark" : "ghost"} size="sm" onClick={() => { setScenario(key); setReport(sample.drift[key]); setLive(false); setMessage(""); }}>{label}</Button>)}
      </div>
      <p className="text-xs text-muted">As of {report.as_of}. Baseline bins are fixed to the reference window. Review thresholds are illustrative and configurable in a future policy version.</p>
    </Panel>
    <Panel>
      <CardHeader title="Input data drift and quality" description="Check missingness first, then distributions. A changed feed can explain a changed model output." />
      <div className="overflow-x-auto"><table className="w-full text-left text-sm"><caption className="sr-only">Computed feature drift comparisons</caption><thead><tr className="border-b border-line"><th className="py-2 pr-3">Feature</th><th className="py-2 pr-3">Change</th><th className="py-2 pr-3">Missing-rate change</th><th className="py-2">Review status</th></tr></thead><tbody>
        {report.features.map(f => <tr key={f.feature} className="border-b border-line"><td className="py-3 pr-3"><b>{f.feature}</b><p className="text-xs text-muted">{f.method}</p></td><td className="py-3 pr-3">{f.value === null ? "Insufficient data" : f.value.toFixed(3)}<p className="text-xs text-muted">Watch {f.watch_threshold}; drift {f.drift_threshold}</p></td><td className="py-3 pr-3">{(f.missing_rate_delta * 100).toFixed(1)} percentage points{f.unseen_category_rate !== null && <p className="text-xs">Unseen categories: {percent(f.unseen_category_rate)}</p>}</td><td className="py-3"><Pill tone={f.status === "stable" ? "ok" : f.status === "insufficient_data" ? "mute" : "warn"}>{f.status.replaceAll("_", " ")}</Pill></td></tr>)}
      </tbody></table></div>
      <div className="flex flex-wrap gap-2"><ButtonLink href="/data/quality" size="sm">Inspect quality exceptions</ButtonLink><ButtonLink href="/governance/superpowers" size="sm">Inspect contract and lineage</ButtonLink></div>
    </Panel>
    <div className="grid gap-4 lg:grid-cols-2">
      <Panel><CardHeader title="Prediction drift" description="A shift in scores or alert volume needs investigation; it does not establish lower accuracy." /><dl className="grid grid-cols-2 gap-3 text-sm"><dt>Score PSI</dt><dd>{report.prediction.psi?.toFixed(3) ?? "Unknown"}</dd><dt>Reference alert rate</dt><dd>{percent(report.prediction.reference_alert_rate)}</dd><dt>Current alert rate</dt><dd>{percent(report.prediction.current_alert_rate)}</dd><dt>Fixed threshold</dt><dd>{report.prediction.threshold}</dd></dl></Panel>
      <Panel><CardHeader title="Measured model performance" description="Average precision and top-K metrics use the labeled subset in each window." /><Pill tone={performance.status === "unknown_without_labels" ? "mute" : performance.status === "review_degradation" ? "warn" : "ok"}>{performance.status.replaceAll("_", " ")}</Pill><dl className="grid grid-cols-2 gap-3 text-sm"><dt>Reference / current label coverage</dt><dd>{percent(performance.reference.coverage)} / {percent(performance.current.coverage)}</dd><dt>Reference / current average precision</dt><dd>{percent(performance.reference.average_precision)} / {percent(performance.current.average_precision)}</dd><dt>Average precision change</dt><dd>{performance.average_precision_delta === null ? "Unknown until labels arrive" : `${(performance.average_precision_delta * 100).toFixed(1)} percentage points`}</dd><dt>Current precision@K / recall@K</dt><dd>{percent(performance.current.precision_at_k)} / {percent(performance.current.recall_at_k)}</dd></dl><p className="text-xs text-muted">Calibration metrics are omitted for ranking scores. Check prevalence, review selection and label delay before concluding the model degraded.</p></Panel>
    </div>
    <Panel><CardHeader title="What to check, and where" description="Use the same model, feature definitions and comparable time windows. Segment by payment rail, currency, geography and customer cohort." />
      <ol className="list-decimal space-y-3 pl-5 text-sm">
        <li><b>Data quality:</b> missing fields, renamed columns, enum/timezone/unit changes, duplicates, stale feeds and reconciliation mismatches. Open Data operations → Quality or Data contracts.</li>
        <li><b>Data drift:</b> compare feature distributions, missing rates and unseen categories against a pinned baseline. Check sample counts and seasonal mix before changing thresholds.</li>
        <li><b>Score drift:</b> compare scores, alert rates and detector disagreement at a fixed threshold. Inspect examples behind each shift.</li>
        <li><b>Performance:</b> once outcomes mature, compare average precision, precision@K and recall@K on the same review budget. Track label coverage, positive rate and reviewer bias.</li>
        <li><b>Concept drift:</b> investigate sustained performance changes within comparable cohorts. An input-drift alert alone cannot confirm a changed feature/outcome relationship.</li>
        <li><b>Operations:</b> check ingestion delay, scoring latency, graph timeouts, verification failures, retrieval failures and export success in Enterprise workspace → Operations.</li>
      </ol>
      <div className="flex flex-wrap gap-2"><ButtonLink href="/governance/enterprise" size="sm">Operations and investigation room</ButtonLink><ButtonLink href="/governance/models" size="sm">Model registry</ButtonLink></div>
      <p className="text-xs text-muted">References: <a className="underline" href="https://learn.evidentlyai.com/ml-observability-course/module-2-ml-monitoring-metrics/data-prediction-drift-in-ml" target="_blank" rel="noreferrer">Evidently: data and prediction drift</a> · <a className="underline" href="https://scikit-learn.org/stable/modules/generated/sklearn.metrics.average_precision_score.html" target="_blank" rel="noreferrer">scikit-learn: average precision</a>.</p>
    </Panel>
    <Panel><details><summary className="cursor-pointer font-semibold">Compare your own monitoring snapshots</summary><form className="mt-4 flex flex-col gap-3" onSubmit={async e => {
      e.preventDefault(); setBusy(true); setMessage("");
      try { const response = await apiRequest<{ report: Report }>("/enterprise/drift/check", { method: "POST", headers: { Authorization: `Bearer ${token}` }, body: JSON.parse(input) }); setReport(response.report); setLive(true); setMessage("Report computed and saved in your tenant."); }
      catch { setMessage("Could not compare snapshots. Check the API URL, feature flag, migration, token, model-worker role and input format."); }
      finally { setBusy(false); }
    }}>
      <Field label="Supabase access token" hint="Kept only in page memory. Admin, analyst or model-worker access is required.">{p => <TextInput {...p} type="password" autoComplete="off" value={token} onChange={e => { setToken(e.target.value); setLive(false); setReport(sample.drift[scenario]); }} />}</Field>
      <Field label="Pinned snapshot input (JSON)" hint="Up to 1,000 rows per window. Use the same model and feature version for both windows.">{p => <TextArea {...p} rows={8} className="font-mono text-xs" value={input} onChange={e => setInput(e.target.value)} />}</Field>
      <Button type="submit" disabled={busy || !token}>{busy ? "Comparing…" : "Compute and save comparison"}</Button><p role="status" className="text-sm">{message}</p>
    </form></details></Panel>
    <p className="text-xs text-muted">{report.caveats.join(" ")}</p>
  </div>;
}
