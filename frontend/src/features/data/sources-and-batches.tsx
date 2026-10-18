"use client";

import Link from "next/link";
import { useMemo } from "react";
import { ROUTES } from "@/lib/routes";
import { batchChecks, BLAST_RADIUS, HELD_BATCH, seededBatches, sourceFeeds } from "@/lib/seed/data-ops";
import { addCheckFeedToScope, openModal, selectBatch } from "@/lib/store/actions";
import { useDemoState, useRole } from "@/lib/store/hooks";
import { Button, buttonClasses } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Card, Panel } from "@/components/ui/card";
import { KeyValueList } from "@/components/ui/key-value";
import { Pill } from "@/components/ui/pill";
import { DataTable, Td, Th, Tr } from "@/components/ui/table";
import { CheckRow } from "./check-row";

export function SourcesAndBatches() {
  const state = useDemoState();
  const role = useRole();
  const { driftFixed: fixed, checkScope, extraBatches, selectedBatch } = state.data;

  const sources = useMemo(() => sourceFeeds(fixed, checkScope), [fixed, checkScope]);
  const batches = useMemo(() => [...extraBatches, ...seededBatches(fixed)], [extraBatches, fixed]);
  const selected = batches.find((b) => b.name === selectedBatch) ?? batches[0];
  const checks = batchChecks(selected, fixed);
  const isHeld = selected.name === HELD_BATCH;

  return (
    <>
      <section aria-label="Sources" className="grid grid-cols-[repeat(auto-fit,minmax(260px,1fr))] gap-4">
        {sources.map((source) => (
          <Card key={source.name} className="flex flex-col gap-2 px-4 py-3.5">
            <div className="flex items-start justify-between gap-2">
              <b>{source.name}</b>
              <Pill tone={source.tone}>{source.status}</Pill>
            </div>
            <span className="text-xs text-muted">Owner: {source.owner}</span>
            <span className="text-xs text-muted">
              Contract {source.contract} · {source.freshness} · {source.sla}
            </span>
            <span className="text-[12.5px]">{source.trust}</span>
            {source.gap ? (
              <Button variant="ghost" size="sm" className="self-start" disabled={!role.canUpload} onClick={addCheckFeedToScope}>
                Add to monitoring scope
              </Button>
            ) : null}
          </Card>
        ))}
      </section>

      <div className="flex flex-wrap items-start gap-4">
        <Card aria-label="Batches" className="min-w-0 flex-[2_1_560px] overflow-hidden">
          <div className="px-5 py-4">
            <h2 className="text-base font-semibold">Batches</h2>
          </div>
          <DataTable caption="Ingested batches">
            <thead>
              <tr>
                <Th>Batch</Th>
                <Th>Rail</Th>
                <Th>Rows</Th>
                <Th>Accepted</Th>
                <Th>Rejected</Th>
                <Th>Status</Th>
                <Th>Owner</Th>
              </tr>
            </thead>
            <tbody>
              {batches.map((batch) => (
                <Tr key={batch.name} selected={batch.name === selected.name}>
                  <Td>
                    <button type="button" className={buttonClasses("link", "md", "font-mono")} aria-pressed={batch.name === selected.name} onClick={() => selectBatch(batch.name)}>
                      {batch.name}
                    </button>
                  </Td>
                  <Td>{batch.rail}</Td>
                  <Td>{batch.rows}</Td>
                  <Td>{batch.accepted}</Td>
                  <Td>{batch.rejected}</Td>
                  <Td>
                    <Pill tone={batch.tone}>{batch.status}</Pill>
                  </Td>
                  <Td className="text-xs text-muted">{batch.owner}</Td>
                </Tr>
              ))}
            </tbody>
          </DataTable>
        </Card>

        <Panel aria-label="Batch detail" className="flex-[1_1_360px]">
          <div>
            <div className="eyebrow">Selected batch</div>
            <h2 className="mt-1 font-mono text-[15px] font-semibold">{selected.name}</h2>
            <span className="text-xs text-muted">
              {selected.schema} · file hash {selected.hash} · {selected.owner}
            </span>
          </div>
          {isHeld && !fixed ? (
            <Callout
              tone="warn"
              title="Schema drift: column renamed."
              action={
                <>
                  <Button size="sm" disabled={!role.canFixData} onClick={() => openModal({ kind: "mappingFix" })}>
                    Review mapping fix v4.1
                  </Button>
                  <Link href={ROUTES.dataQuality} className={buttonClasses("ghost", "sm")}>
                    See affected rows
                  </Link>
                </>
              }
            >
              The source renamed target_account to bene_acct_no. 1,206 rows have no target account, so the whole batch is held. This hides one hop of AL-1027.
              <KeyValueList items={BLAST_RADIUS} className="mt-2" />
            </Callout>
          ) : null}
          {isHeld && fixed ? (
            <Callout tone="ok" role="status" title="Loaded after mapping fix v4.1.">
              18,212 rows accepted, 28 rejected. AL-1027, AL-1029 and AL-1038 were re-scored and snapshot GS-0412-b was created.
            </Callout>
          ) : null}
          <h3 className="text-[13.5px] font-semibold">Contract checks</h3>
          {checks.map((check) => (
            <CheckRow key={check.label} check={check} />
          ))}
        </Panel>
      </div>
    </>
  );
}
