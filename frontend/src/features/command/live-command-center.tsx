"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { liveApi, useLive } from "@/lib/live/api";
import { useDashboardSummary, type DashboardSummary, type FlagStatus } from "@/lib/live/api-command";
import { BAND_LABEL, BAND_TONE, alertLabel, money, utc, words } from "@/lib/live/format";
import { LIVE_ROLES } from "@/lib/live/roles";
import { useLiveProfile } from "@/lib/live/session";
import { ROUTES } from "@/lib/routes";
import type { Tone } from "@/lib/types";
import { buttonClasses } from "@/components/ui/button";
import { Card, CardHeader, Panel } from "@/components/ui/card";
import { EmptyState, Legend, Meter, Skeleton, Swatch } from "@/components/ui/misc";
import { Pill } from "@/components/ui/pill";
import { DataTable, Td, Th } from "@/components/ui/table";
import { LiveData } from "@/features/live/live-state";

/**
 * Screen 1 on live backend data: the same layout as the seeded command center, with every number
 * from `GET /dashboard/summary`. Services the backend does not run yet (graph store, LLM, policy
 * index) are shown as not connected rather than with sample numbers.
 */

export const FLAG_TONE: Record<FlagStatus, Tone> = { ok: "ok", attention: "warn", failed: "bad", not_run: "mute" };
export const FLAG_LABEL: Record<FlagStatus, string> = { ok: "Healthy", attention: "Attention", failed: "Failed", not_run: "Not run yet" };

export function hoursLabel(hours: number | null): string {
  if (hours === null) return "None";
  if (hours < 1) return "Under 1 h";
  if (hours < 48) return `${Math.round(hours)} h`;
  return `${Math.round(hours / 24)} d`;
}

function ageFrom(iso: string | null | undefined, now: number): number | null {
  const at = iso ? Date.parse(iso) : Number.NaN;
  return Number.isNaN(at) ? null : Math.max(0, (now - at) / 3_600_000);
}

/** "$57,900.50 + €100.10": exact per-currency sums from the backend, never converted or mixed. */
function amountLabel(byCurrency: Record<string, string>): string {
  const entries = Object.entries(byCurrency);
  if (entries.length === 0) return money(0);
  const usdFirst = entries.sort(([a], [b]) => (a === "USD" ? -1 : b === "USD" ? 1 : a.localeCompare(b)));
  return usdFirst.map(([currency, amount]) => money(amount, currency)).join(" + ");
}

const LiveTag = () => <Pill tone="ok">Live</Pill>;

