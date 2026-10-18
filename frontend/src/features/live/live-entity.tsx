"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { cn } from "@/lib/cn";
import { entityApi, type AccountProfile, type LiveAccount, type RobustZ } from "@/lib/live/api-entity";
import { useLive } from "@/lib/live/api";
import { BAND_LABEL, BAND_TONE, CASE_TONE, SEVERITY_TONE, alertLabel, caseLabel, money, utc, words } from "@/lib/live/format";
import { useLiveProfile } from "@/lib/live/session";
import { ROUTES } from "@/lib/routes";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Card, CardHeader, Panel } from "@/components/ui/card";
import { Segmented } from "@/components/ui/controls";
import { Field, TextInput } from "@/components/ui/field";
import { KeyValueList, StatBox } from "@/components/ui/key-value";
import { EmptyState, Legend, Swatch } from "@/components/ui/misc";
import { PageHeader } from "@/components/ui/page-header";
import { Pill } from "@/components/ui/pill";
import { DataTable, Td, Th, Tr } from "@/components/ui/table";
import { LiveData } from "./live-state";

/**
 * Live account and entity profile (screen 5): behaviour from the account's own transactions up to
 * an as-of cut-off, against its prior baseline. Mismatches are context for review, not proof.
 */

const CHIP = "inline-flex min-h-9 items-center rounded-full border px-3 font-mono text-[13px] font-semibold";
const CHIP_IDLE = "border-line-strong bg-white text-ink-2 hover:border-[#8a97ab]";

/** A live account reference that opens its profile. */
export function LiveAccountLink({ id, label, className }: { id: string; label: string; className?: string }) {
  return (
    <Link href={ROUTES.entity(id)} className={cn("font-mono font-semibold text-brand-link underline underline-offset-2", className)}>
      {label}
    </Link>
  );
}

/** Search live accounts by reference; results link to their profiles. Renders nothing without a live session. */
export function LiveAccountSearch({ selectedId, autoFocus }: { selectedId?: string; autoFocus?: boolean }) {
  const profile = useLiveProfile();
  const [text, setText] = useState("");
  const [term, setTerm] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setTerm(text.trim()), 250);
    return () => clearTimeout(timer);
  }, [text]);
  const accounts = useLive(async () => (profile ? await entityApi.searchAccounts(term, 25) : null), `accounts:${term}`);
  if (!profile) return null;

  return (
    <Panel>
      <CardHeader eyebrow="Live backend" title="Accounts" description="Search by account reference. Profiles are built from ingested transactions only." />
      <Field label="Account reference">
        {(props) => <TextInput {...props} type="search" placeholder="For example ACC-7011 or 7011" value={text} autoFocus={autoFocus} onChange={(e) => setText(e.target.value)} maxLength={64} />}
      </Field>
      <LiveData query={accounts} label="accounts">
        {(page) =>
          !page || page.items.length === 0 ? (
            <EmptyState title={term ? `No live account matches "${term}".` : "No live accounts yet."} description={term ? undefined : "Upload a CSV batch in Data operations to create accounts."} />
          ) : (
            <>
              <ul className="flex flex-wrap gap-1.5" aria-label="Live accounts">
                {page.items.map((a: LiveAccount) => (
                  <li key={a.id}>
                    <Link href={ROUTES.entity(a.id)} aria-current={a.id === selectedId ? "page" : undefined} className={cn(CHIP, a.id === selectedId ? "border-ink bg-ink text-white" : CHIP_IDLE)}>
                      {a.account_ref}
                      {a.is_internal ? "" : " · external"}
                    </Link>
                  </li>
                ))}
              </ul>
              {page.total !== null && page.total > page.items.length ? (
                <p className="text-xs text-muted">
                  Showing {page.items.length} of {page.total}. Type more of the reference to narrow the list.
                </p>
              ) : null}
            </>
          )
        }
      </LiveData>
    </Panel>
  );
}

// ---------------------------------------------------------------- display helpers

const DEC = (v: string | null | undefined, digits = 2) => (v === null || v === undefined ? null : Number(v).toFixed(digits));

function zText(z: RobustZ): string {
  if (z.z === null) return `Not defined: ${z.reason ?? "no baseline"}`;
  const n = Number(z.z);
  return `${n > 0 ? "+" : ""}${n.toFixed(2)}${z.method === "mean_absolute_deviation_fallback" ? " (mean-deviation fallback; the baseline's MAD is 0)" : ""}`;
}

