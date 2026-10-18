"use client";

import Link from "next/link";
import { useMemo } from "react";
import { cn } from "@/lib/cn";
import { formatMoney } from "@/lib/format";
import { ROUTES } from "@/lib/routes";
import { ALERT_AGE_BUCKETS, ALERTS_CLOSED_14D, ALERTS_CREATED_14D, OPEN_ALERT_TOTAL, TYPOLOGY_MIX } from "@/lib/seed/alerts";
import { useDemoState, useRole, useScoredAlerts } from "@/lib/store/hooks";
import type { Tone } from "@/lib/types";
import { buttonClasses } from "@/components/ui/button";
import { Card, CardHeader, Panel } from "@/components/ui/card";
import { Legend, Meter, Sparkline, Swatch } from "@/components/ui/misc";
import { Pill } from "@/components/ui/pill";
import { DataTable, Td, Th } from "@/components/ui/table";

interface NextAction {
  title: string;
  description: string;
  cta: string;
  href: string;
}

function NextActions() {
  const state = useDemoState();
  const role = useRole();
  const alerts = useScoredAlerts();
  const fixed = state.data.driftFixed;
  const main = alerts.find((a) => a.id === "AL-1027");

  const actions: NextAction[] = [];
  if (!fixed) actions.push({ title: "Resolve the data hold on ach_2026_10_06_b", description: "Schema drift hides 1,206 rows. AL-1027 is missing a hop, so it ranks lower than it should.", cta: "Open data hold", href: ROUTES.data });
  actions.push({
    title: "Review AL-1027: layering, rapid pass-through",
    description: fixed ? `Re-scored after the fix: ${main?.bandLabel.toLowerCase()}. Trace the path, then ask the copilot.` : "Partial data. Trace what is visible and note the gap.",
    cta: "Open alert",
    href: ROUTES.riskAlert("AL-1027"),
  });
  if (!state.caseWork.claimFixed) actions.push({ title: "Fix 1 blocked claim in the CASE-0412 narrative", description: "The verifier stopped an unsupported total before display.", cta: "Open narrative", href: ROUTES.case("CASE-0412", "narrative") });
  if (state.caseWork.status === "Pending approval" && role.canApproveEscalation) {
    actions.push({ title: "Approve or return the CASE-0412 escalation", description: "A second reviewer is required before SAR-support review.", cta: "Review request", href: ROUTES.case("CASE-0412", "disposition") });
  }

  return (
    <section aria-label="Next best actions" className="flex flex-wrap items-start gap-6 rounded-[18px] bg-hero p-7 text-white">
      <div className="flex flex-[1_1_300px] flex-col gap-2">
        <div className="eyebrow text-brand-light">Good afternoon, {role.name}</div>
        <h1 className="text-[28px] font-semibold leading-tight">{actions.length} things need you today</h1>
        <p className="text-sm text-hero-text">Ranked by impact on open cases. Every item links to its evidence.</p>
      </div>
      <ol className="flex flex-[2_1_520px] flex-col gap-2.5">
        {actions.map((action, i) => (
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
        ))}
      </ol>
    </section>
  );
}

function KpiGrid() {
  const state = useDemoState();
  const alerts = useScoredAlerts();
  const fixed = state.data.driftFixed;
  const reviewFirst = alerts.filter((a) => a.band === 1).length + 8;
  const kpis: Array<{ label: string; value: string; delta: string; tone: Tone; series: number[]; href: string }> = [
    { label: "Open alerts", value: String(OPEN_ALERT_TOTAL), delta: "+12 today", tone: "info", series: ALERTS_CREATED_14D, href: ROUTES.risk },
    { label: "Review first", value: String(reviewFirst), delta: `Priority policy ${state.policy.version}`, tone: "dark", series: [5, 6, 6, 7, 8, 7, 9], href: ROUTES.risk },
    { label: "Est. amount, review first", value: "$1.84M", delta: "Synthetic sample", tone: "info", series: [1.2, 1.4, 1.3, 1.6, 1.7, 1.9, 1.84], href: ROUTES.risk },
    { label: "Past internal SLA", value: "12", delta: "Demo setting: 5 days", tone: "warn", series: [8, 9, 9, 10, 11, 12, 12], href: ROUTES.risk },
    { label: "Connected networks", value: "14", delta: "CL-17 newest", tone: "info", series: [10, 11, 11, 12, 13, 13, 14], href: ROUTES.graph },
    { label: "Data holds", value: fixed ? "0" : "1", delta: fixed ? "Clear" : "1,206 rows held", tone: fixed ? "ok" : "warn", series: [0, 0, 1, 0, 0, 0, fixed ? 0 : 1], href: ROUTES.data },
  ];
  return (
    <section aria-label="Key numbers" className="grid grid-cols-[repeat(auto-fit,minmax(170px,1fr))] gap-4">
      {kpis.map((kpi) => (
        <Link key={kpi.label} href={kpi.href} className="flex flex-col gap-1.5 rounded-[14px] border border-line bg-white px-4 py-3.5 transition-colors hover:border-[#9fb0c6]">
          <span className="text-[12.5px] text-muted">{kpi.label}</span>
          <span className="text-[28px] font-bold leading-tight tracking-[-0.02em]">{kpi.value}</span>
          <Sparkline values={kpi.series} />
          <Pill tone={kpi.tone} className="self-start">
            {kpi.delta}
          </Pill>
        </Link>
      ))}
    </section>
  );
}

