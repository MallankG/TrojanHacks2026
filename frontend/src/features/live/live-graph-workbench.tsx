"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState, type FormEvent } from "react";
import { errorMessage, invalidateLive, liveApi, useLive } from "@/lib/live/api";
import {
  GRAPH_CAPS,
  RAILS,
  graphApi,
  type GraphDirection,
  type GraphFilters,
  type Hop,
  type PatternFinding,
  type PatternOperation,
  type PatternResult,
  type PathResult,
  type Rail,
  type WorkbenchGraph,
} from "@/lib/live/api-graph";
import { alertLabel, caseLabel, money, utc, words } from "@/lib/live/format";
import { LIVE_ROLES } from "@/lib/live/roles";
import { useLiveProfile } from "@/lib/live/session";
import { useMaskers } from "@/lib/privacy/store";
import { ROUTES } from "@/lib/routes";
import { Button, buttonClasses } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Card, CardHeader, Panel } from "@/components/ui/card";
import { Chip, Segmented } from "@/components/ui/controls";
import { Field, Select, TextInput } from "@/components/ui/field";
import { KeyValueList } from "@/components/ui/key-value";
import { CodeBlock, EmptyState, Skeleton } from "@/components/ui/misc";
import { PageHeader } from "@/components/ui/page-header";
import { Pill } from "@/components/ui/pill";
import { MaskedRef, SensitiveRef } from "@/components/ui/sensitive";
import { DataTable, Td, Th, Tr } from "@/components/ui/table";
import { LiveData } from "./live-state";
import { LiveGraphCanvas, type GraphSelection } from "./live-graph-canvas";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const RAIL_LABEL: Record<Rail, string> = { ach: "ACH", wire: "Wire", p2p: "P2P", card: "Card", cash: "Cash", check: "Check", internal_transfer: "Internal", crypto: "Crypto", other: "Other" };
const PATTERN_LABEL: Record<PatternOperation, string> = { cycles: "Cycles", fan_in: "Fan-in", fan_out: "Fan-out", pass_through: "Pass-through" };

/** Hours as "1 d 3 h 10 m" for path summaries. */
export function duration(hours: number | null | undefined): string {
  if (hours === null || hours === undefined) return "n/a";
  const minutes = Math.round(hours * 60);
  const d = Math.floor(minutes / 1440);
  const h = Math.floor((minutes % 1440) / 60);
  const m = minutes % 60;
  return [d ? `${d} d` : "", h ? `${h} h` : "", m || (!d && !h) ? `${m} m` : ""].filter(Boolean).join(" ");
}

/** datetime-local values are read as UTC so the window matches the UTC timestamps shown everywhere. */
function toIso(local: string): string | undefined {
  return local ? `${local.length === 16 ? `${local}:00` : local}Z` : undefined;
}

/** Union of two graph payloads, so a path or pattern found outside the current view still draws. */
function mergeGraphs(base: WorkbenchGraph, extra: WorkbenchGraph | null | undefined): WorkbenchGraph {
  if (!extra) return base;
  const nodes = new Map(base.nodes.map((n) => [n.id, n]));
  for (const n of extra.nodes) if (!nodes.has(n.id)) nodes.set(n.id, { ...n, layer: undefined, role: null });
  const edges = new Map(base.edges.map((e) => [e.id, e]));
  for (const e of extra.edges) if (!edges.has(e.id)) edges.set(e.id, e);
  return { ...base, nodes: [...nodes.values()], edges: [...edges.values()].sort((a, b) => a.occurred_at.localeCompare(b.occurred_at)) };
}

interface Overlay {
  kind: "path" | "pattern";
  title: string;
  transactionIds: string[];
  summary: string;
  graph: WorkbenchGraph | null;
  payload: Record<string, unknown>;
}