function hours(value: string | null): string {
  if (value === null) return "Not enough transactions";
  const total = Math.round(Number(value) * 60);
  if (total === 0) return "Under 1 minute";
  const d = Math.floor(total / 1440);
  const h = Math.floor((total % 1440) / 60);
  const m = total % 60;
  return [d ? `${d} d` : "", h ? `${h} h` : "", m ? `${m} m` : ""].filter(Boolean).join(" ");
}

function pct(value: string | null): string | null {
  return value === null ? null : `${(Number(value) * 100).toFixed(1)}%`;
}

function concentration(hhi: string | null, top: string | null, n: number): string {
  if (hhi === null) return "No counterparties in the window";
  const v = Number(hhi);
  const level = v >= 0.5 ? "High" : v >= 0.25 ? "Medium" : "Low";
  return `${level}: HHI ${v.toFixed(2)} across ${n} counterpart${n === 1 ? "y" : "ies"}; top one ${pct(top)} of value`;
}

/** `YYYY-MM-DDTHH:mm` in UTC for a datetime-local input, and back. */
const toInput = (iso: string) => iso.slice(0, 16);
const fromInput = (value: string) => (value ? `${value}:00Z` : null);

// ---------------------------------------------------------------- chart

const CHART_HEIGHT = 130;

function FlowSeries({ p }: { p: AccountProfile }) {
  const days = p.daily_series.map((d) => ({ ...d, inflow: Number(d.inflow), outflow: Number(d.outflow) }));
  const max = Math.max(1, ...days.map((d) => Math.max(d.inflow, d.outflow)));
  const height = (v: number) => (v > 0 ? Math.max(2, Math.round(Math.sqrt(v / max) * CHART_HEIGHT)) : 0);
  const base = p.baseline_vs_observed.baseline;
  const baseDays = p.windows.baseline.days;
  const baselineLine = height(Math.max(Number(base.inflow_amount), Number(base.outflow_amount)) / baseDays);
  const observedFrom = p.windows.observed.start.slice(0, 10);
  const every = Math.max(1, Math.ceil(days.length / 6));
  const label = (iso: string) => new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  const busiest = days.reduce((a, b) => (b.inflow + b.outflow > a.inflow + a.outflow ? b : a), days[0]);

  return (
    <Panel>
      <div className="flex flex-wrap justify-between gap-1.5">
        <h2 className="text-base font-semibold">Daily flow, {days.length} days to as-of</h2>
        <span className="text-xs text-muted">UTC days, USD. Square-root scale. Dashed line = baseline daily average. Shaded = observed window.</span>
      </div>
      <div
        className="relative flex h-[140px] items-end gap-[3px] border-b border-[#d5dce6]"
        role="img"
        aria-label={`Daily inflow and outflow for ${p.account.account_ref} over ${days.length} days${busiest && busiest.inflow + busiest.outflow > 0 ? `, busiest on ${label(busiest.date)}` : ", no activity"}.`}
      >
        {baselineLine > 0 ? <div aria-hidden="true" className="absolute inset-x-0 border-t-2 border-dashed border-[#8a97ab]" style={{ bottom: baselineLine }} /> : null}
        {days.map((day) => (
          <div key={day.date} title={`${label(day.date)}: in ${money(day.inflow)}, out ${money(day.outflow)}, ${day.count} transaction${day.count === 1 ? "" : "s"}`} className={cn("flex h-full flex-1 items-end gap-px", day.date >= observedFrom && "bg-[#f1f6fb]")}>
            <div className="flex-1 rounded-t-sm bg-brand" style={{ height: height(day.inflow) }} />
            <div className="flex-1 rounded-t-sm bg-ink" style={{ height: height(day.outflow) }} />
          </div>
        ))}
      </div>
      <div className="flex gap-[3px]">
        {days.map((day, i) => (
          <span key={day.date} className="flex-1 overflow-visible whitespace-nowrap text-[10px] text-muted">
            {i % every === 0 ? label(day.date) : ""}
          </span>
        ))}
      </div>
      <Legend
        items={[
          { label: "Inflow (left bar)", swatch: <Swatch color="var(--color-brand)" /> },
          { label: "Outflow (right bar)", swatch: <Swatch color="var(--color-ink)" /> },
        ]}
      />
    </Panel>
  );
}

// ---------------------------------------------------------------- profile

