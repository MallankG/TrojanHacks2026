"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo } from "react";
import { cn } from "@/lib/cn";
import { formatMoney } from "@/lib/format";
import { useMaskers } from "@/lib/privacy/store";
import { ROUTES } from "@/lib/routes";
import { dailyFlows, ENTITIES, ENTITY_ORDER } from "@/lib/seed/entities";
import { decideEntityLink, decideScreening, openEvidence, selectGraphNode, setCopilotFocus, toast } from "@/lib/store/actions";
import { useDemoState } from "@/lib/store/hooks";
import type { AccountKey } from "@/lib/types";
import { Button, buttonClasses } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Card, Panel } from "@/components/ui/card";
import { KeyValueList } from "@/components/ui/key-value";
import { Legend, Swatch } from "@/components/ui/misc";
import { PageHeader } from "@/components/ui/page-header";
import { Pill } from "@/components/ui/pill";
import { MaskedRef, SensitiveRef, SensitiveText } from "@/components/ui/sensitive";
import { DataTable, Td, Th } from "@/components/ui/table";

const CHART_HEIGHT = 130;

function FlowChart({ entityKey }: { entityKey: AccountKey }) {
  const entity = ENTITIES[entityKey];
  const days = useMemo(() => dailyFlows(entity), [entity]);
  const max = Math.max(1, ...days.map((d) => Math.max(d.inflow, d.outflow)));
  const height = (v: number) => (v > 0 ? Math.max(2, Math.round(Math.sqrt(v / max) * CHART_HEIGHT)) : 0);
  const baseline = height(Math.max(entity.baseIn, entity.baseOut));
  const mask = useMaskers();

  return (
    <Panel>
      <div className="flex flex-wrap justify-between gap-1.5">
        <h2 className="text-base font-semibold">Daily flow, last 30 days</h2>
        <span className="text-xs text-muted">Square-root scale. Dashed line = baseline.</span>
      </div>
      <div className="relative flex h-[140px] items-end gap-[3px] border-b border-[#d5dce6]" role="img" aria-label={`Daily inflow and outflow for ${mask.ref(entity.id)} over 30 days, with a spike on ${days[entity.spikeDay]?.label}.`}>
        <div aria-hidden="true" className="absolute inset-x-0 border-t-2 border-dashed border-[#8a97ab]" style={{ bottom: baseline }} />
        {days.map((day) => (
          <div key={day.label} title={`${day.label}: in ${formatMoney(day.inflow)}, out ${formatMoney(day.outflow)}`} className="flex h-full flex-1 items-end gap-px">
            <div className="flex-1 rounded-t-sm bg-brand" style={{ height: height(day.inflow) }} />
            <div className="flex-1 rounded-t-sm bg-ink" style={{ height: height(day.outflow) }} />
          </div>
        ))}
      </div>
      <div className="flex gap-[3px]">
        {days.map((day, i) => (
          <span key={day.label} className="flex-1 overflow-visible whitespace-nowrap text-[10px] text-muted">
            {i % 6 === 0 ? day.label : ""}
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

export function EntityProfile({ entityKey }: { entityKey: AccountKey }) {
  const router = useRouter();
  const state = useDemoState();
  const fixed = state.data.driftFixed;
  const entity = ENTITIES[entityKey];
  const dataWarning = Boolean(entity.dataHeld) && !fixed;
  const mask = useMaskers();

  return (
    <>
      <PageHeader
        title="Account and entity profile"
        description="Baseline against what happened. Mismatches are context for review, not proof."
        actions={
          <nav aria-label="Accounts in this case" className="flex flex-wrap gap-1.5">
            {ENTITY_ORDER.map((key) => {
              const active = key === entityKey;
              return (
                <Link
                  key={key}
                  href={ROUTES.entity(ENTITIES[key].id)}
                  aria-current={active ? "page" : undefined}
                  className={cn("inline-flex min-h-9 items-center rounded-full border px-3 font-mono text-[13px] font-semibold", active ? "border-ink bg-ink text-white" : "border-line-strong bg-white text-ink-2 hover:border-[#8a97ab]")}
                >
                  <MaskedRef value={ENTITIES[key].id} />
                </Link>
              );
            })}
          </nav>
        }
      />

      <Panel className="flex-row flex-wrap items-start justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <div className="eyebrow">{entity.role}</div>
          <h2 className="font-mono text-[22px] font-semibold"><SensitiveRef value={entity.id} /></h2>
          <span className="text-[13.5px] text-muted">
            {entity.kind} · {entity.segment}
          </span>
          <div className="mt-1 flex flex-wrap gap-1.5">
            <Pill tone="mute">Opened {entity.opened}</Pill>
            <Pill tone="mute">{entity.customer}</Pill>
            <Pill tone="mute">{entity.geo}</Pill>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              selectGraphNode(entityKey);
              router.push(ROUTES.graph);
            }}
          >
            Show in graph
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setCopilotFocus(entity.id);
              toast(`Copilot context refreshed: ${mask.ref(entity.id)} is now selected.`);
              router.push(ROUTES.copilot);
            }}
          >
            Ask copilot
          </Button>
        </div>
      </Panel>

      {dataWarning ? (
        <Callout tone="warn" title="Data-quality warning." action={<Link href={ROUTES.data} className={buttonClasses("link")}>Open data operations</Link>}>
          <SensitiveText
            value={
              entityKey === "C"
                ? "The outbound transfer to ACC-4471 is in quarantined batch ach_2026_10_06_b. Flow ratios are partial."
                : "The inbound transfer from ACC-3315 is in quarantined batch ach_2026_10_06_b. This profile is incomplete."
            }
          />
        </Callout>
      ) : null}

      <div className="grid grid-cols-[repeat(auto-fit,minmax(340px,1fr))] gap-4">
        <FlowChart entityKey={entityKey} />
        <Card className="overflow-hidden">
          <div className="px-5 py-4">
            <h2 className="text-base font-semibold">Baseline vs observed</h2>
            <span className="text-xs text-muted">Robust z-score of daily volume: {entity.zScore}</span>
          </div>
          <DataTable caption="Baseline versus observed activity">
            <thead>
              <tr>
                <Th>Measure</Th>
                <Th>Baseline (30 d)</Th>
                <Th>Observed (Oct 6 to 9)</Th>
              </tr>
            </thead>
            <tbody>
              {(["Daily volume", "Transactions per day", "Counterparties (30 d)"] as const).map((label, i) => (
                <tr key={label}>
                  <Td>{label}</Td>
                  <Td>{entity.baseline[i]}</Td>
                  <Td className="font-bold">{entity.observed[i]}</Td>
                </tr>
              ))}
            </tbody>
          </DataTable>
          <div className="px-5 py-3.5">
            <KeyValueList
              items={[
                ["Flow ratio", entity.flowRatio],
                ["Retained balance proxy", entity.retained],
                ["Time between transfers", entity.interArrival],
                ["Counterparty concentration", entity.concentration],
              ]}
            />
          </div>
        </Card>
      </div>

      <div className="grid grid-cols-[repeat(auto-fit,minmax(340px,1fr))] gap-4">
        <Card className="overflow-hidden">
          <div className="px-5 py-4">
            <h2 className="text-base font-semibold">Counterparties</h2>
          </div>
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
              {entity.counterparties.map((c) => (
                <tr key={c.id}>
                  <Td>
                    <button type="button" className={buttonClasses("link", "md", "font-mono")} onClick={() => openEvidence(c.id)}>
                      <MaskedRef value={c.id} />
                    </button>
                  </Td>
                  <Td>{c.direction}</Td>
                  <Td>{c.firstSeen}</Td>
                  <Td>
                    <Pill tone={c.status === "New" ? "warn" : "mute"}>{c.status}</Pill>
                  </Td>
                  <Td>{c.amount}</Td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        </Card>
        <Panel>
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-base font-semibold">Profile and geography</h2>
            {entity.mismatch ? <Pill tone="warn">Mismatch: context, not proof</Pill> : null}
          </div>
          <KeyValueList
            items={[
              ["Expected activity", entity.expected],
              ["Observed activity", entity.observedActivity],
              ["Geography", entity.geoNote],
            ]}
          />
        </Panel>
      </div>

      <div className="grid grid-cols-[repeat(auto-fit,minmax(340px,1fr))] gap-4">
        <Panel>
          <h2 className="text-base font-semibold">Shared token links and entity resolution</h2>
          <span className="text-xs text-muted">Candidates are never merged automatically.</span>
          {entity.links.length === 0 ? <span className="text-[13.5px] text-muted">No shared tokens found for this account.</span> : null}
          {entity.links.map((link) => {
            const decision = state.erDecisions[link.key];
            return (
              <div key={link.key} className="flex flex-col gap-1.5 rounded-xl border border-line px-3.5 py-3">
                <div className="flex flex-wrap justify-between gap-2">
                  <b className="font-mono text-[12.5px]">
                    {link.type} · {link.token}
                  </b>
                  <span className="text-xs text-muted">with {link.withId}</span>
                </div>
                <span className="text-[13px]">
                  {link.how}. {link.factors}.
                </span>
                {link.candidate ? (
                  <div className="flex flex-wrap items-center gap-2">
                    {decision ? <Pill tone={decision === "Approved" ? "ok" : "mute"}>{decision} by a person</Pill> : null}
                    <Button variant="ghost" size="sm" onClick={() => decideEntityLink(link.key, entity.id, link.type, link.withId, link.token, "Approved")}>
                      Approve link
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => decideEntityLink(link.key, entity.id, link.type, link.withId, link.token, "Rejected")}>
                      Reject
                    </Button>
                  </div>
                ) : null}
              </div>
            );
          })}
        </Panel>
        <div className="flex flex-col gap-4">
          <Panel>
            <h2 className="text-base font-semibold">Related alerts and cases</h2>
            <div className="flex flex-wrap gap-2">
              {entity.related.map((id) => (
                <Link key={id} href={id.startsWith("CASE") ? ROUTES.case(id) : ROUTES.riskAlert(id)} className="inline-flex min-h-9 items-center rounded-full border border-line-strong bg-white px-3 font-mono text-[13px] font-semibold text-ink-2 hover:border-[#8a97ab]">
                  {id}
                </Link>
              ))}
            </div>
          </Panel>
          {entity.screening ? (
            <Panel>
              <div className="flex justify-between gap-2">
                <h2 className="text-base font-semibold">Screening (mock adapter)</h2>
                <Pill tone="mute">Not live data</Pill>
              </div>
              <span className="text-[13.5px]">Possible match to a synthetic watchlist entry. Name similarity 0.71, no date-of-birth or address match.</span>
              {state.screeningDecision ? (
                <Pill tone="ok" className="self-start">
                  {state.screeningDecision}
                </Pill>
              ) : null}
              <div className="flex flex-wrap gap-2">
                <Button variant="ghost" size="sm" onClick={() => decideScreening("False positive")}>
                  Mark false positive
                </Button>
                <Button variant="ghost" size="sm" onClick={() => decideScreening("Needs review")}>
                  Needs second review
                </Button>
              </div>
            </Panel>
          ) : null}
        </div>
      </div>
    </>
  );
}
