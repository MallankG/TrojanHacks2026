"use client";

import { useMemo, useState } from "react";
import { useLiveAuditEvents } from "@/lib/live/adapters";
import { exportAuditTrail } from "@/lib/store/actions";
import { useDemoState } from "@/lib/store/hooks";
import type { AuditType } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Chip } from "@/components/ui/controls";
import { EmptyState } from "@/components/ui/misc";
import { Pill } from "@/components/ui/pill";
import { DataTable, Td, Th } from "@/components/ui/table";

type Filter = "all" | AuditType;

const FILTERS: Filter[] = ["all", "Data", "Model", "Alert", "AI", "Case", "Graph", "Access", "Export", "Policy", "Entity", "Screening"];

export function AuditTrail() {
  const seeded = useDemoState().events;
  // Live backend events first (real, append-only log), then the events from the seeded sample flows.
  const live = useLiveAuditEvents();
  const events = useMemo(() => [...(live.data ?? []), ...seeded], [live.data, seeded]);
  const [filter, setFilter] = useState<Filter>("all");
  const rows = events.filter((event) => filter === "all" || event.type === filter);

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2.5">
        <div role="group" aria-label="Event type" className="flex flex-wrap gap-1.5">
          {FILTERS.map((f) => (
            <Chip key={f} pressed={filter === f} onClick={() => setFilter(f)}>
              {f === "all" ? "All" : f}
            </Chip>
          ))}
        </div>
        <Button variant="ghost" size="sm" onClick={exportAuditTrail}>
          Export CSV
        </Button>
      </div>
      <Card className="overflow-hidden">
        <p className="px-5 py-3.5 text-xs text-muted">
          {rows.length} events · append-only style · actor, action, object, time, request ID and payload hash
          {live.live ? ` · ${live.loading ? "loading live events" : live.error ? `live events unavailable: ${live.error}` : `${live.data?.length ?? 0} live from the backend, the rest from sample flows`}` : ""}
        </p>
        {rows.length === 0 ? (
          <EmptyState title="No events of this type yet." action={<Button variant="ghost" size="sm" onClick={() => setFilter("all")}>Show all events</Button>} />
        ) : (
          <DataTable caption="Audit events">
            <thead>
              <tr>
                <Th>Time</Th>
                <Th>Actor</Th>
                <Th>Type</Th>
                <Th>Action</Th>
                <Th>Object</Th>
                <Th>Request</Th>
                <Th>Hash</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((event, index) => (
                <tr key={`${event.live ? "live" : "seed"}-${event.requestId}-${index}`}>
                  <Td className="whitespace-nowrap text-xs text-muted">{event.at}</Td>
                  <Td className="whitespace-nowrap">{event.actor}</Td>
                  <Td>
                    <span className="inline-flex gap-1">
                      <Pill tone="info">{event.type}</Pill>
                      {live.live ? <Pill tone={event.live ? "ok" : "mute"}>{event.live ? "Live" : "Sample"}</Pill> : null}
                    </span>
                  </Td>
                  <Td>{event.action}</Td>
                  <Td className="font-mono">{event.object}</Td>
                  <Td className="font-mono">{event.requestId}</Td>
                  <Td className="font-mono text-xs text-muted">{event.hash}</Td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        )}
      </Card>
    </>
  );
}
