"use client";

import { useMemo, useState } from "react";
import { WEIGHT_KEYS, WEIGHT_LABELS } from "@/lib/domain/priority";
import { benchmarkStats, configError, evaluate, productionConfig, type SandboxConfig, type SandboxResult } from "@/lib/domain/sandbox";
import { BENCHMARK_SEED } from "@/lib/seed/benchmark";
import { saveSandboxProposal } from "@/lib/store/actions";
import { useDemoState, useRole } from "@/lib/store/hooks";
import { FeatureGate } from "@/components/shell/feature-gate";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Card, CardHeader, Panel } from "@/components/ui/card";
import { Field, TextInput } from "@/components/ui/field";
import { Meter } from "@/components/ui/misc";
import { Pill } from "@/components/ui/pill";
import { DataTable, Td, Th } from "@/components/ui/table";

const pct = (v: number | null) => (v === null ? "n/a" : `${(v * 100).toFixed(1)}%`);

function delta(after: number | null, before: number | null, asPercent: boolean, lowerIsBetter = false) {
  if (after === null || before === null) return null;
  const d = after - before;
  if (Math.abs(d) < 1e-9) return <span className="text-xs text-muted">no change</span>;
  const text = asPercent ? `${d > 0 ? "+" : ""}${(d * 100).toFixed(1)} pts` : `${d > 0 ? "+" : ""}${Number.isInteger(d) ? d : d.toFixed(1)}`;
  const better = lowerIsBetter ? d < 0 : d > 0;
  return <Pill tone={better ? "ok" : "warn"}>{text}</Pill>;
}

function MetricsTable({ production, proposal }: { production: SandboxResult; proposal: SandboxResult | null }) {
  const rows: Array<{ label: string; prod: string; prop: string; d: ReturnType<typeof delta> }> = [
    { label: "Alerts for review", prod: String(production.alerts), prop: proposal ? String(proposal.alerts) : "n/a", d: proposal ? delta(proposal.alerts, production.alerts, false, true) : null },
    { label: "Review first", prod: String(production.reviewFirst), prop: proposal ? String(proposal.reviewFirst) : "n/a", d: proposal ? delta(proposal.reviewFirst, production.reviewFirst, false, true) : null },
    { label: "Precision", prod: pct(production.precision), prop: proposal ? pct(proposal.precision) : "n/a", d: proposal ? delta(proposal.precision, production.precision, true) : null },
    { label: "Recall", prod: pct(production.recall), prop: proposal ? pct(proposal.recall) : "n/a", d: proposal ? delta(proposal.recall, production.recall, true) : null },
    { label: "False alerts per 100 candidates", prod: production.falseAlertsPer100.toFixed(1), prop: proposal ? proposal.falseAlertsPer100.toFixed(1) : "n/a", d: proposal ? delta(proposal.falseAlertsPer100, production.falseAlertsPer100, false, true) : null },
  ];
  return (
    <DataTable caption="Production versus proposal">
      <thead>
        <tr>
          <Th>Metric</Th>
          <Th>Production</Th>
          <Th>Proposal</Th>
          <Th>Change</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.label}>
            <Td>{r.label}</Td>
            <Td>{r.prod}</Td>
            <Td className="font-semibold">{r.prop}</Td>
            <Td>{r.d}</Td>
          </tr>
        ))}
      </tbody>
    </DataTable>
  );
}