/** Live graph workbench: real accounts and transactions, bounded by the backend's caps. */
export function LiveGraphWorkbench() {
  const mask = useMaskers();
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const profile = useLiveProfile();
  const role = LIVE_ROLES[profile?.role ?? "analyst"];
  const requested = params.get("account");
  const alertId = params.get("alert");
  const requestedId = requested && UUID.test(requested) ? requested : null;

  const start = useLive(() => graphApi.accounts(undefined, 8), "graph-accounts:top");
  const focus = requestedId ?? start.data?.suggested?.account_id ?? null;

  const [hops, setHops] = useState(2);
  const [direction, setDirection] = useState<GraphDirection>("both");
  const [rails, setRails] = useState<Rail[]>([]);
  const [minAmount, setMinAmount] = useState(0);
  const [windowStart, setWindowStart] = useState("");
  const [windowEnd, setWindowEnd] = useState("");
  const [view, setView] = useState<"graph" | "table">("graph");
  const [selection, setSelection] = useState<GraphSelection>(null);
  const [overlay, setOverlay] = useState<Overlay | null>(null);

  const filters: GraphFilters = { start: toIso(windowStart), end: toIso(windowEnd), rails, minAmount };
  const filterKey = `${hops}:${direction}:${rails.join(",")}:${minAmount}:${windowStart}:${windowEnd}`;
  const hood = useLive(
    () => (focus ? graphApi.neighborhood(focus, { ...filters, hops, direction }) : Promise.resolve(null)),
    `graph-hood:${focus ?? "none"}:${filterKey}`,
  );
  const alert = useLive(() => (alertId && UUID.test(alertId) ? liveApi.alert(alertId) : Promise.resolve(null)), `alert:${alertId ?? "none"}`);
  const alertAccounts = useMemo(() => new Set((alert.data?.accounts ?? []).map((a) => a.accounts.id)), [alert.data]);

  const focusOn = (id: string) => {
    const next = new URLSearchParams(params.toString());
    next.set("account", id);
    setSelection(null);
    setOverlay(null);
    router.replace(`${pathname}?${next.toString()}`, { scroll: false });
  };

  const shown = useMemo(() => (hood.data ? mergeGraphs(hood.data, overlay?.graph) : null), [hood.data, overlay]);
  const highlight = useMemo(() => new Set(overlay?.transactionIds ?? []), [overlay]);
  const toggleRail = (rail: Rail) => setRails((list) => (list.includes(rail) ? list.filter((r) => r !== rail) : [...list, rail]));
  // Every label below (edges, hops, patterns, inspector) goes through refOf, so masking here covers them.
  const refOf = (id: string) => mask.ref(shown?.nodes.find((n) => n.id === id)?.account_ref ?? id.slice(0, 8));

  return (
    <>
      <PageHeader
        title="Graph investigation workbench"
        description="Live transactions. Time-respecting paths only: each hop happens after the one before it. Every query is bounded by hops, time window, node and edge caps and a time limit."
        actions={
          <>
            <Pill tone="ok">Live</Pill>
            <Segmented<"graph" | "table">
              label="View"
              value={view}
              onChange={setView}
              options={[
                { value: "graph", label: "Graph" },
                { value: "table", label: "Table" },
              ]}
            />
          </>
        }
      />

      {alertId && alert.data ? (
        <Callout tone="info" title={`Opened from ${alertLabel(alert.data.alert_number)} · ${words(alert.data.typology)}.`} action={<Link href={ROUTES.riskAlert(alert.data.id)} className={buttonClasses("link")}>Back to the alert</Link>}>
          <span className="flex flex-wrap items-center gap-1.5">
            Alert accounts (dashed ring):
            {alert.data.accounts.map(({ role: r, accounts: a }) => (
              <Chip key={a.id} mono pressed={a.id === focus} onClick={() => focusOn(a.id)}>
                <MaskedRef value={a.account_ref} /> · {words(r)}
              </Chip>
            ))}
          </span>
        </Callout>
      ) : null}

      <AccountPicker focus={focus} onPick={focusOn} start={start.data} startError={start.error} />

      <Card className="flex flex-wrap items-center gap-x-4 gap-y-3 px-4 py-3">
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-muted">Depth</span>
          <Segmented<number> label="Hop depth" value={hops} onChange={setHops} options={[1, 2, 3].map((d) => ({ value: d, label: `${d} hop${d > 1 ? "s" : ""}` }))} />
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-muted">Direction</span>
          <Segmented<GraphDirection>
            label="Direction"
            value={direction}
            onChange={setDirection}
            options={[
              { value: "incoming", label: "Incoming" },
              { value: "both", label: "Both" },
              { value: "outgoing", label: "Outgoing" },
            ]}
          />
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-muted">Amount</span>
          <Segmented<number>
            label="Minimum amount"
            value={minAmount}
            onChange={setMinAmount}
            options={[
              { value: 0, label: "Any" },
              { value: 1000, label: "≥ $1k" },
              { value: 10000, label: "≥ $10k" },
              { value: 40000, label: "≥ $40k" },
            ]}
          />
        </div>
        <div role="group" aria-label="Rails" className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-muted">Rails</span>
          <Chip pressed={rails.length === 0} onClick={() => setRails([])}>
            All
          </Chip>
          {RAILS.map((rail) => (
            <Chip key={rail} pressed={rails.includes(rail)} onClick={() => toggleRail(rail)}>
              {RAIL_LABEL[rail]}
            </Chip>
          ))}
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <label className="flex flex-col gap-1 text-xs text-muted">
            From (UTC)
            <input type="datetime-local" value={windowStart} onChange={(e) => setWindowStart(e.target.value)} className="min-h-9 rounded-[10px] border border-line-strong px-2 text-[13px] text-ink" />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted">
            To (UTC)
            <input type="datetime-local" value={windowEnd} onChange={(e) => setWindowEnd(e.target.value)} className="min-h-9 rounded-[10px] border border-line-strong px-2 text-[13px] text-ink" />
          </label>
          {windowStart || windowEnd ? (
            <Button variant="link" onClick={() => {
                setWindowStart("");
                setWindowEnd("");
              }}>
              Clear window
            </Button>
          ) : null}
        </div>
        <span className="basis-full font-mono text-[11.5px] text-muted">
          Query guard: max_hops {hops} of {GRAPH_CAPS.neighborhoodHops} · window {hood.data?.source_window?.start && hood.data.source_window.end ? `${hood.data.source_window.start} to ${hood.data.source_window.end}` : windowStart || windowEnd ? `${windowStart || "start"} to ${windowEnd || "end"} UTC` : "14 days around latest activity"} (max {GRAPH_CAPS.windowDays} days) ·{" "}
          {hood.data?.limits ? `max ${hood.data.limits.max_nodes} nodes · ${hood.data.limits.max_edges} edges · timeout ${hood.data.limits.timeout_s} s` : "server caps apply"}
        </span>
      </Card>

      <div className="flex flex-wrap items-start gap-4">
        <Card className="flex min-w-0 flex-[3_1_600px] flex-col gap-3 p-3.5">
          {!focus ? (
            start.loading ? (
              <Skeleton label="Finding a starting account" />
            ) : (
              <EmptyState title="No accounts yet." description="Upload transactions on Data operations, then pick an account here." />
            )
          ) : (
            <LiveData query={hood} label="the account neighborhood">
              {() =>
                shown && shown.edges.length > 0 ? (
                  <>
                    {view === "graph" ? (
                      <>
                        <LiveGraphCanvas graph={shown} focus={focus} selection={selection} onSelect={setSelection} highlight={highlight} marked={alertAccounts} />
                        <div className="flex flex-wrap gap-3.5 text-xs font-semibold text-ink-2">
                          <span>● Internal account</span>
                          <span>○ External account</span>
                          <span>Amber ring = focus · dashed ring = alert account</span>
                          <span>Dashed amber arrows = selected path or pattern</span>
                          <span>Senders to the left, receivers to the right</span>
                        </div>
                      </>
                    ) : (
                      <EdgeTable graph={shown} selection={selection} onSelect={setSelection} highlight={highlight} refOf={refOf} />
                    )}
                    <p className="text-xs text-muted">
                      {shown.nodes.length} accounts · {shown.edges.length} transactions
                      {hood.data?.truncated ? ` · capped (${hood.data.limits?.stopped_by ? words(hood.data.limits.stopped_by) : "edge limit"}); narrow the filters to see the rest` : ""}
                    </p>
                  </>
                ) : (
                  <EmptyState title="No transactions match these filters." description="Widen the time window, add rails or lower the minimum amount." />
                )
              }
            </LiveData>
          )}
          <Callout tone="info" title="Investigative context, not proof.">
            Money in a shared balance is commingled, so a path shows where funds plausibly moved, not that the same funds moved. Pattern search is bounded (3 to {GRAPH_CAPS.cycleHops} hops, at most {GRAPH_CAPS.patternWindowDays} days, capped subgraph) and never claims to be complete.
          </Callout>
        </Card>

        <div className="flex flex-[1_1_340px] flex-col gap-4">
          {shown && selection ? <Inspector graph={shown} selection={selection} refOf={refOf} onFocus={focusOn} isFocus={selection.id === focus} /> : null}
          {focus && shown ? (
            <PathTracer
              key={`paths:${focus}`}
              focus={focus}
              accounts={shown.nodes.filter((n) => n.id !== focus).map((n) => ({ id: n.id, ref: refOf(n.id) }))}
              preferredTarget={selection?.kind === "node" && selection.id !== focus ? selection.id : null}
              filters={filters}
              refOf={refOf}
              active={overlay?.kind === "path" ? overlay.title : null}
              onShow={setOverlay}
            />
          ) : null}
          {focus ? <PatternSearch key={`patterns:${focus}`} focus={focus} filters={filters} refOf={refOf} active={overlay?.kind === "pattern" ? overlay.title : null} onShow={setOverlay} /> : null}
          {overlay ? <PinToCase overlay={overlay} canPin={role.canInvestigate} onClear={() => setOverlay(null)} /> : null}
        </div>
      </div>
    </>
  );
}