function NextActions({ summary }: { summary: DashboardSummary }) {
  const profile = useLiveProfile();
  const role = profile ? LIVE_ROLES[profile.role] : null;
  const { alerts, cases, ingestion } = summary;
  const actions: Array<{ title: string; description: string; cta: string; href: string }> = [];

  if (ingestion.run_count === 0 && role?.canUpload) {
    actions.push({ title: "Upload the first transaction batch", description: "Nothing has been ingested for this tenant yet.", cta: "Open data operations", href: ROUTES.data });
  }
  if (ingestion.open_issues_total > 0) {
    actions.push({
      title: `Review ${ingestion.open_issues_total} open data-quality issue${ingestion.open_issues_total === 1 ? "" : "s"}`,
      description: `${ingestion.open_issues_by_severity.critical + ingestion.open_issues_by_severity.error} blocking. Rows with issues can hide activity from alerts.`,
      cta: "Open data quality",
      href: ROUTES.dataQuality,
    });
  }
  if (role?.canReview && cases && cases.pending_review > 0) {
    actions.push({
      title: `Decide ${cases.pending_review} case${cases.pending_review === 1 ? "" : "s"} pending review`,
      description: "A second person records the disposition. Scores are leads; you decide.",
      cta: "Open cases",
      href: ROUTES.cases,
    });
  }
  if (role?.canInvestigate && alerts.review_first > 0) {
    actions.push({
      title: `Triage ${alerts.review_first} review-first alert${alerts.review_first === 1 ? "" : "s"}`,
      description: alerts.age.past_sla > 0 ? `${alerts.age.past_sla} open alert(s) are past the ${alerts.age.sla_days}-day review SLA.` : "Trace the money flow, then open a case or close with a reason.",
      cta: "Open risk intelligence",
      href: ROUTES.risk,
    });
  }

  return (
    <section aria-label="Next best actions" className="flex flex-wrap items-start gap-6 rounded-[18px] bg-hero p-7 text-white">
      <div className="flex flex-[1_1_300px] flex-col gap-2">
        <div className="eyebrow text-brand-light">Signed in as {profile?.display_name ?? profile?.email ?? "you"}</div>
        <h1 className="text-[28px] font-semibold leading-tight">{actions.length === 0 ? "Nothing needs you right now" : `${actions.length} thing${actions.length === 1 ? "" : "s"} need you today`}</h1>
        <p className="text-sm text-hero-text">From live tenant data, updated {utc(summary.generated_at)}.</p>
      </div>
      <ol className="flex flex-[2_1_520px] flex-col gap-2.5">
        {actions.length === 0 ? (
          <li className="rounded-xl bg-hero-card px-3.5 py-3 text-[13.5px] text-hero-text">
            {alerts.model_run ? "No open alerts, cases or data issues are waiting for your role." : "No ranker run has been published yet. Alerts appear after the model worker scores the uploaded data."}
          </li>
        ) : (
          actions.map((action, i) => (
            <li key={action.title} className="flex flex-wrap items-center gap-3.5 rounded-xl bg-hero-card px-3.5 py-3">
              <span className="inline-flex size-[30px] shrink-0 items-center justify-center rounded-full bg-brand-light font-bold text-sidebar">{i + 1}</span>
              <div className="flex-[1_1_260px]">
                <b className="text-[14.5px]">{action.title}</b>
                <div className="text-[13px] text-hero-text">{action.description}</div>
              </div>
              <Link href={action.href} className={buttonClasses("primary", "sm")}>
                {action.cta}
              </Link>
            </li>
          ))
        )}
      </ol>
    </section>
  );
}

function KpiGrid({ summary }: { summary: DashboardSummary }) {
  const { alerts, ingestion } = summary;
  const amountNote = alerts.estimated_amount.alerts_without_amount > 0 ? `Estimate · ${alerts.estimated_amount.alerts_without_amount} without amount` : "Estimate";
  const kpis: Array<{ label: string; value: string; note: string; tone: Tone; href: string; title?: string }> = [
    { label: "Open alerts", value: String(alerts.open_total), note: alerts.model_run ? `New or under review` : "No ranker run yet", tone: "info", href: ROUTES.risk },
    { label: "Review first", value: String(alerts.review_first), note: `${alerts.by_priority_band.critical} critical, ${alerts.by_priority_band.high} high`, tone: "dark", href: ROUTES.risk },
    { label: "Est. amount, open alerts", value: amountLabel(alerts.estimated_amount.by_currency), note: amountNote, tone: "info", href: ROUTES.risk, title: alerts.estimated_amount.note },
    { label: "Past review SLA", value: String(alerts.age.past_sla), note: `Internal setting: ${alerts.age.sla_days} days`, tone: alerts.age.past_sla > 0 ? "warn" : "ok", href: ROUTES.risk },
    { label: "Connected accounts", value: String(alerts.connected_accounts), note: "Across open alerts", tone: "info", href: ROUTES.graph },
    {
      label: "Open data issues",
      value: String(ingestion.open_issues_total),
      note: ingestion.open_issues_total === 0 ? "Clear" : `${ingestion.open_issues_by_severity.critical + ingestion.open_issues_by_severity.error} blocking`,
      tone: ingestion.open_issues_total === 0 ? "ok" : "warn",
      href: ROUTES.dataQuality,
    },
  ];
  // Case counts are only returned to roles with case access.
  if (summary.cases) {
    kpis.push({
      label: "Open cases",
      value: String(summary.cases.open_total),
      note: `${summary.cases.pending_review} pending review, ${summary.cases.by_status.escalated ?? 0} escalated`,
      tone: summary.cases.pending_review > 0 ? "warn" : "info",
      href: ROUTES.cases,
    });
  }
  return (
    <section aria-label="Key numbers" className="grid grid-cols-[repeat(auto-fit,minmax(170px,1fr))] gap-4">
      {kpis.map((kpi) => (
        <Link key={kpi.label} href={kpi.href} title={kpi.title} className="flex flex-col gap-1.5 rounded-[14px] border border-line bg-white px-4 py-3.5 transition-colors hover:border-[#9fb0c6]">
          <span className="flex items-center justify-between gap-2 text-[12.5px] text-muted">
            {kpi.label}
            <LiveTag />
          </span>
          <span className="break-words text-[28px] font-bold leading-tight tracking-[-0.02em]">{kpi.value}</span>
          <Pill tone={kpi.tone} className="self-start">
            {kpi.note}
          </Pill>
        </Link>
      ))}
    </section>
  );
}