function TypologyMix() {
  const max = Math.max(...TYPOLOGY_MIX.map(([, n]) => n));
  return (
    <Panel>
      <CardHeader title="Typology mix" actions={<span className="text-xs text-muted">Open alerts, {OPEN_ALERT_TOTAL}</span>} />
      {TYPOLOGY_MIX.map(([label, count]) => (
        <div key={label} className="grid grid-cols-[150px_1fr_28px] items-center gap-2.5 text-[13px]">
          <span>{label}</span>
          <Meter value={count / max} label={`${label}: ${count} alerts`} />
          <b className="text-right">{count}</b>
        </div>
      ))}
    </Panel>
  );
}

function AlertFlow() {
  const { area, created, closed } = useMemo(() => {
    const x = (i: number) => (20 + (i * 560) / 13).toFixed(1);
    const y = (v: number) => (170 - (v / 14) * 150).toFixed(1);
    const path = (series: number[]) => series.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join(" ");
    const createdPath = path(ALERTS_CREATED_14D);
    return { created: createdPath, closed: path(ALERTS_CLOSED_14D), area: `${createdPath} L${x(13)},170 L${x(0)},170 Z` };
  }, []);
  return (
    <Panel>
      <CardHeader title="Alert flow, 14 days" actions={<span className="text-xs text-muted">Created vs closed</span>} />
      <svg role="img" aria-label="Line chart of alerts created and closed per day over 14 days. Created ranges from 6 to 13 per day, closed from 6 to 10." viewBox="0 0 600 190" className="w-full">
        <line x1="20" y1="170" x2="580" y2="170" stroke="#d5dce6" />
        <line x1="20" y1="95" x2="580" y2="95" stroke="#eef1f5" />
        <line x1="20" y1="20" x2="580" y2="20" stroke="#eef1f5" />
        <path d={area} fill="var(--color-brand)" fillOpacity={0.1} />
        <path d={created} fill="none" stroke="var(--color-brand)" strokeWidth={3} strokeLinejoin="round" />
        <path d={closed} fill="none" stroke="var(--color-select)" strokeWidth={2.5} strokeDasharray="7 5" strokeLinejoin="round" />
        <text x="16" y="24" fontSize="11" textAnchor="end" fill="#5b6678">
          14
        </text>
        <text x="16" y="174" fontSize="11" textAnchor="end" fill="#5b6678">
          0
        </text>
      </svg>
      <div className="flex justify-between text-xs text-muted">
        {["Sep 26", "Sep 30", "Oct 4", "Oct 9"].map((t) => (
          <span key={t}>{t}</span>
        ))}
      </div>
      <Legend
        items={[
          { label: "Created (solid)", swatch: <svg width="22" height="10" aria-hidden="true"><line x1="0" y1="5" x2="22" y2="5" stroke="var(--color-brand)" strokeWidth={3} /></svg> },
          { label: "Closed (dashed)", swatch: <svg width="22" height="10" aria-hidden="true"><line x1="0" y1="5" x2="22" y2="5" stroke="var(--color-select)" strokeWidth={2.5} strokeDasharray="6 4" /></svg> },
        ]}
      />
    </Panel>
  );
}