function AccountPicker({ focus, onPick, start, startError }: { focus: string | null; onPick: (id: string) => void; start: Awaited<ReturnType<typeof graphApi.accounts>> | null; startError: string | null }) {
  const [text, setText] = useState("");
  const [term, setTerm] = useState("");
  const results = useLive(() => (term ? graphApi.accounts(term, 12) : Promise.resolve(null)), `graph-accounts:q:${term}`);
  const list = term ? (results.data?.items ?? []) : (start?.items ?? []);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    setTerm(text.trim());
  };
  return (
    <Panel>
      <CardHeader
        title="Start from an account"
        description={
          start?.suggested && !term
            ? start.suggested.reason === "top_alert"
              ? `Default: the subject of the top-ranked alert ${alertLabel(start.suggested.alert_number ?? 0)} in the latest ranker run.`
              : "Default: the most connected account (no published alerts yet)."
            : "Search by account reference. Results are ranked by transaction count."
        }
      />
      <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
        <Field label="Account reference" className="min-w-[220px] flex-1">
          {(props) => <TextInput {...props} value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. ACC-7030" maxLength={64} />}
        </Field>
        <Button type="submit" variant="dark">
          Search
        </Button>
        {term ? (
          <Button variant="link" onClick={() => {
              setTerm("");
              setText("");
            }}>
            Show most connected
          </Button>
        ) : null}
      </form>
      {startError ? (
        <Callout tone="bad" role="alert">
          {startError}
        </Callout>
      ) : null}
      {results.error ? (
        <Callout tone="bad" role="alert">
          {results.error}
        </Callout>
      ) : null}
      {term && results.loading ? <Skeleton lines={1} label="Searching accounts" /> : null}
      {term && !results.loading && list.length === 0 ? <p className="text-xs text-muted">No account reference contains “{term}”.</p> : null}
      <div className="flex flex-wrap gap-1.5">
        {list.map((a) => (
          <Chip key={a.id} mono pressed={a.id === focus} onClick={() => onPick(a.id)} title={`${a.in_count} in, ${a.out_count} out, ${a.counterparty_count} counterparties, ${money(a.volume_usd)} volume`}>
            <MaskedRef value={a.account_ref} />
            <span className="font-sans text-[11.5px] font-medium opacity-80">
              {a.degree} txns · {money(a.volume_usd).replace(/\.00$/, "")}
            </span>
          </Chip>
        ))}
      </div>
    </Panel>
  );
}