const OBSERVED = [1, 2, 7, 14, 30].map((d) => ({ value: d, label: `${d} d` }));
const BASELINE = [7, 14, 30, 90].map((d) => ({ value: d, label: `${d} d` }));

export function LiveEntityProfile({ accountId }: { accountId: string }) {
  const profile = useLiveProfile();
  const [asOf, setAsOf] = useState<string | null>(null);
  const [observedDays, setObservedDays] = useState(7);
  const [baselineDays, setBaselineDays] = useState(30);
  const query = useLive(
    () => entityApi.profile(accountId, { as_of: asOf ?? undefined, observed_days: observedDays, baseline_days: baselineDays }),
    `entity:${accountId}:${asOf ?? "now"}:${observedDays}:${baselineDays}`,
  );

  if (!profile) {
    return (
      <Callout tone="warn" title="This is a live backend account.">
        Sign in with your backend account (email and password) to open its profile.
      </Callout>
    );
  }

  return (
    <>
      <PageHeader title="Account and entity profile" description="Baseline against what happened, up to an as-of cut-off. Mismatches are context for review, not proof." />
      <LiveAccountSearch selectedId={accountId} />
      <Panel>
        <div className="flex flex-wrap items-end gap-4">
          <Field label="As of (UTC)" hint={asOf ? "Transactions after this moment are ignored." : "Now. Transactions after this moment are ignored."}>
            {(props) => (
              <TextInput
                {...props}
                type="datetime-local"
                className="w-auto"
                value={asOf ? toInput(asOf) : ""}
                onChange={(e) => setAsOf(fromInput(e.target.value))}
              />
            )}
          </Field>
          <div className="flex flex-col gap-1.5">
            <span className="text-[13.5px] font-semibold">Observed window</span>
            <Segmented label="Observed window" value={observedDays} options={OBSERVED} onChange={setObservedDays} />
          </div>
          <div className="flex flex-col gap-1.5">
            <span className="text-[13.5px] font-semibold">Baseline before it</span>
            <Segmented label="Baseline window" value={baselineDays} options={BASELINE} onChange={setBaselineDays} />
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="ghost" size="sm" disabled={!query.data?.activity.last_at} onClick={() => query.data?.activity.last_at && setAsOf(query.data.activity.last_at)}>
              Last activity
            </Button>
            <Button variant="ghost" size="sm" disabled={asOf === null} onClick={() => setAsOf(null)}>
              Now
            </Button>
          </div>
        </div>
      </Panel>
      <LiveData query={query} label="account profile">
        {(p) => <ProfileBody p={p} />}
      </LiveData>
    </>
  );
}

