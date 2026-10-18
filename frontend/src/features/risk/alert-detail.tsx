"use client";

import Link from "next/link";
import { graphContext, typologySignals, typologyTone, whyBenign, whyFlagged, type CitedPoint } from "@/lib/domain/alert-insights";
import { BAND_THRESHOLDS } from "@/lib/domain/priority";
import { formatMoney } from "@/lib/format";
import { ROUTES } from "@/lib/routes";
import { shapFor } from "@/lib/seed/alerts";
import { toast } from "@/lib/store/actions";
import { useDemoState, useScoredAlerts } from "@/lib/store/hooks";
import type { AlertId } from "@/lib/types";
import { Button, buttonClasses } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Card, CardHeader, Panel } from "@/components/ui/card";
import { CiteList } from "@/components/ui/cite";
import { KeyValueList, StatBox } from "@/components/ui/key-value";
import { Meter } from "@/components/ui/misc";
import { Pill } from "@/components/ui/pill";
import { DataTable, Td, Th } from "@/components/ui/table";
import { useFlag } from "@/components/shell/feature-gate";
import { AlertChangeLog } from "./alert-changes";
import { SignalBalance } from "./signal-balance";

function PointList({ points }: { points: CitedPoint[] }) {
  return (
    <ul className="flex list-disc flex-col gap-1.5 pl-[18px] text-[13.5px]">
      {points.map((point) => (
        <li key={point.text}>
          {point.text}
          <br />
          <CiteList ids={point.cites} />
        </li>
      ))}
    </ul>
  );
}