function EdgeTable({ graph, selection, onSelect, highlight, refOf }: { graph: WorkbenchGraph; selection: GraphSelection; onSelect: (s: GraphSelection) => void; highlight: Set<string>; refOf: (id: string) => string }) {
  return (
    <DataTable caption="Visible transactions">
      <thead>
        <tr>
          <Th>Transaction</Th>
          <Th>From</Th>
          <Th>To</Th>
          <Th>Amount</Th>
          <Th>Rail</Th>
          <Th>Time (UTC)</Th>
        </tr>
      </thead>
      <tbody>
        {graph.edges.map((e) => {
          const selected = selection?.kind === "edge" && selection.id === e.id;
          return (
            <Tr key={e.id} selected={selected}>
              <Td>
                <button type="button" aria-pressed={selected} className={buttonClasses("link", "md", "font-mono")} onClick={() => onSelect({ kind: "edge", id: e.id })}>
                  {e.transaction_ref ?? e.id.slice(0, 8)}
                </button>
                {highlight.has(e.id) ? (
                  <Pill tone="warn" className="ml-1.5">
                    On selected path
                  </Pill>
                ) : null}
              </Td>
              <Td className="font-mono">{refOf(e.source)}</Td>
              <Td className="font-mono">{refOf(e.target)}</Td>
              <Td>{money(e.amount, e.currency)}</Td>
              <Td>{words(e.channel)}</Td>
              <Td className="whitespace-nowrap text-xs">{utc(e.occurred_at)}</Td>
            </Tr>
          );
        })}
      </tbody>
    </DataTable>
  );
}

