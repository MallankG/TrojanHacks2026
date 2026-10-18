"use client";

import { liveApi, useLive } from "@/lib/live/api";
import { shortId, utc, words } from "@/lib/live/format";
import { CardHeader, Panel } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/misc";
import { Pill } from "@/components/ui/pill";
import { DataTable, Td, Th, Tr } from "@/components/ui/table";
import { LiveData } from "./live-state";

/** Append-only audit events from the database, written by triggers and the backend. */
export function LiveAudit({ objectType, objectId, title = "Audit trail" }: { objectType?: string; objectId?: string; title?: string }) {
  const events = useLive(() => liveApi.audit({ object_type: objectType, object_id: objectId, limit: 100 }), `audit:${objectType}:${objectId}`);
  return (
    <Panel>
      <CardHeader title={title} description="Newest first. Actors are the signed-in user, an agent or the system." />
      <LiveData query={events} label="audit events">
        {(page) =>
          page.items.length === 0 ? (
            <EmptyState title="No audit events yet." />
          ) : (
            <DataTable caption={title}>
              <thead>
                <tr>
                  <Th>#</Th>
                  <Th>When</Th>
                  <Th>Actor</Th>
                  <Th>Action</Th>
                  <Th>Object</Th>
                  <Th>Request</Th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((e) => (
                  <Tr key={e.id}>
                    <Td className="font-mono text-xs">{e.event_id}</Td>
                    <Td className="whitespace-nowrap text-xs">{utc(e.occurred_at)}</Td>
                    <Td>
                      <Pill tone={e.actor_type === "user" ? "info" : e.actor_type === "agent" ? "blue" : "mute"}>{words(e.actor_type)}</Pill>
                      <span className="ml-1.5 font-mono text-xs text-muted">{shortId(e.actor_id)}</span>
                    </Td>
                    <Td className="font-mono text-xs">{e.action}</Td>
                    <Td className="text-xs">
                      {words(e.object_type)} <span className="font-mono text-muted">{shortId(e.object_id)}</span>
                    </Td>
                    <Td className="font-mono text-xs text-muted">{shortId(e.request_id)}</Td>
                  </Tr>
                ))}
              </tbody>
            </DataTable>
          )
        }
      </LiveData>
    </Panel>
  );
}