export function AlertDetail({ alertId }: { alertId: AlertId }) {
  const state = useDemoState();
  const alerts = useScoredAlerts();
  const fixed = state.data.driftFixed;
  const policyVersion = state.policy.version;
  const alert = alerts.find((a) => a.id === alertId) ?? alerts[0];
  const isMain = alert.id === "AL-1027";
  const rescored = fixed && isMain;
  const counterOn = useFlag("counterEvidence");
  const diffOn = useFlag("alertDiff");
  const passportOn = useFlag("passport");

  const components = [
    { name: "XGBoost", version: "xgb-aml v1.4.2 · threshold 0.62", value: alert.xgb.toFixed(2), meter: alert.xgb, note: alert.xgb >= 0.62 ? "Above threshold" : "Below threshold" },
    { name: "Anomaly", version: "iforest v0.9 · shadow", value: alert.anomaly.toFixed(2), meter: alert.anomaly, note: "Ranking signal, not a probability" },
    { name: "Typology rules", version: "typology-rules v3.2", value: `${alert.rulesFired} of 9 fired`, meter: alert.typologyScore, note: "Deterministic, parameters versioned" },
    { name: "Graph signals", version: `GraphBase · ${fixed ? "GS-0412-b" : "GS-0412-a"}`, value: alert.graphScore.toFixed(2), meter: alert.graphScore, note: alert.graphText },
    { name: "Data quality", version: "Contract checks", value: alert.dataQuality, meter: alert.dataQuality === "Complete" ? 1 : 0.6, note: alert.dataQuality === "Complete" ? "All source rows linked" : "Missing data is not treated as low risk" },
  ];

  const shap = shapFor(alert.id, fixed);
  const maxShap = Math.max(...shap.map((s) => Math.abs(s.contribution)));

  return (
    <div className="flex min-w-0 flex-[3_1_560px] flex-col gap-4">
      <Panel>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="eyebrow">
              {alert.id} · cluster {alert.cluster ?? "None"}
            </div>
            <h2 className="mt-1 text-[22px] font-semibold">{alert.typology}</h2>
            <div className="mt-2 flex flex-wrap gap-1.5">
              <Pill tone={alert.bandTone}>
                {alert.bandLabel} · {alert.score.toFixed(1)}
              </Pill>
              <Pill tone={alert.dataQualityTone}>Data: {alert.dataQuality}</Pill>
              <Pill tone="mute">{alert.status}</Pill>
              <Pill tone="mute">SLA: {alert.sla}</Pill>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link href={ROUTES.graph} className={buttonClasses("ghost", "sm")}>
              Trace in workbench
            </Link>
            <Link href={ROUTES.copilot} className={buttonClasses("ghost", "sm")}>
              Ask copilot
            </Link>
            {passportOn ? (
              <Link href={ROUTES.riskPassport(alert.id)} className={buttonClasses("ghost", "sm")}>
                Evidence passport
              </Link>
            ) : null}
            {alert.caseId ? (
              <Link href={ROUTES.case(alert.caseId)} className={buttonClasses("primary", "sm")}>
                Open {alert.caseId}
              </Link>
            ) : (
              <Button size="sm" onClick={() => toast(`Case creation is shown on CASE-0412 in this prototype. ${alert.id} was added to the triage list.`)}>
                Open a case
              </Button>
            )}
          </div>
        </div>
        <div className="grid grid-cols-[repeat(auto-fit,minmax(130px,1fr))] gap-2.5">
          <StatBox label="Est. amount in window" value={formatMoney(alert.amount)} />
          <StatBox label="Connected accounts" value={alert.accounts} />
          <StatBox label="Alert age" value={alert.age} />
        </div>
        {alert.dataQuality !== "Complete" ? (
          <Callout tone="warn" title="Data-quality caveat." action={<Link href={ROUTES.data} className={buttonClasses("link")}>Open data operations</Link>}>
            {alert.dataQuality}. Missing data is not treated as low risk, so this priority may be understated.
          </Callout>
        ) : null}
        {alert.benign ? (
          <Callout tone="info" title="Benign lookalike.">
            Strong counter-evidence suggests payroll. Human review is still required to close it.
          </Callout>
        ) : null}
      </Panel>

      <div className="grid grid-cols-[repeat(auto-fit,minmax(340px,1fr))] gap-4">
        <Panel>
          <CardHeader title="Component signals" description="Kept separate. No single opaque risk number." />
          {components.map((c) => (
            <div key={c.name} className="flex flex-col gap-1">
              <div className="flex justify-between gap-2 text-[13.5px] font-bold">
                <span>{c.name}</span>
                <span>{c.value}</span>
              </div>
              <Meter value={c.meter} label={`${c.name}: ${c.value}`} />
              <span className="text-xs text-muted">
                {c.version} · {c.note}
              </span>
            </div>
          ))}
        </Panel>
        <Panel>
          <CardHeader title="Why this priority" description={`Priority policy ${policyVersion} · weight × signal = points. ${BAND_THRESHOLDS.first}+ review first, ${BAND_THRESHOLDS.next}+ review next.`} />
          <DataTable caption="Priority calculation">
            <thead>
              <tr>
                <Th>Signal</Th>
                <Th>Value</Th>
                <Th>Weight</Th>
                <Th>Points</Th>
              </tr>
            </thead>
            <tbody>
              {alert.parts.map((part) => (
                <tr key={part.label}>
                  <Td>{part.label}</Td>
                  <Td>{part.value.toFixed(2)}</Td>
                  <Td>{part.weight}%</Td>
                  <Td className="font-bold">{part.points.toFixed(1)}</Td>
                </tr>
              ))}
              <tr>
                <Td colSpan={3} className="font-bold">
                  Total
                </Td>
                <Td className="font-bold">{alert.score.toFixed(1)}</Td>
              </tr>
            </tbody>
          </DataTable>
        </Panel>
      </div>

      <Panel>
        <CardHeader title="XGBoost feature contributions" actions={<span className="text-xs text-muted">Contributions to the score, not causes. Blue lowers the score, teal raises it.</span>} />
        <div role="list" aria-label="Feature contributions">
          {shap.map((s) => (
            <div role="listitem" key={s.feature} className="grid grid-cols-[minmax(150px,1.2fr)_70px_1fr_1fr_52px] items-center gap-2 py-1 text-[13px]">
              <span className="font-mono">{s.feature}</span>
              <span className="text-xs text-muted">= {s.value}</span>
              <div className="flex h-4 justify-end border-r-2 border-[#8a97ab]">
                <div className="rounded-l-[3px] bg-select" style={{ width: s.contribution < 0 ? `${Math.round((-s.contribution / maxShap) * 100)}%` : "0%" }} />
              </div>
              <div className="flex h-4">
                <div className="rounded-r-[3px] bg-brand" style={{ width: s.contribution > 0 ? `${Math.round((s.contribution / maxShap) * 100)}%` : "0%" }} />
              </div>
              <b className="text-right">{`${s.contribution > 0 ? "+" : ""}${s.contribution.toFixed(2)}`}</b>
            </div>
          ))}
        </div>
      </Panel>

      {counterOn ? (
        <SignalBalance alert={alert} fixed={fixed} />
      ) : (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(340px,1fr))] gap-4">
          <Panel>
            <h2 className="text-base font-semibold">Why this alert</h2>
            <PointList points={whyFlagged(alert, fixed)} />
          </Panel>
          <Panel>
            <h2 className="text-base font-semibold">Why it might be benign</h2>
            <PointList points={whyBenign(alert)} />
          </Panel>
        </div>
      )}

      {diffOn ? <AlertChangeLog alertId={alert.id} /> : null}

      <div className="grid grid-cols-[repeat(auto-fit,minmax(340px,1fr))] gap-4">
        <Card className="overflow-hidden">
          <div className="px-5 py-4">
            <h2 className="text-base font-semibold">Typology signals</h2>
          </div>
          <DataTable caption="Typology signals">
            <thead>
              <tr>
                <Th>Typology</Th>
                <Th>Result</Th>
                <Th>Parameters</Th>
                <Th>Evidence</Th>
              </tr>
            </thead>
            <tbody>
              {typologySignals(alert, fixed, state.policy.bandMin, state.policy.bandMax).map((t) => (
                <tr key={t.name}>
                  <Td>{t.name}</Td>
                  <Td>
                    <Pill tone={typologyTone(t.result)}>{t.result}</Pill>
                  </Td>
                  <Td className="text-xs text-muted">{t.parameters}</Td>
                  <Td>
                    <CiteList ids={t.cites} />
                  </Td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        </Card>
        <div className="flex flex-col gap-4">
          <Panel>
            <CardHeader
              title="Graph context"
              actions={
                <Link href={ROUTES.graph} className={buttonClasses("ghost", "sm")}>
                  Open workbench
                </Link>
              }
            />
            <KeyValueList items={graphContext(alert, fixed)} />
          </Panel>
          <Panel>
            <h2 className="text-base font-semibold">Model version and run</h2>
            <KeyValueList
              items={[
                ["Scoring run", rescored ? "SR-2292 (re-scored after data fix)" : "SR-2291"],
                ["Scored at", rescored ? "Oct 9, 14:06 UTC" : "Oct 9, 13:40 UTC"],
                ["Feature set", "v7, as-of scoring time"],
                ["Priority policy", `${policyVersion} (weights in Governance)`],
                ["Artifact checksum", "sha256:5b1e…9a40 (simulated)"],
              ]}
            />
          </Panel>
        </div>
      </div>
    </div>
  );
}