function Inspector({ graph, selection, refOf, onFocus, isFocus }: { graph: WorkbenchGraph; selection: NonNullable<GraphSelection>; refOf: (id: string) => string; onFocus: (id: string) => void; isFocus: boolean }) {
  const [showRow, setShowRow] = useState<string | null>(null);
  if (selection.kind === "node") {
    const n = graph.nodes.find((x) => x.id === selection.id);
    if (!n) return null;
    const inShown = graph.edges.filter((e) => e.target === n.id).length;
    const outShown = graph.edges.filter((e) => e.source === n.id).length;
    return (
      <Panel>
        <div className="eyebrow">Selected account</div>
        <h2 className="font-mono text-base font-semibold"><SensitiveRef value={n.account_ref ?? n.id} /></h2>
        <KeyValueList
          items={[
            ["Account", <MaskedRef key="ref" value={n.account_ref} />],
            ["Institution", n.institution_ref ?? "Not recorded"],
            ["Type", n.is_internal === false ? "External" : "Internal"],
            ["Shown in", `${inShown} transaction${inShown === 1 ? "" : "s"}`],
            ["Shown out", `${outShown} transaction${outShown === 1 ? "" : "s"}`],
          ]}
        />
        {!isFocus ? (
          <div className="flex flex-wrap gap-2">
            <Button variant="ghost" size="sm" onClick={() => onFocus(n.id)}>
              Focus the graph here
            </Button>
          </div>
        ) : (
          <span className="text-xs text-muted">This is the focus account.</span>
        )}
      </Panel>
    );
  }
  const e = graph.edges.find((x) => x.id === selection.id);
  if (!e) return null;
  return (
    <Panel>
      <div className="eyebrow">Selected transaction</div>
      <h2 className="font-mono text-base font-semibold">{e.transaction_ref ?? e.id.slice(0, 8)}</h2>
      <KeyValueList
        items={[
          ["Sender", <span key="s" className="font-mono">{refOf(e.source)}</span>],
          ["Receiver", <span key="r" className="font-mono">{refOf(e.target)}</span>],
          ["Amount", money(e.amount, e.currency)],
          ...(e.currency !== "USD" && e.amount_usd ? ([["USD equivalent", money(e.amount_usd)]] as Array<[string, string]>) : []),
          ["Rail", words(e.channel)],
          ["Time", utc(e.occurred_at)],
          ["Transaction id", <span key="i" className="font-mono text-xs">{e.id}</span>],
        ]}
      />
      <div className="flex flex-wrap gap-2">
        {e.source_row_id ? (
          <Button variant="ghost" size="sm" aria-expanded={showRow === e.id} onClick={() => setShowRow(showRow === e.id ? null : e.id)}>
            {showRow === e.id ? "Hide source row" : "Open source row"}
          </Button>
        ) : null}
        <Link href={ROUTES.dataQuality} className={buttonClasses("ghost", "sm")}>
          Quality exceptions
        </Link>
      </div>
      {showRow === e.id && e.source_row_id ? <SourceRowPeek rowId={e.source_row_id} /> : null}
    </Panel>
  );
}

/** Lineage drill-through: the raw source row behind a transaction and its data-quality findings. */
function SourceRowPeek({ rowId }: { rowId: string }) {
  const row = useLive(() => liveApi.sourceRow(rowId), `row:${rowId}`);
  const mask = useMaskers();
  return (
    <LiveData query={row} label="source row">
      {(d) => (
        <div className="flex flex-col gap-2 border-t border-line-soft pt-3">
          <KeyValueList
            items={[
              ["File", d.source_row.ingestion_runs?.source_files?.filename ?? "Not recorded"],
              ["Row", d.source_row.row_number],
              ["Validation", words(d.source_row.status)],
              ["Ingestion run", <span key="r" className="font-mono text-xs">{d.source_row.run_id.slice(0, 8)}</span>],
              ["Data-quality findings", d.issues.length],
              ["Alerts using it", d.downstream.alerts.length],
            ]}
          />
          {d.issues.length > 0 ? (
            <Callout tone="warn" title="This row has data-quality findings." action={<Link href={ROUTES.dataQuality} className={buttonClasses("link")}>Review in quality exceptions</Link>}>
              {d.issues.map((i) => `${i.rule_id}: ${mask.text(i.message)}`).join(" · ")}
            </Callout>
          ) : null}
          <b className="text-[13px]">Original values</b>
          <CodeBlock>{JSON.stringify(mask.record(d.source_row.raw_data), null, 2)}</CodeBlock>
        </div>
      )}
    </LiveData>
  );
}

