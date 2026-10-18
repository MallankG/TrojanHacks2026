"use client";

import { caseStatusTone } from "@/lib/domain/case";
import { ROUTES } from "@/lib/routes";
import { caseById, PRIMARY_CASE } from "@/lib/seed/governance";
import { useDemoState } from "@/lib/store/hooks";
import type { CaseId, CaseStatus, Tone } from "@/lib/types";
import { Panel } from "@/components/ui/card";
import { TabNav } from "@/components/ui/controls";
import { Pill } from "@/components/ui/pill";

const FLOW = ["New", "In review", "Pending approval", "Decided"] as const;

function flowIndex(status: CaseStatus): number {
  if (status === "In review") return 1;
  if (status === "Pending approval") return 2;
  return 3;
}

/** Case title, status lifecycle and, for the working case, the section tabs. */
export function CaseHeader({ caseId }: { caseId: CaseId }) {
  const liveStatus = useDemoState().caseWork.status;
  const item = caseById(caseId);
  const isPrimary = caseId === PRIMARY_CASE;
  const status = isPrimary ? liveStatus : (item.status ?? "In review");
  const current = flowIndex(status);

  return (
    <>
      <Panel>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="eyebrow">
              {item.id} · alerts {item.alerts}
            </div>
            <h2 className="mt-1 text-[22px] font-semibold">{item.title}</h2>
            <div className="mt-2 flex flex-wrap gap-1.5">
              <Pill tone={caseStatusTone(status)}>{status}</Pill>
              <Pill tone="mute">Owner {item.owner}</Pill>
            </div>
          </div>
          {isPrimary ? (
            <ol aria-label="Case status" className="flex flex-wrap gap-1.5">
              {FLOW.map((label, i) => {
                const tone: Tone = i < current ? "ok" : i === current ? "dark" : "mute";
                return (
                  <li key={label} aria-current={i === current ? "step" : undefined}>
                    <Pill tone={tone}>{label}</Pill>
                  </li>
                );
              })}
            </ol>
          ) : null}
        </div>
        {!isPrimary ? (
          <>
            <p className="text-sm">{item.summary}</p>
            <span className="text-xs text-muted">This sample case is read-only. Open CASE-0412 to walk the full workflow.</span>
          </>
        ) : null}
      </Panel>
      {isPrimary ? (
        <TabNav
          label="Case sections"
          tabs={[
            { href: ROUTES.case(caseId), label: "Overview" },
            { href: ROUTES.case(caseId, "checklist"), label: "Evidence checklist" },
            { href: ROUTES.case(caseId, "narrative"), label: "Narrative" },
            { href: ROUTES.case(caseId, "disposition"), label: "Disposition" },
            { href: ROUTES.case(caseId, "report"), label: "Report builder" },
            { href: ROUTES.governance("enterprise"), label: "Collaboration" },
          ]}
        />
      ) : null}
    </>
  );
}