function AlertAge() {
  const max = Math.max(...ALERT_AGE_BUCKETS.map((b) => b.count));
  return (
    <Panel>
      <CardHeader title="Alert age vs SLA" actions={<span className="text-xs text-muted">Internal demo SLA: 5 days</span>} />
      <div className="flex h-[180px] items-end gap-3.5 pt-2.5">
        {ALERT_AGE_BUCKETS.map((bucket) => (
          <div key={bucket.label} className="flex h-full flex-1 flex-col items-center justify-end gap-1.5">
            <b className="text-[13px]">{bucket.count}</b>
            <div className="w-full max-w-14 rounded-t-md" style={{ height: `${Math.round((bucket.count / max) * 140)}px`, background: bucket.pastSla ? "var(--color-attention-stroke)" : "var(--color-brand)" }} />
            <span className="text-center text-xs text-muted">{bucket.label}</span>
          </div>
        ))}
      </div>
      <Legend
        items={[
          { label: "Within SLA", swatch: <Swatch color="var(--color-brand)" /> },
          { label: "Past SLA", swatch: <Swatch color="var(--color-attention-stroke)" /> },
        ]}
      />
    </Panel>
  );
}

function LiveAlerts() {
  const alerts = useScoredAlerts().slice(0, 6);
  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-2.5 px-5 py-4">
        <h2 className="text-base font-semibold">Live alerts</h2>
        <Link href={ROUTES.risk} className={buttonClasses("ghost", "sm")}>
          Open risk intelligence
        </Link>
      </div>
      <DataTable caption="Live alerts ranked by priority">
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
          {alerts.map((a) => (
            <tr key={a.id}>
              <Td>
                <Link href={ROUTES.riskAlert(a.id)} className={buttonClasses("link", "md", "font-mono")}>
                  {a.id}
                </Link>
              </Td>
              <Td>
                <Pill tone={a.bandTone}>{a.bandLabel}</Pill>
              </Td>
              <Td>{a.typology}</Td>
              <Td>{formatMoney(a.amount)}</Td>
              <Td>{a.accounts}</Td>
              <Td>
                <Pill tone={a.sla.startsWith("Past") ? "warn" : "mute"}>{a.age}</Pill>
              </Td>
            </tr>
          ))}
        </tbody>
      </DataTable>
    </Card>
  );
}

function PlatformHealth() {
  const state = useDemoState();
  const fixed = state.data.driftFixed;
  const rows: Array<{ label: string; detail: string; tag: string; tone: Tone; href: string }> = [
    { label: "Ingestion", detail: fixed ? "5 of 6 feeds current. Cash feed late (SLA 2 h)." : "1 batch quarantined, cash feed late.", tag: "Attention", tone: "warn", href: ROUTES.data },
    { label: "GraphBase sync", detail: state.sim.graph ? "Unreachable. Serving saved snapshots." : "Watermark 13:59:18 UTC, lag 42 s.", tag: state.sim.graph ? "Down" : "Healthy", tone: state.sim.graph ? "bad" : "ok", href: ROUTES.governance("pipelines") },
    { label: "Scoring", detail: "SR-2291 finished 18 min ago, p95 1.8 s.", tag: "Healthy", tone: "ok", href: ROUTES.governance("models") },
    { label: "Model freshness", detail: "xgb-aml v1.4.2, features v7. No drift alarm.", tag: "Current", tone: "ok", href: ROUTES.governance("drift") },
    { label: "LLM provider", detail: state.sim.llm ? "Unavailable. Drafting paused, review continues." : "Groq hosted, Ollama fallback ready.", tag: state.sim.llm ? "Down" : "Healthy", tone: state.sim.llm ? "bad" : "ok", href: ROUTES.governance("pipelines") },
    { label: "Policy index", detail: "FFIEC manual and FinCEN guidance, indexed [date].", tag: "Current", tone: "ok", href: ROUTES.governance("settings") },
  ];
  return (
    <Panel>
      <h2 className="text-base font-semibold">Platform health</h2>
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

export function CommandCenter() {
  return (
    <>
      <NextActions />
      <KpiGrid />
      <div className="grid grid-cols-[repeat(auto-fit,minmax(300px,1fr))] gap-4">
        <TypologyMix />
        <AlertFlow />
        <AlertAge />
      </div>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(340px,1fr))] gap-4">
        <LiveAlerts />
        <PlatformHealth />
      </div>
    </>
  );
}