function HopList({ hops, refOf }: { hops: Hop[]; refOf: (id: string) => string }) {
  return (
    <ol className="flex flex-col gap-1 text-xs">
      {hops.map((h, i) => (
        <li key={h.transaction_id} className="flex flex-wrap gap-x-2">
          <span className="font-mono font-semibold">
            {i + 1}. {refOf(h.from)} → {refOf(h.to)}
          </span>
          <span>{money(h.amount, h.currency)}</span>
          <span className="text-muted">
            {h.channel ? `${words(h.channel)} · ` : ""}
            {utc(h.occurred_at)}
            {h.gap_hours !== null ? ` · +${duration(h.gap_hours)}` : ""}
          </span>
        </li>
      ))}
    </ol>
  );
}

function PathTracer({
  focus,
  accounts,
  preferredTarget,
  filters,
  refOf,
  active,
  onShow,
}: {
  focus: string;
  accounts: Array<{ id: string; ref: string }>;
  preferredTarget: string | null;
  filters: GraphFilters;
  refOf: (id: string) => string;
  active: string | null;
  onShow: (overlay: Overlay) => void;
}) {
  const [target, setTarget] = useState("");
  const [maxHops, setMaxHops] = useState(5);
  const [maxGap, setMaxGap] = useState(72);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PathResult | null>(null);
  const chosen = target || preferredTarget || "";
  const options = [...accounts].sort((a, b) => a.ref.localeCompare(b.ref));

  const trace = async () => {
    if (!chosen) return;
    setBusy(true);
    setError(null);
    try {
      const r = await graphApi.paths({ ...filters, source: focus, target: chosen, maxHops, maxGapHours: maxGap });
      setResult(r);
      if (r.paths[0]) show(r, 0);
    } catch (problem) {
      setError(errorMessage(problem));
      setResult(null);
    } finally {
      setBusy(false);
    }
  };

  const title = (i: number) => `Path ${i + 1}: ${refOf(focus)} to ${refOf(chosen)}`;
  const show = (r: PathResult, i: number) => {
    const p = r.paths[i];
    onShow({
      kind: "path",
      title: title(i),
      transactionIds: p.hops.map((h) => h.transaction_id),
      summary: `${p.length}-hop time-respecting path ${p.hops.map((h) => refOf(h.from)).join(" → ")} → ${refOf(p.hops[p.hops.length - 1].to)}; ${duration(p.elapsed_hours)} elapsed, largest gap ${duration(p.max_gap_hours)}, ${money(p.start_amount_usd)} entered, ${money(p.end_amount_usd)} on the last hop (difference ${money(p.amount_delta_usd)}).`,
      graph: r.graph,
      payload: { kind: "time_respecting_path", source_account_id: focus, target_account_id: chosen, parameters: r.parameters, hops: p.hops, elapsed_hours: p.elapsed_hours, amount_delta_usd: p.amount_delta_usd, caveat: r.caveat },
    });
  };

  return (
    <Panel>
      <CardHeader title="Path tracer" description="Time-respecting paths from the focus account: each hop happens after the previous one, within the gap limit." />
      <div className="flex flex-col gap-2">
        <Field label="To account" hint="Accounts in the current view. Select a node on the graph to pick it.">
          {(props) => (
            <Select {...props} value={chosen} onChange={(e) => setTarget(e.target.value)}>
              <option value="">Choose an account</option>
              {options.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.ref}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted">Max hops</span>
          <Segmented<number> label="Maximum hops" value={maxHops} onChange={setMaxHops} options={[2, 3, 4, 5, 6].map((h) => ({ value: h, label: String(h) }))} />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted">Max gap</span>
          <Segmented<number>
            label="Maximum gap between hops"
            value={maxGap}
            onChange={setMaxGap}
            options={[
              { value: 24, label: "1 day" },
              { value: 72, label: "3 days" },
              { value: 168, label: "7 days" },
            ]}
          />
        </div>
        <div>
          <Button size="sm" disabled={!chosen || busy} onClick={trace}>
            {busy ? "Tracing" : "Trace paths"}
          </Button>
        </div>
      </div>
      {error ? (
        <Callout tone="bad" role="alert">
          {error}
        </Callout>
      ) : null}
      {result ? (
        result.paths.length === 0 ? (
          <p className="text-xs text-muted">No time-respecting path within {maxHops} hops and a {duration(maxGap)} gap ({result.searched_edges} transactions searched).</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {result.paths.map((p, i) => (
              <li key={p.hops.map((h) => h.transaction_id).join("-")} className="flex flex-col gap-1.5 rounded-xl border border-line p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <b className="text-[13px]">{title(i)}</b>
                  <Button variant={active === title(i) ? "dark" : "ghost"} size="sm" aria-pressed={active === title(i)} onClick={() => show(result, i)}>
                    {active === title(i) ? "Shown on graph" : "Show on graph"}
                  </Button>
                </div>
                <KeyValueList
                  items={[
                    ["Hops", p.length],
                    ["Elapsed", duration(p.elapsed_hours)],
                    ["Largest gap", duration(p.max_gap_hours)],
                    ["Entered", money(p.start_amount_usd)],
                    ["Last hop", money(p.end_amount_usd)],
                    ["Difference", money(p.amount_delta_usd)],
                  ]}
                />
                <HopList hops={p.hops} refOf={refOf} />
              </li>
            ))}
          </ul>
        )
      ) : null}
    </Panel>
  );
}

function PatternSearch({ focus, filters, refOf, active, onShow }: { focus: string; filters: GraphFilters; refOf: (id: string) => string; active: string | null; onShow: (overlay: Overlay) => void }) {
  const [operation, setOperation] = useState<PatternOperation>("cycles");
  const [maxHops, setMaxHops] = useState(6);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PatternResult | null>(null);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await graphApi.patterns({
        operation,
        account_id: focus,
        start: filters.start,
        end: filters.end,
        channels: filters.rails.length ? filters.rails : undefined,
        min_amount: filters.minAmount || undefined,
        ...(operation === "cycles" ? { max_hops: maxHops } : {}),
        ...(operation === "fan_in" || operation === "fan_out" ? { min_counterparties: 2 } : {}),
      });
      setResult(r);
      if (r.findings[0]) show(r, r.findings[0], 0);
    } catch (problem) {
      setError(errorMessage(problem));
      setResult(null);
    } finally {
      setBusy(false);
    }
  };

  const title = (r: PatternResult, i: number) => `${PATTERN_LABEL[r.operation]} ${i + 1} around ${refOf(r.account_id)}`;
  const show = (r: PatternResult, f: PatternFinding, i: number) =>
    onShow({
      kind: "pattern",
      title: title(r, i),
      transactionIds: f.transaction_ids,
      summary: `${PATTERN_LABEL[r.operation]} lead: ${f.summary} Accounts: ${f.account_ids.map(refOf).join(" → ")}.`,
      graph: r.graph,
      payload: { kind: f.kind, operation: r.operation, anchor_account_id: f.anchor_account_id, account_ids: f.account_ids, window: r.window, parameters: r.parameters, elapsed_hours: f.elapsed_hours, amount_delta_usd: f.amount_delta_usd ?? null, limitation: r.limitation, caveat: r.caveat },
    });

  return (
    <Panel>
      <CardHeader title="Pattern search" description="Bounded searches around the focus account, using the time window, rails and minimum amount above." />
      <Segmented<PatternOperation> label="Pattern" value={operation} onChange={setOperation} options={(Object.keys(PATTERN_LABEL) as PatternOperation[]).map((op) => ({ value: op, label: PATTERN_LABEL[op] }))} />
      {operation === "cycles" ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted">Max hops</span>
          <Segmented<number> label="Maximum cycle length" value={maxHops} onChange={setMaxHops} options={[3, 4, 6, 8, 10].map((h) => ({ value: h, label: String(h) }))} />
        </div>
      ) : null}
      <p className="text-xs text-muted">
        {operation === "cycles"
          ? "Round trips that leave the account and return to it, each hop after the previous one within 3 days."
          : operation === "pass_through"
            ? "Money in, then at least 80% of it out again within 3 days."
            : `At least 2 distinct counterparties paying ${operation === "fan_in" ? "into" : "out of"} the account within 3 days.`}{" "}
        Without a time window, the search uses 7 days either side of the account&apos;s latest transaction.
      </p>
      <div>
        <Button size="sm" disabled={busy} onClick={run}>
          {busy ? "Searching" : `Search ${PATTERN_LABEL[operation].toLowerCase()}`}
        </Button>
      </div>
      {error ? (
        <Callout tone="bad" role="alert">
          {error}
        </Callout>
      ) : null}
      {result ? (
        <div className="flex flex-col gap-2">
          <p className="text-xs text-muted">
            {result.findings.length} finding{result.findings.length === 1 ? "" : "s"} · {result.searched.edges} transactions and {result.searched.accounts} accounts searched in {Math.round(result.elapsed_ms)} ms · window {utc(result.window.start)} to {utc(result.window.end)}
            {result.searched.truncated ? " · subgraph capped" : ""}
            {result.searched.budget_exhausted ? " · expansion budget reached" : ""}
            {result.searched.timed_out ? " · time limit reached" : ""}
          </p>
          {result.findings.map((f, i) => (
            <div key={f.transaction_ids.join("-")} className="flex flex-col gap-1.5 rounded-xl border border-line p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <b className="text-[13px]">{title(result, i)}</b>
                <Button variant={active === title(result, i) ? "dark" : "ghost"} size="sm" aria-pressed={active === title(result, i)} onClick={() => show(result, f, i)}>
                  {active === title(result, i) ? "Shown on graph" : "Show on graph"}
                </Button>
              </div>
              <p className="text-[13px]">{f.summary}</p>
              <span className="font-mono text-xs">{f.account_ids.map(refOf).join(" → ")}</span>
              <HopList hops={f.hops} refOf={refOf} />
            </div>
          ))}
          <Callout tone="mute" title="Bounded search, not complete enumeration.">
            {result.limitation}
          </Callout>
        </div>
      ) : null}
    </Panel>
  );
}