function NoAlerts({ summary }: { summary: DashboardSummary }) {
  return summary.alerts.model_run ? (
    <EmptyState title="No open alerts" description="The latest ranker run has no alerts waiting for triage." />
  ) : (
    <EmptyState title="No alerts yet" description="Alerts appear after the model worker publishes a ranker run for the uploaded data." />
  );
}

function TypologyMix({ summary }: { summary: DashboardSummary }) {
  const mix = summary.alerts.by_typology;
  const max = Math.max(1, ...mix.map((t) => t.count));
  return (
    <Panel>
      <CardHeader title="Typology mix" actions={<span className="flex items-center gap-2 text-xs text-muted">Open alerts, {summary.alerts.open_total} <LiveTag /></span>} />
      {mix.length === 0 ? (
        <NoAlerts summary={summary} />
      ) : (
        mix.map(({ typology, count }) => (
          <div key={typology} className="grid grid-cols-[150px_1fr_28px] items-center gap-2.5 text-[13px]">
            <span>{words(typology)}</span>
            <Meter value={count / max} label={`${words(typology)}: ${count} alerts`} />
            <b className="text-right">{count}</b>
          </div>
        ))
      )}
    </Panel>
  );
}

/** The backend keeps no daily series yet, so the live view shows where the current run's alerts stand. */
function AlertStatus({ summary }: { summary: DashboardSummary }) {
  const { by_status, run_alert_total, model_run } = summary.alerts;
  const rows: Array<[string, number, string]> = [
    ["New", by_status.new ?? 0, "var(--color-brand)"],
    ["Under review", by_status.under_review ?? 0, "var(--color-brand)"],
    ["In a case", by_status.in_case ?? 0, "var(--color-select)"],
    ["Closed, no action", by_status.closed_no_action ?? 0, "#8a96a8"],
    ["Dismissed", by_status.dismissed ?? 0, "#8a96a8"],
  ];
  const max = Math.max(1, ...rows.map(([, n]) => n));
  return (
    <Panel>
      <CardHeader title="Alert status, latest run" actions={<span className="flex items-center gap-2 text-xs text-muted">{run_alert_total} alerts <LiveTag /></span>} />
      {model_run === null ? (
        <NoAlerts summary={summary} />
      ) : (
        <>
          {rows.map(([label, count, color]) => (
            <div key={label} className="grid grid-cols-[150px_1fr_28px] items-center gap-2.5 text-[13px]">
              <span>{label}</span>
              <Meter value={count / max} color={color} label={`${label}: ${count} alerts`} />
              <b className="text-right">{count}</b>
            </div>
          ))}
          <p className="text-xs text-muted">
            {model_run.model_name} {model_run.model_version}, completed {utc(model_run.completed_at)}.
          </p>
        </>
      )}
    </Panel>
  );
}

