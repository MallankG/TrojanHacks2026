"use client";

import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { BLOCKED_CLAIM_TEXT, VERIFIED_TOTAL_TEXT, buildNarrative, claimTone } from "@/lib/domain/case";
import { applyVerifiedValue, commitRegeneratedDraft, openClaimEditor, regenerateBlocked, toast, toggleDiff } from "@/lib/store/actions";
import { useDemoState } from "@/lib/store/hooks";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Card, CardHeader, Panel } from "@/components/ui/card";
import { CiteList } from "@/components/ui/cite";
import { Skeleton } from "@/components/ui/misc";
import { Pill } from "@/components/ui/pill";
import { DataTable, Td, Th } from "@/components/ui/table";

const REDRAFT_MS = 1300;

export function NarrativeView() {
  const state = useDemoState();
  const sections = useMemo(() => buildNarrative(state), [state]);
  const [busy, setBusy] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const work = state.caseWork;
  const revision = `r${work.revisions[0].revision}`;
  const fixed = state.data.driftFixed;
  const blocked = !work.claimFixed;
  const redraftBlocked = regenerateBlocked(state);
  const showDiff = work.showDiff && work.claimFixed;

  // Activity hides this page on navigation; finish a pending redraft instead of leaving it stuck.
  useLayoutEffect(
    () => () => {
      if (timer.current) {
        clearTimeout(timer.current);
        timer.current = null;
        setBusy(false);
        commitRegeneratedDraft();
      }
    },
    [],
  );

  const regenerate = () => {
    if (redraftBlocked) {
      toast(redraftBlocked);
      return;
    }
    setBusy(true);
    timer.current = setTimeout(() => {
      timer.current = null;
      setBusy(false);
      commitRegeneratedDraft();
    }, REDRAFT_MS);
  };

  return (
    <>
      <Panel>
        <CardHeader
          title={`Narrative draft · revision ${revision}`}
          description="AI drafts text only. Each factual claim cites evidence and is verified before it can appear in a report."
          actions={
            <>
              {showDiff ? <Pill tone="info">Changed in {revision}</Pill> : null}
              <Button variant="ghost" size="sm" disabled={!work.claimFixed} onClick={toggleDiff}>
                {work.showDiff ? "Hide change" : "Show change"}
              </Button>
              <Button variant="ghost" size="sm" disabled={busy || redraftBlocked !== null} title={redraftBlocked ?? undefined} onClick={regenerate}>
                Regenerate draft
              </Button>
            </>
          }
        />
        {blocked ? (
          <Callout tone="bad" role="alert" title="1 claim blocked by the verifier.">
            It does not match the evidence and cannot appear in the report.
          </Callout>
        ) : !fixed ? (
          <Callout tone="warn" title="Claims are waiting for data.">
            Resolve the data hold so the full path can be verified.
          </Callout>
        ) : (
          <Callout tone="ok" role="status" title="All factual claims verified.">
            Hypotheses and suggestions are labeled as such.
          </Callout>
        )}
        {busy ? <Skeleton label="Redrafting narrative" /> : null}
        {showDiff ? (
          <div className="flex flex-col gap-1 rounded-xl border border-dashed border-[#9fb0c6] px-3.5 py-2.5 text-[13.5px]">
            <span className="text-xs text-muted">Change in section 1</span>
            <del className="text-danger-ink">{BLOCKED_CLAIM_TEXT}</del>
            <ins className="font-semibold text-brand-ink no-underline">{VERIFIED_TOTAL_TEXT}</ins>
          </div>
        ) : null}
        {sections.map((section) => (
          <section key={section.heading} aria-label={section.heading} className="flex flex-col gap-2 border-t border-line-soft pt-2.5">
            <h3 className="text-[13.5px] font-semibold">{section.heading}</h3>
            {section.claims.map((claim) => (
              <div key={claim.text} className="flex flex-col gap-1.5 rounded-[10px] border border-line-soft bg-[#fafbfc] px-3 py-2.5">
                <span className="text-sm">{claim.text}</span>
                <div className="flex flex-wrap items-center gap-1.5">
                  <Pill tone={claimTone(claim.status)}>{claim.status}</Pill>
                  <CiteList ids={claim.cites} />
                </div>
                {claim.status === "Blocked" ? (
                  <div className="flex flex-wrap gap-2">
                    <Button size="sm" onClick={applyVerifiedValue}>
                      Use verified value
                    </Button>
                    <Button variant="ghost" size="sm" onClick={openClaimEditor}>
                      Edit and re-verify
                    </Button>
                  </div>
                ) : null}
              </div>
            ))}
          </section>
        ))}
      </Panel>
      <Card className="overflow-hidden">
        <div className="px-5 py-4">
          <CardHeader title="Revision history" description="Every revision keeps its verifier result. Old revisions are never overwritten." />
        </div>
        <DataTable caption="Narrative revisions">
          <thead>
            <tr>
              <Th>Revision</Th>
              <Th>By</Th>
              <Th>Verifier result</Th>
            </tr>
          </thead>
          <tbody>
            {work.revisions.map((rev) => (
              <tr key={rev.revision}>
                <Td className="font-mono">r{rev.revision}</Td>
                <Td>{rev.by}</Td>
                <Td>{rev.note}</Td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      </Card>
    </>
  );
}
