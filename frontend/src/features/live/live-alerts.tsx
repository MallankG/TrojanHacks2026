"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { cn } from "@/lib/cn";
import { errorMessage, invalidateLive, liveApi, useLive, type Alert } from "@/lib/live/api";
import { BAND_LABEL, BAND_TONE, alertLabel, caseLabel, money, utc, words } from "@/lib/live/format";
import { LIVE_ROLES } from "@/lib/live/roles";
import { useLiveProfile } from "@/lib/live/session";
import { ROUTES } from "@/lib/routes";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { CardHeader, Panel } from "@/components/ui/card";
import { KeyValueList } from "@/components/ui/key-value";
import { CodeBlock, EmptyState, Skeleton } from "@/components/ui/misc";
import { Pill } from "@/components/ui/pill";
import { DataTable, Td, Th, Tr } from "@/components/ui/table";
import { LiveData } from "./live-state";
import { LiveGraph } from "./live-graph";

/** Live alerts from the latest completed ranker run, at the top of the alert queue. */
export function LiveAlertEntries({ selectedId }: { selectedId: string }) {
  const profile = useLiveProfile();
  const alerts = useLive(async () => (profile ? await liveApi.alerts({ limit: 50 }) : null), `alerts:${profile?.id ?? "demo"}`);
  if (!profile) return null;

  return (
    <div className="flex flex-col gap-2 border-b border-line-soft pb-3">
      <span className="eyebrow">Live alerts</span>
      {alerts.error ? (
        <Callout tone="bad" role="alert">
          {alerts.error}
        </Callout>
      ) : alerts.loading || !alerts.data ? (
        <Skeleton lines={2} label="Loading live alerts" />
      ) : alerts.data.items.length === 0 ? (
        <p className="text-xs text-muted">
          {alerts.data.model_run_id
            ? "The latest ranker run published no alerts."
            : "No ranker run has been published yet. Alerts appear after the model worker scores your uploads."}
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {alerts.data.items.map((a) => {
            const active = a.id === selectedId;
            return (
              <li key={a.id}>
                <Link
                  href={ROUTES.riskAlert(a.id)}
                  aria-current={active ? "page" : undefined}
                  scroll={false}
                  className={cn("flex w-full flex-col gap-1.5 rounded-xl border bg-white text-left", active ? "border-2 border-brand bg-[#f7fcfb] px-[13px] py-[11px]" : "border-line px-3.5 py-3 hover:border-[#9fb0c6]")}
                >
                  <span className="flex w-full items-center justify-between gap-2">
                    <b className="font-mono">{alertLabel(a.alert_number)}</b>
                    <Pill tone={BAND_TONE[a.priority_band]}>{BAND_LABEL[a.priority_band]}</Pill>
                  </span>
                  <span className="text-[13.5px] font-semibold">{words(a.typology)}</span>
                  <span className="text-xs text-muted">
                    {money(a.total_amount, a.amount_currency ?? "USD")} · {a.connected_account_count ?? "?"} accounts · rank {a.priority_rank ?? "none"}
                  </span>
                  <span className="flex flex-wrap gap-1.5">
                    <Pill tone={a.has_dq_caveat ? "warn" : "ok"}>{a.has_dq_caveat ? "Data-quality caveat" : "No known data issues"}</Pill>
                    <Pill tone="mute">{words(a.status)}</Pill>
                    <Pill tone="ok">Live</Pill>
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
      <span className="eyebrow mt-1">Sample alerts</span>
    </div>
  );
}

/** A live backend alert: why it ranked, component signals, money-flow graph, timeline and lineage. */
export function LiveAlertDetail({ alertId }: { alertId: string }) {
  const router = useRouter();
  const profile = useLiveProfile();
  const role = LIVE_ROLES[profile?.role ?? "analyst"];
  const alert = useLive(() => liveApi.alert(alertId), `alert:${alertId}`);
  const graph = useLive(() => liveApi.alertGraph(alertId), `graph:${alertId}`);
  const [showLineage, setShowLineage] = useState(false);
  const lineage = useLive(() => (showLineage ? liveApi.alertLineage(alertId) : Promise.resolve(null)), `lineage:${alertId}:${showLineage}`);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!profile) {
    return (
      <Callout tone="warn" title="This is a live backend alert." className="min-w-0 flex-[2_1_560px]">
        Sign in with your backend account (email and password) to open it.
      </Callout>
    );
  }

  const openCase = async (a: Alert) => {
    setBusy(true);
    setError(null);
    try {
      const created = await liveApi.createCase({ title: `${words(a.typology)} lead from ${alertLabel(a.alert_number)}`, priority_band: a.priority_band, alert_ids: [a.id] });
      invalidateLive("cases");
      invalidateLive("alerts");
      router.push(ROUTES.case(created.id));
    } catch (problem) {
      setError(errorMessage(problem));
      setBusy(false);
    }
  };

  const dismiss = async () => {
    setBusy(true);
    setError(null);
    try {
      await liveApi.updateAlert(alertId, "dismissed");
      alert.reload();
      invalidateLive("alerts");
    } catch (problem) {
      setError(errorMessage(problem));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-w-0 flex-[2_1_560px] flex-col gap-4">
      <LiveData query={alert} label="alert">
        {(a) => {
          const txns = [...a.transactions].sort((x, y) => x.transactions.occurred_at.localeCompare(y.transactions.occurred_at));
          const existingCase = a.cases[0]?.cases;
          return (
            <>
              <Panel>
                <CardHeader
                  eyebrow="Live alert"
                  title={`${alertLabel(a.alert_number)} · ${words(a.typology)}`}
                  description={`${words(a.scope)} alert · window ${utc(a.window_start)} to ${utc(a.window_end)}`}
                  actions={
                    <>
                      <Pill tone={BAND_TONE[a.priority_band]}>{BAND_LABEL[a.priority_band]}</Pill>
                      {existingCase ? (
                        <Link className="font-semibold text-brand-link underline underline-offset-2" href={ROUTES.case(existingCase.id)}>
                          Open {caseLabel(existingCase.case_number)}
                        </Link>
                      ) : role.canInvestigate ? (
                        <>
                          <Button variant="ghost" size="sm" disabled={busy || a.status === "dismissed"} onClick={dismiss}>
                            Dismiss
                          </Button>
                          <Button size="sm" disabled={busy} onClick={() => openCase(a)}>
                            Open a case
                          </Button>
                        </>
                      ) : null}
                    </>
                  }
                />
                {error ? (
                  <Callout tone="bad" role="alert">
                    {error}
                  </Callout>
                ) : null}
                {a.has_dq_caveat ? (
                  <Callout tone="warn" title="Data-quality caveat.">
                    {a.dq_caveat_notes ?? "Some supporting source rows had data-quality findings."}
                  </Callout>
                ) : null}
              </Panel>
              <div className="grid gap-4 xl:grid-cols-2">
                <Panel>
                  <CardHeader title="Why it was ranked" description={`Ranking: ${a.ranking_strategy ?? "not recorded"} · model ${a.model_runs?.model_name ?? "unknown"} ${a.model_runs?.model_version ?? ""}`} />
                  <KeyValueList
                    items={[
                      ["Rank score", a.rank_score ?? "Not recorded"],
                      ["Total amount", money(a.total_amount, a.amount_currency ?? "USD")],
                      ["Transactions", a.transaction_count ?? txns.length],
                      ["Connected accounts", a.connected_account_count ?? a.accounts.length],
                      ["Status", words(a.status)],
                    ]}
                  />
                  {a.explanation ? <p className="text-[13.5px]">{a.explanation}</p> : null}
                  {a.limitations ? <p className="text-xs text-muted">Limitations: {a.limitations}</p> : null}
                </Panel>
                <Panel>
                  <CardHeader title="Component signals" description="Each detector's output, shown separately." />
                  {a.signals.length === 0 ? (
                    <EmptyState title="No signals linked." />
                  ) : (
                    <DataTable caption="Signals">
                      <thead>
                        <tr>
                          <Th>Signal</Th>
                          <Th>Source</Th>
                          <Th>Score</Th>
                          <Th>Threshold</Th>
                          <Th>Fired</Th>
                        </tr>
                      </thead>
                      <tbody>
                        {a.signals.map(({ risk_signals: s }) => (
                          <Tr key={s.id}>
                            <Td>{s.signal_name}</Td>
                            <Td>{words(s.source)}</Td>
                            <Td>{s.score ?? "None"}</Td>
                            <Td>{s.threshold ?? "None"}</Td>
                            <Td>{s.triggered ? "Yes" : "No"}</Td>
                          </Tr>
                        ))}
                      </tbody>
                    </DataTable>
                  )}
                </Panel>
              </div>
              <Panel>
                <CardHeader title="Money flow" description="Select an edge for its details. Path tracing is investigative context; commingled balances cannot always be traced exactly." />
                <LiveData query={graph} label="graph">
                  {(g) => <LiveGraph graph={g} />}
                </LiveData>
              </Panel>
              <Panel>
                <CardHeader title="Timeline" description="Supporting transactions in time order." />
                <DataTable caption="Supporting transactions">
                  <thead>
                    <tr>
                      <Th>Time</Th>
                      <Th>Transaction</Th>
                      <Th>Role</Th>
                      <Th>Amount</Th>
                      <Th>Rail</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {txns.map(({ role: r, transactions: t }) => (
                      <Tr key={t.id}>
                        <Td className="whitespace-nowrap text-xs">{utc(t.occurred_at)}</Td>
                        <Td className="font-mono text-xs">{t.transaction_ref}</Td>
                        <Td>{words(r)}</Td>
                        <Td>{money(t.amount, t.currency)}</Td>
                        <Td>{words(t.channel)}</Td>
                      </Tr>
                    ))}
                  </tbody>
                </DataTable>
              </Panel>
              <Panel>
                <CardHeader
                  title="Lineage"
                  description="Source file → source rows → transactions → signals → this alert."
                  actions={
                    <Button variant="ghost" size="sm" onClick={() => setShowLineage(!showLineage)}>
                      {showLineage ? "Hide lineage" : "Show lineage"}
                    </Button>
                  }
                />
                {showLineage ? (
                  <LiveData query={lineage} label="lineage">
                    {(l) => <CodeBlock>{JSON.stringify(l, null, 2)}</CodeBlock>}
                  </LiveData>
                ) : null}
              </Panel>
            </>
          );
        }}
      </LiveData>
    </div>
  );
}