function AlertAge({ summary }: { summary: DashboardSummary }) {
  const { age, open_total } = summary.alerts;
  const max = Math.max(1, ...age.buckets.map((b) => b.count));
  return (
    <Panel>
      <CardHeader title="Alert age vs SLA" actions={<span className="flex items-center gap-2 text-xs text-muted">Internal setting: {age.sla_days} days <LiveTag /></span>} />
      {open_total === 0 ? (
        <NoAlerts summary={summary} />
      ) : (
        <>
          <div className="flex h-[180px] items-end gap-3.5 pt-2.5">
            {age.buckets.map((bucket) => (
              <div key={bucket.label} className="flex h-full flex-1 flex-col items-center justify-end gap-1.5">
                <b className="text-[13px]">{bucket.count}</b>
                <div className="w-full max-w-14 rounded-t-md" style={{ height: `${Math.round((bucket.count / max) * 140)}px`, background: bucket.past_sla ? "var(--color-attention-stroke)" : "var(--color-brand)" }} />
                <span className="text-center text-xs text-muted">{bucket.label}</span>
              </div>
            ))}
          </div>
          <p className="text-xs text-muted">
            Oldest {hoursLabel(age.oldest_hours)}, median {hoursLabel(age.median_hours)}, {age.past_sla} past SLA.
          </p>
          <Legend
            items={[
              { label: "Within SLA", swatch: <Swatch color="var(--color-brand)" /> },
              { label: "Past SLA", swatch: <Swatch color="var(--color-attention-stroke)" /> },
            ]}
          />
        </>
      )}
    </Panel>
  );
}