/** Tests a proposed priority policy or review band on the seeded benchmark. Never changes production. */
export function SandboxView() {
  const state = useDemoState();
  const role = useRole();
  const production = useMemo(() => productionConfig(state.policy), [state.policy]);
  const [draft, setDraft] = useState<SandboxConfig>(() => productionConfig(state.policy));
  const error = configError(draft);
  const prodResult = useMemo(() => evaluate(production), [production]) as SandboxResult;
  const result = useMemo(() => (error ? null : evaluate(draft)), [draft, error]);
  const stats = benchmarkStats();
  const canEdit = role.canEditPolicy;
  const dirty = JSON.stringify(draft) !== JSON.stringify(production);

  const set = (patch: Partial<SandboxConfig>) => setDraft((d) => ({ ...d, ...patch }));
  const summary = result
    ? `weights ${WEIGHT_KEYS.map((k) => draft.weights[k]).join("/")}, cutoff ${draft.cutoff}, band $${draft.bandMin}-$${draft.bandMax} x${draft.minDeposits}: ${result.alerts} alerts, precision ${pct(result.precision)}, recall ${pct(result.recall)}`
    : "";

  return (
    <FeatureGate flag="sandbox">
      <Callout tone="info" title="Sandbox only.">
        Results come from a synthetic labeled benchmark ({stats.size} candidates, {stats.positives} labeled suspicious, seed {BENCHMARK_SEED}). They do not predict performance on real bank data, and nothing here changes production policy {state.policy.version}.
      </Callout>
      <div className="flex flex-wrap items-start gap-4">
        <Panel className="flex-[1_1_340px]">
          <CardHeader title="Proposed configuration" description={canEdit ? "Edit any value; results update as you type." : "Read-only for your role. Only a BSA officer can test proposals."} />
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
            {WEIGHT_KEYS.map((key) => (
              <Field key={key} label={<span className="text-xs">{WEIGHT_LABELS[key]}</span>}>
                {(props) => <TextInput {...props} inputMode="numeric" value={draft.weights[key]} disabled={!canEdit} onChange={(e) => set({ weights: { ...draft.weights, [key]: e.target.value } })} />}
              </Field>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Review cutoff (score)" hint={`Production ${production.cutoff}`}>
              {(props) => <TextInput {...props} inputMode="numeric" value={draft.cutoff} disabled={!canEdit} onChange={(e) => set({ cutoff: e.target.value })} />}
            </Field>
            <Field label="Deposits in band to fire" hint="Structuring-like rule">
              {(props) => <TextInput {...props} inputMode="numeric" value={draft.minDeposits} disabled={!canEdit} onChange={(e) => set({ minDeposits: e.target.value })} />}
            </Field>
            <Field label="Band minimum ($)">{(props) => <TextInput {...props} inputMode="numeric" value={draft.bandMin} disabled={!canEdit} onChange={(e) => set({ bandMin: e.target.value })} />}</Field>
            <Field label="Band maximum ($)">{(props) => <TextInput {...props} inputMode="numeric" value={draft.bandMax} disabled={!canEdit} onChange={(e) => set({ bandMax: e.target.value })} />}</Field>
          </div>
          {error ? (
            <span role="alert" className="text-[13px] font-semibold text-danger-ink">
              {error}
            </span>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={!canEdit || !result || !dirty} onClick={() => saveSandboxProposal(summary)}>
              Record as proposal
            </Button>
            <Button variant="ghost" size="sm" disabled={!dirty} onClick={() => setDraft(production)}>
              Reset to production
            </Button>
          </div>
          <span className="text-xs text-muted">A recorded proposal goes to the audit trail. Applying it still requires saving a new policy version in Policy and settings.</span>
        </Panel>

        <div className="flex min-w-0 flex-[2_1_480px] flex-col gap-4">
          <Card className="overflow-hidden">
            <div className="px-5 py-4">
              <CardHeader title="Production versus proposal" description="Precision and recall are measurable here only because the benchmark has synthetic labels." />
            </div>
            <MetricsTable production={prodResult} proposal={result} />
          </Card>
          {result ? (
            <>
              <Panel>
                <CardHeader title="Typology coverage" description="Labeled suspicious candidates caught at the proposed cutoff." />
                {result.coverage.map((c, i) => {
                  const prod = prodResult.coverage[i];
                  return (
                    <div key={c.typology} className="flex flex-col gap-1">
                      <div className="flex justify-between gap-2 text-[13px]">
                        <span>{c.typology}</span>
                        <span>
                          <b>
                            {c.caught} of {c.total}
                          </b>{" "}
                          <span className="text-xs text-muted">(production {prod.caught})</span>
                        </span>
                      </div>
                      <Meter value={c.total ? c.caught / c.total : 0} label={`${c.typology}: ${c.caught} of ${c.total}`} />
                    </div>
                  );
                })}
              </Panel>
              <div className="grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(260px,1fr))]">
                <Panel>
                  <CardHeader title="False-positive examples" description="Highest-scoring benign candidates the proposal would alert on." />
                  <ExampleList items={result.falsePositiveExamples} empty="No benign candidates above the cutoff." />
                </Panel>
                <Panel>
                  <CardHeader title="Missed examples" description="Highest-scoring suspicious candidates below the cutoff." />
                  <ExampleList items={result.missedExamples} empty="Every labeled suspicious candidate is above the cutoff." />
                </Panel>
              </div>
            </>
          ) : null}
          {state.sandboxProposals.length > 0 ? (
            <Panel>
              <CardHeader title="Recorded proposals" description="Not applied to production." />
              <ul className="flex flex-col">
                {state.sandboxProposals.map((p) => (
                  <li key={p.id} className="border-t border-line-soft py-1.5 text-[13px]">
                    <b className="font-mono">{p.id}</b> · {p.by} · <span className="text-xs text-muted">{p.at}</span>
                    <div className="text-xs text-muted">{p.summary}</div>
                  </li>
                ))}
              </ul>
            </Panel>
          ) : null}
        </div>
      </div>
    </FeatureGate>
  );
}

function ExampleList({ items, empty }: { items: Array<{ id: string; kind: string; score: number }>; empty: string }) {
  if (items.length === 0) return <span className="text-[13px] text-muted">{empty}</span>;
  return (
    <ul className="flex flex-col">
      {items.map((item) => (
        <li key={item.id} className="flex justify-between gap-2 border-t border-line-soft py-1.5 text-[13px]">
          <span>
            <span className="font-mono">{item.id}</span> · {item.kind}
          </span>
          <b>{item.score.toFixed(1)}</b>
        </li>
      ))}
    </ul>
  );
}