function PinToCase({ overlay, canPin, onClear }: { overlay: Overlay; canPin: boolean; onClear: () => void }) {
  const cases = useLive(async () => (await liveApi.cases(100)).items.filter((c) => c.status !== "closed"), "cases:open-for-pin");
  const [caseId, setCaseId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pinned, setPinned] = useState<string | null>(null);

  const pin = async () => {
    if (!caseId) return;
    setBusy(true);
    setError(null);
    try {
      await graphApi.pinEvidence(caseId, {
        evidence_type: "transaction_path",
        explanation: `${overlay.summary} Investigative context, not proof: balances are commingled.`,
        transaction_ids: overlay.transactionIds,
        source_ref: "graph-workbench",
        payload: overlay.payload,
      });
      invalidateLive(`case:${caseId}`);
      setPinned(caseId);
    } catch (problem) {
      setError(errorMessage(problem));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel>
      <CardHeader
        title="Pin to case"
        description={overlay.title}
        actions={
          <Button variant="link" onClick={onClear}>
            Clear highlight
          </Button>
        }
      />
      <p className="text-xs text-muted">{overlay.summary}</p>
      {!canPin ? (
        <p className="text-xs text-muted">Your role can view the graph but cannot add case evidence.</p>
      ) : (
        <LiveData query={cases} label="open cases">
          {(list) =>
            list.length === 0 ? (
              <p className="text-xs text-muted">
                No open live cases. Open one from a live alert on{" "}
                <Link href={ROUTES.risk} className="font-semibold text-brand-link underline underline-offset-2">
                  Risk intelligence
                </Link>
                .
              </p>
            ) : (
              <div className="flex flex-wrap items-end gap-2">
                <Field label="Case" className="min-w-[200px] flex-1">
                  {(props) => (
                    <Select {...props} value={caseId} onChange={(e) => {
                        setCaseId(e.target.value);
                        setPinned(null);
                      }}>
                      <option value="">Choose a case</option>
                      {list.map((c) => (
                        <option key={c.id} value={c.id}>
                          {caseLabel(c.case_number)} · {c.title}
                        </option>
                      ))}
                    </Select>
                  )}
                </Field>
                <Button size="sm" disabled={!caseId || busy} onClick={pin}>
                  {busy ? "Pinning" : `Pin ${overlay.transactionIds.length} transaction${overlay.transactionIds.length === 1 ? "" : "s"}`}
                </Button>
              </div>
            )
          }
        </LiveData>
      )}
      {pinned ? (
        <Callout tone="ok" title="Pinned as transaction-path evidence." action={<Link href={ROUTES.case(pinned)} className={buttonClasses("link")}>Open the case</Link>}>
          The case now cites these {overlay.transactionIds.length} transactions with the caveat that this is investigative context, not proof.
        </Callout>
      ) : null}
      {error ? (
        <Callout tone="bad" role="alert">
          {error}
        </Callout>
      ) : null}
    </Panel>
  );
}