function LiveAlertsTable({ summary }: { summary: DashboardSummary }) {
  const queue = useLive(() => liveApi.alerts({ limit: 6 }), `alerts:command:${summary.alerts.model_run?.id ?? "none"}`);
  const now = Date.parse(summary.generated_at);
  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-2.5 px-5 py-4">
        <h2 className="flex items-center gap-2 text-base font-semibold">
          Live alerts <LiveTag />
        </h2>
        <Link href={ROUTES.risk} className={buttonClasses("ghost", "sm")}>
          Open risk intelligence
        </Link>
      </div>
      {queue.error ? (
        <p className="px-5 pb-4 text-[13.5px] text-muted">Could not load the alert queue: {queue.error}</p>
      ) : queue.loading || !queue.data ? (
        <div className="px-5 pb-4">
          <Skeleton lines={3} label="Loading alerts" />
        </div>
      ) : queue.data.items.length === 0 ? (
        <NoAlerts summary={summary} />
      ) : (
        <DataTable caption="Live alerts from the latest ranker run, ranked by priority">
          <thead>
            <tr>
              <Th>Alert</Th>
              <Th>Priority</Th>
              <Th>Typology</Th>
              <Th>Amount</Th>
              <Th>Accounts</Th>
              <Th>Age</Th>
            </tr>
          </thead>
          <tbody>
            {queue.data.items.map((a) => {
              const hours = ageFrom(a.created_at, now);
              const pastSla = hours !== null && hours > summary.alerts.age.sla_days * 24;
              return (
                <tr key={a.id}>
                  <Td>
                    <Link href={ROUTES.riskAlert(a.id)} className={buttonClasses("link", "md", "font-mono")}>
                      {alertLabel(a.alert_number)}
                    </Link>
                  </Td>
                  <Td>
                    <Pill tone={BAND_TONE[a.priority_band]}>{BAND_LABEL[a.priority_band]}</Pill>
                  </Td>
                  <Td>{words(a.typology)}</Td>
                  <Td>{money(a.total_amount, a.amount_currency ?? "USD")}</Td>
                  <Td>{a.connected_account_count ?? "Not recorded"}</Td>
                  <Td>
                    <Pill tone={pastSla ? "warn" : "mute"}>{pastSla ? `Past SLA, ${hoursLabel(hours)}` : `${hoursLabel(hours)}, ${words(a.status).toLowerCase()}`}</Pill>
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </DataTable>
      )}
    </Card>
  );
}

const MODEL_KIND_LABEL: Record<string, string> = { rule_engine: "Rules", supervised: "Supervised", anomaly: "Anomaly", graph_model: "Graph model", ranker: "Ranker" };

function PlatformHealth({ summary }: { summary: DashboardSummary }) {
  const flag = (key: string) => summary.pipeline.find((f) => f.key === key);
  const latest = summary.ingestion.latest_run;
  const completedModels = Object.entries(summary.model_freshness).filter(([, run]) => run !== null);
  const ranker = summary.model_freshness.ranker;
  const notConnected: Tone = "mute";
  const rows: Array<{ label: string; detail: ReactNode; tag: string; tone: Tone; href: string; live: boolean }> = [];

  const ingestion = flag("ingestion");
  if (ingestion) {
    rows.push({
      label: "Ingestion",
      detail: latest
        ? `${latest.filename ?? "Latest batch"}: ${words(latest.status).toLowerCase()} ${utc(latest.completed_at ?? latest.started_at)}. ${summary.ingestion.total_rows.toLocaleString("en-US")} rows in total, ${summary.ingestion.rejected_rate === null ? "0" : (summary.ingestion.rejected_rate * 100).toFixed(1)}% rejected.`
        : ingestion.detail,
      tag: FLAG_LABEL[ingestion.status],
      tone: FLAG_TONE[ingestion.status],
      href: ROUTES.data,
      live: true,
    });
  }
  const quality = flag("data_quality");
  if (quality) {
    const s = summary.ingestion.open_issues_by_severity;
    rows.push({
      label: "Data quality",
      detail: `Open issues: ${s.critical} critical, ${s.error} error, ${s.warning} warning, ${s.info} info.`,
      tag: FLAG_LABEL[quality.status],
      tone: FLAG_TONE[quality.status],
      href: ROUTES.dataQuality,
      live: true,
    });
  }
  const scoring = flag("scoring");
  if (scoring) {
    rows.push({
      label: "Scoring",
      detail: ranker ? `${ranker.model_name ?? "Ranker"} ${ranker.model_version ?? ""} finished ${hoursLabel(ranker.age_hours)} ago. ${scoring.detail}` : scoring.detail,
      tag: FLAG_LABEL[scoring.status],
      tone: FLAG_TONE[scoring.status],
      href: ROUTES.governance("models"),
      live: true,
    });
  }
  rows.push({
    label: "Model freshness",
    detail:
      completedModels.length === 0
        ? "No completed model runs yet."
        : completedModels.map(([kind, run]) => `${MODEL_KIND_LABEL[kind] ?? words(kind)} ${hoursLabel(run?.age_hours ?? null)} ago`).join(", "),
    tag: completedModels.length === 0 ? "Not run yet" : `${completedModels.length} of 5 kinds`,
    tone: completedModels.length === 0 ? "mute" : "ok",
    href: ROUTES.governance("models"),
    live: true,
  });
  rows.push({ label: "GraphBase sync", detail: "Graph views are built from Postgres on request. No graph store is connected.", tag: "Not connected", tone: notConnected, href: ROUTES.governance("pipelines"), live: false });
  rows.push({ label: "LLM provider", detail: "Not connected to a live service in this prototype. Review continues without drafting.", tag: "Not connected", tone: notConnected, href: ROUTES.governance("pipelines"), live: false });

  return (
    <Panel>
      <h2 className="flex items-center gap-2 text-base font-semibold">
        Platform health <LiveTag />
      </h2>
      {rows.map((row) => (
        <Link key={row.label} href={row.href} className={cn("flex w-full items-center justify-between gap-3 rounded-xl border border-[#d5dce6] bg-white px-3.5 py-3 text-[13.5px] hover:border-[#9fb0c6]")}>
          <span className="flex flex-col gap-0.5">
            <b>{row.label}</b>
            <span className="text-xs text-muted">{row.detail}</span>
          </span>
          <Pill tone={row.tone}>{row.tag}</Pill>
        </Link>
      ))}
    </Panel>
  );
}

export function LiveCommandCenter() {
  const summary = useDashboardSummary();
  return (
    <LiveData query={summary} label="the command center">
      {(data) => (
        <>
          {data.alerts.truncated || data.ingestion.truncated || data.cases?.truncated || data.alerts.connected_accounts_truncated ? (
            <p role="note" className="text-xs text-muted">
              Some totals are capped by server-side limits for this view.
            </p>
          ) : null}
          <NextActions summary={data} />
          <KpiGrid summary={data} />
          <div className="grid grid-cols-[repeat(auto-fit,minmax(300px,1fr))] gap-4">
            <TypologyMix summary={data} />
            <AlertStatus summary={data} />
            <AlertAge summary={data} />
          </div>
          <div className="grid grid-cols-[repeat(auto-fit,minmax(340px,1fr))] gap-4">
            <LiveAlertsTable summary={data} />
            <PlatformHealth summary={data} />
          </div>
        </>
      )}
    </LiveData>
  );
}