function ProfileBody({ p }: { p: AccountProfile }) {
  const a = p.account;
  const bvo = p.baseline_vs_observed;
  const r = p.ratios;
  const w = p.windows;
  const dq = p.data_quality;
  const range = (start: string, end: string) => `${utc(start)} to ${utc(end)}`;
  const risk = a.risk_context && Object.keys(a.risk_context).length > 0 ? a.risk_context : null;
  const openIssues = dq.transaction_issues.filter((i) => i.status === "open" || i.status === "acknowledged");

  return (
    <>
      <Panel className="flex-row flex-wrap items-start justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <div className="eyebrow">{a.is_internal ? "Internal account" : "External account"} · Live</div>
          <h2 className="font-mono text-[22px] font-semibold">{a.account_ref}</h2>
          <span className="text-[13.5px] text-muted">
            {a.account_type ? words(a.account_type) : "Account type not recorded"} · Institution {a.institution_ref ?? "not recorded"}
          </span>
          <div className="mt-1 flex flex-wrap gap-1.5">
            <Pill tone={a.status === "active" ? "ok" : "warn"}>Status: {words(a.status)}</Pill>
            <Pill tone="mute">{a.open_date ? `Opened ${a.open_date}` : "Open date not recorded"}</Pill>
            <Pill tone="mute">{a.customer_ref ? `Customer ${a.customer_ref}` : "No customer reference"}</Pill>
            <Pill tone={a.customer_risk_rating === "high" ? "warn" : "mute"}>{a.customer_risk_rating ? `Risk rating ${a.customer_risk_rating}` : "No risk rating"}</Pill>
            {a.is_synthetic ? <Pill tone="mute">Synthetic</Pill> : null}
            <Pill tone="ok">Live</Pill>
          </div>
        </div>
        <div className="flex flex-col items-end gap-1 text-xs text-muted">
          <span>As of {utc(w.as_of)}</span>
          <span>First activity {utc(p.activity.first_at)}</span>
          <span>Last activity before as-of {utc(p.activity.last_at)}</span>
        </div>
      </Panel>

      {p.limits.history_notes.map((note) => (
        <Callout key={note} tone="info" title="Baseline coverage.">
          {note}
        </Callout>
      ))}
      {dq.rejected_rows.length > 0 || openIssues.length > 0 ? (
        <Callout tone="warn" title="Data-quality warning." action={<Link href={ROUTES.dataQuality} className="font-semibold text-brand-link underline underline-offset-2">Open quality exceptions</Link>}>
          {dq.rejected_rows.length > 0 ? `${dq.rejected_rows.length} rejected source row${dq.rejected_rows.length === 1 ? " names" : "s name"} this account and never became transactions, so this profile is incomplete. ` : ""}
          {openIssues.length > 0 ? `${openIssues.length} of its transactions carry open data-quality findings.` : ""}
        </Callout>
      ) : null}

      <div className="grid grid-cols-[repeat(auto-fit,minmax(340px,1fr))] gap-4">
        <FlowSeries p={p} />
        <Card className="overflow-hidden">
          <div className="px-5 py-4">
            <h2 className="text-base font-semibold">Baseline vs observed</h2>
            <span className="text-xs text-muted">Robust z-score of daily USD volume: {zText(bvo.volume_z)}</span>
          </div>
          <DataTable caption="Baseline versus observed activity">
            <thead>
              <tr>
                <Th>Measure</Th>
                <Th>Baseline ({w.baseline.days} d)</Th>
                <Th>Observed ({w.observed.days} d)</Th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <Td>Transactions</Td>
                <Td>{bvo.baseline.transactions}</Td>
                <Td className="font-bold">{bvo.observed.transactions}</Td>
              </tr>
              <tr>
                <Td>Inflow</Td>
                <Td>{money(bvo.baseline.inflow_amount)} ({bvo.baseline.inflow_count})</Td>
                <Td className="font-bold">{money(bvo.observed.inflow_amount)} ({bvo.observed.inflow_count})</Td>
              </tr>
              <tr>
                <Td>Outflow</Td>
                <Td>{money(bvo.baseline.outflow_amount)} ({bvo.baseline.outflow_count})</Td>
                <Td className="font-bold">{money(bvo.observed.outflow_amount)} ({bvo.observed.outflow_count})</Td>
              </tr>
              <tr>
                <Td>Daily volume</Td>
                <Td>{money(bvo.baseline.daily_volume_median)} median</Td>
                <Td className="font-bold">{money(bvo.observed.daily_volume_mean)} mean</Td>
              </tr>
              <tr>
                <Td>Median ticket</Td>
                <Td>{bvo.baseline.ticket.median ? money(bvo.baseline.ticket.median) : "None"}</Td>
                <Td className="font-bold">{bvo.observed.ticket.median ? money(bvo.observed.ticket.median) : "None"}</Td>
              </tr>
              <tr>
                <Td>Ticket p25 to p90</Td>
                <Td>{bvo.baseline.ticket.p25 ? `${money(bvo.baseline.ticket.p25)} to ${money(bvo.baseline.ticket.p90)}` : "None"}</Td>
                <Td className="font-bold">{bvo.observed.ticket.p25 ? `${money(bvo.observed.ticket.p25)} to ${money(bvo.observed.ticket.p90)}` : "None"}</Td>
              </tr>
              <tr>
                <Td>Counterparties</Td>
                <Td>{bvo.baseline.unique_counterparties}</Td>
                <Td className="font-bold">
                  {bvo.observed.unique_counterparties} ({p.new_counterparties.length} new)
                </Td>
              </tr>
            </tbody>
          </DataTable>
          <div className="flex flex-col gap-3 px-5 py-3.5">
            <KeyValueList
              items={[
                ["Count z-score", zText(bvo.count_z)],
                ["Flow ratio (30 d)", r.inflow_outflow_ratio === null ? "No outflow in 30 days" : `${DEC(r.inflow_outflow_ratio)} in / out; pass-through ${pct(r.pass_through_ratio) ?? "n/a (no inflow)"}`],
                ["Net flow (30 d)", money(r.net_flow)],
                ["Retained balance proxy", r.retained_balance_proxy === null ? "No inflow in 30 days" : `${pct(r.retained_balance_proxy)} of inflow kept`],
                ["Time between transfers", `${hours(r.inter_arrival_median_hours)} median (${r.inter_arrival_gaps} gaps, 30 d)`],
                ["Counterparty concentration", concentration(r.counterparty_hhi, r.top_counterparty_share, p.flows["30d"].unique_counterparties)],
              ]}
            />
            <p className="text-xs text-muted">
              Observed {range(w.observed.start, w.observed.end)}; baseline {range(w.baseline.start, w.baseline.end)}. {w.boundaries} {r.retained_balance_note}
            </p>
          </div>
        </Card>
      </div>

      <Panel>
        <CardHeader title="Flow windows" description={`Ending at ${utc(w.as_of)}. USD.`} />
        <div className="grid grid-cols-[repeat(auto-fit,minmax(170px,1fr))] gap-2.5">
          {(["1h", "24h", "7d", "30d"] as const).map((key) => {
            const f = p.flows[key];
            return (
              <StatBox
                key={key}
                label={`Last ${key}`}
                value={
                  <span className="flex flex-col text-[13.5px] font-semibold">
                    <span>
                      In {money(f.inflow_amount)} ({f.inflow_count})
                    </span>
                    <span>
                      Out {money(f.outflow_amount)} ({f.outflow_count})
                    </span>
                    <span className="text-xs font-medium text-muted">
                      Net {money(f.net_flow)} · {f.unique_counterparties} counterpart{f.unique_counterparties === 1 ? "y" : "ies"}
                    </span>
                  </span>
                }
              />
            );
          })}
        </div>
        {p.counts.excluded_no_usd_amount + p.counts.excluded_self_transfers > 0 ? (
          <p className="text-xs text-muted">
            Left out: {p.counts.excluded_no_usd_amount} without a USD amount, {p.counts.excluded_self_transfers} self-transfers.
          </p>
        ) : null}
      </Panel>

      <div className="grid grid-cols-[repeat(auto-fit,minmax(340px,1fr))] gap-4">
        <Card className="overflow-hidden">
          <div className="px-5 py-4">
            <h2 className="text-base font-semibold">Counterparties</h2>
            <span className="text-xs text-muted">
              New means first seen in the observed window and absent from the baseline. History before the {Math.max(30, w.observed.days + w.baseline.days)}-day lookback is not read.
            </span>
          </div>
          {p.counterparties.length === 0 ? (
            <div className="px-5 pb-4">
              <EmptyState title="No counterparties in the lookback window." />
            </div>
          ) : (
            <DataTable caption="Counterparties">
              <thead>
                <tr>
                  <Th>Account</Th>
                  <Th>Direction</Th>
                  <Th>First seen</Th>
                  <Th>Status</Th>
                  <Th>Amount</Th>
                </tr>
              </thead>
              <tbody>
                {p.counterparties.map((c) => (
                  <Tr key={c.account_id}>
                    <Td>
                      <LiveAccountLink id={c.account_id} label={c.account?.account_ref ?? c.account_id.slice(0, 8)} />
                    </Td>
                    <Td>{c.inflow_count && c.outflow_count ? "Both" : c.inflow_count ? "Sender" : "Receiver"}</Td>
                    <Td className="whitespace-nowrap text-xs">{utc(c.first_seen_at)}</Td>
                    <Td>
                      <Pill tone={c.status === "new" ? "warn" : "mute"}>{c.status === "new" ? "New" : c.status === "known" ? "Known" : "Before baseline"}</Pill>
                    </Td>
                    <Td>{money(c.total_amount)}</Td>
                  </Tr>
                ))}
              </tbody>
            </DataTable>
          )}
          {p.counterparties_total > p.counterparties.length ? <p className="px-5 py-3 text-xs text-muted">Showing the top {p.counterparties.length} of {p.counterparties_total} by value.</p> : null}
        </Card>
        <Panel>
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-base font-semibold">Profile and geography</h2>
            <Pill tone="mute">Only what the data holds</Pill>
          </div>
          <KeyValueList
            items={[
              ["Expected activity", p.not_available.expected_activity ? <span className="text-muted">Not available. {p.not_available.expected_activity}</span> : "Recorded"],
              ["Observed activity", `${bvo.observed.transactions} transactions in ${w.observed.days} d: ${money(bvo.observed.inflow_amount)} in, ${money(bvo.observed.outflow_amount)} out`],
              ["Geography", a.jurisdiction ? `Jurisdiction ${a.jurisdiction}` : <span className="text-muted">Not available. {p.not_available.geography}</span>],
              ["Risk context", risk ? <code className="font-mono text-xs">{JSON.stringify(risk)}</code> : <span className="text-muted">Not available. No synthetic KYC or screening context recorded.</span>],
            ]}
          />
          <p className="text-xs text-muted">No expected-versus-observed mismatch is claimed without an expected-activity profile.</p>
        </Panel>
      </div>

      <div className="grid grid-cols-[repeat(auto-fit,minmax(340px,1fr))] gap-4">
        <Panel>
          <h2 className="text-base font-semibold">Shared token links and entity resolution</h2>
          <span className="text-[13.5px] text-muted">Not available. {p.not_available.shared_tokens}</span>
        </Panel>
        <Panel>
          <h2 className="text-base font-semibold">Related alerts and cases</h2>
          {p.related.alerts.length === 0 && p.related.cases.length === 0 ? (
            <span className="text-[13.5px] text-muted">No alerts or cases reference this account yet.</span>
          ) : (
            <div className="flex flex-wrap gap-2">
              {p.related.alerts.map((al) => (
                <Link key={al.id} href={ROUTES.riskAlert(al.id)} className={cn(CHIP, CHIP_IDLE, "gap-1.5")} title={`${words(al.typology)} · ${words(al.role)} · ${words(al.status)}`}>
                  {alertLabel(al.alert_number)}
                  <Pill tone={BAND_TONE[al.priority_band]}>{BAND_LABEL[al.priority_band]}</Pill>
                </Link>
              ))}
              {p.related.cases.map((c) => (
                <Link key={c.id} href={ROUTES.case(c.id)} className={cn(CHIP, CHIP_IDLE, "gap-1.5")} title={c.title}>
                  {caseLabel(c.case_number)}
                  <Pill tone={CASE_TONE[c.status]}>{words(c.status)}</Pill>
                </Link>
              ))}
            </div>
          )}
        </Panel>
      </div>

      <Panel>
        <CardHeader title="Data quality on this account's source rows" description={dq.note} />
        {dq.transaction_issues.length === 0 && dq.rejected_rows.length === 0 ? (
          <EmptyState title="No data-quality findings on rows that touch this account." />
        ) : (
          <DataTable caption="Data-quality findings">
            <thead>
              <tr>
                <Th>Row</Th>
                <Th>Effect</Th>
                <Th>Rule</Th>
                <Th>Severity</Th>
                <Th>Finding</Th>
              </tr>
            </thead>
            <tbody>
              {dq.rejected_rows.flatMap((row) =>
                (row.issues.length ? row.issues : [null]).map((issue, i) => (
                  <Tr key={`${row.source_row_id}-${issue?.id ?? i}`}>
                    <Td>Row {row.row_number}</Td>
                    <Td>
                      <Pill tone="bad">Rejected, missing from metrics</Pill>
                    </Td>
                    <Td className="font-mono text-xs">{issue?.rule_id ?? "None"}</Td>
                    <Td>{issue ? <Pill tone={SEVERITY_TONE[issue.severity]}>{words(issue.severity)}</Pill> : "None"}</Td>
                    <Td>
                      {issue?.message ?? "Rejected"} ({row.side === "origin_account" ? "as sender" : "as receiver"})
                    </Td>
                  </Tr>
                )),
              )}
              {dq.transaction_issues.map((issue) => (
                <Tr key={issue.id}>
                  <Td>Row {issue.source_rows?.row_number ?? "?"}</Td>
                  <Td>
                    <Pill tone="warn">Kept with a caveat</Pill>
                  </Td>
                  <Td className="font-mono text-xs">{issue.rule_id}</Td>
                  <Td>
                    <Pill tone={SEVERITY_TONE[issue.severity]}>{words(issue.severity)}</Pill>
                  </Td>
                  <Td>
                    {issue.message} · {words(issue.status)}
                  </Td>
                </Tr>
              ))}
            </tbody>
          </DataTable>
        )}
        <p className="text-xs text-muted">
          Read {p.limits.transactions_read} transaction{p.limits.transactions_read === 1 ? "" : "s"} (cap {p.limits.max_transactions}){p.limits.truncated ? ", truncated" : ""}; scanned {dq.rejected_rows_scanned} rejected rows
          {dq.rejected_scan_capped ? " (cap reached; older rejected rows were not checked)" : ""}.
        </p>
      </Panel>
    </>
  );
}
