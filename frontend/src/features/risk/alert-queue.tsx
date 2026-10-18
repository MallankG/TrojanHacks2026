"use client";

import Link from "next/link";
import { cn } from "@/lib/cn";
import { formatMoney } from "@/lib/format";
import { ROUTES } from "@/lib/routes";
import { OPEN_ALERT_TOTAL } from "@/lib/seed/alerts";
import { updateRiskQueue } from "@/lib/store/actions";
import type { QueueBandFilter, QueueStatusFilter } from "@/lib/store/state";
import { useDemoState, useScoredAlerts } from "@/lib/store/hooks";
import type { AlertId } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Chip, Segmented } from "@/components/ui/controls";
import { TextInput } from "@/components/ui/field";
import { EmptyState } from "@/components/ui/misc";
import { Pill } from "@/components/ui/pill";
import { useLiveProfile } from "@/lib/live/session";
import { LiveAlertEntries } from "@/features/live/live-alerts";

type BandFilter = QueueBandFilter;
type StatusFilter = QueueStatusFilter;

export function AlertQueue({ selectedId }: { selectedId: AlertId | string }) {
  const profile=useLiveProfile();
  const alerts = useScoredAlerts();
  // Filters live in the store so they survive moving between alert pages.
  const { text, band, status, grouped } = useDemoState().riskQueue;
  const setText = (value: string) => updateRiskQueue({ text: value });
  const setBand = (value: BandFilter) => updateRiskQueue({ band: value });
  const setStatus = (value: StatusFilter) => updateRiskQueue({ status: value });

  const q = text.trim().toLowerCase();
  const rows = alerts.filter(
    (a) =>
      (band === "all" || a.band === band) &&
      (status === "all" || (status === "new" ? a.status === "New" : a.status !== "New")) &&
      (q === "" || `${a.id} ${a.typology}`.toLowerCase().includes(q)),
  );

  const clear = () => updateRiskQueue({ text: "", band: "all", status: "all" });

  if(profile) return <Card aria-label="Ranked alerts" className="flex min-w-0 max-w-[420px] flex-[1_1_340px] flex-col gap-2.5 p-3.5"><LiveAlertEntries selectedId={selectedId}/></Card>;

  return (
    <Card aria-label="Ranked alerts" className="flex min-w-0 max-w-[420px] flex-[1_1_340px] flex-col gap-2.5 p-3.5">
      {/* Live backend alerts (nothing without a live session), then the seeded sample queue. */}
      <LiveAlertEntries selectedId={selectedId} />
      <label>
        <span className="sr-only">Filter alerts</span>
        <TextInput value={text} onChange={(e) => setText(e.target.value)} placeholder="Filter by ID or typology" />
      </label>
      <div role="group" aria-label="Priority band" className="flex flex-wrap gap-1.5">
        {(
          [
            ["all", "All"],
            [1, "Review first"],
            [2, "Review next"],
            [3, "Review when able"],
          ] as Array<[BandFilter, string]>
        ).map(([value, label]) => (
          <Chip key={String(value)} pressed={band === value} onClick={() => setBand(value)}>
            {label}
          </Chip>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <Segmented<StatusFilter>
          label="Status"
          value={status}
          onChange={setStatus}
          options={[
            { value: "all", label: "All" },
            { value: "new", label: "New" },
            { value: "case", label: "In a case" },
          ]}
        />
        <Chip pressed={grouped} onClick={() => updateRiskQueue({ grouped: !grouped })}>
          {grouped ? "Related alerts grouped" : "Group related alerts"}
        </Chip>
      </div>
      <span className="text-xs text-muted">
        {rows.length} of {alerts.length} seeded alerts shown ({OPEN_ALERT_TOTAL} open in total)
      </span>
      <ul className="flex flex-col gap-2">
        {rows.map((a) => {
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
                  <b className="font-mono">{a.id}</b>
                  <Pill tone={a.bandTone}>{a.bandLabel}</Pill>
                </span>
                <span className="text-[13.5px] font-semibold">{a.typology}</span>
                <span className="text-xs text-muted">
                  {formatMoney(a.amount)} · {a.accounts} accounts · {a.age} old · score {a.score.toFixed(1)}
                </span>
                <span className="flex flex-wrap gap-1.5">
                  <Pill tone={a.dataQualityTone}>{a.dataQuality}</Pill>
                  {grouped && a.cluster ? <Pill tone="info">Cluster {a.cluster}</Pill> : null}
                  <Pill tone="mute">{a.status}</Pill>
                </span>
              </Link>
            </li>
          );
        })}
      </ul>
      {rows.length === 0 ? (
        <EmptyState
          title="No alerts match."
          description="Clear filters to see the full queue."
          action={
            <Button variant="ghost" size="sm" onClick={clear}>
              Clear filters
            </Button>
          }
        />
      ) : null}
    </Card>
  );
}
