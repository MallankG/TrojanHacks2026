"use client";

import Link from "next/link";
import { cn } from "@/lib/cn";
import { caseStatusTone } from "@/lib/domain/case";
import { ROUTES } from "@/lib/routes";
import { CASES, PRIMARY_CASE } from "@/lib/seed/governance";
import { useDemoState } from "@/lib/store/hooks";
import type { CaseId } from "@/lib/types";
import { Pill } from "@/components/ui/pill";
import { LiveCaseEntries } from "@/features/live/live-cases";

/** Case navigator. Each entry is a link, so cases are deep-linkable and keyboard reachable. */
export function CaseList({ selectedId }: { selectedId: CaseId | string }) {
  const status = useDemoState().caseWork.status;
  return (
    <nav aria-label="Cases" className="flex w-full flex-col gap-2 rounded-[14px] border border-line bg-white p-3 lg:max-w-[340px] lg:flex-[1_1_280px]">
      {/* Live backend cases (nothing without a live session), then the seeded sample cases. */}
      <LiveCaseEntries selectedId={selectedId} />
      {CASES.map((item) => {
        const isPrimary = item.id === PRIMARY_CASE;
        const itemStatus = isPrimary ? status : (item.status ?? "In review");
        const selected = item.id === selectedId;
        return (
          <Link
            key={item.id}
            href={ROUTES.case(item.id)}
            aria-current={selected ? "page" : undefined}
            className={cn(
              "flex flex-col gap-1.5 rounded-xl border bg-white text-left transition-colors",
              selected ? "border-2 border-brand bg-[#f7fcfb] px-[13px] py-[11px]" : "border-line px-3.5 py-3 hover:border-[#9fb0c6]",
            )}
          >
            <span className="flex w-full justify-between gap-2">
              <b className="font-mono">{item.id}</b>
              <span className="text-xs text-muted">{item.amount}</span>
            </span>
            <span className="text-[13.5px] font-semibold">{item.title}</span>
            <Pill tone={caseStatusTone(itemStatus)} className="self-start">
              {itemStatus}
            </Pill>
            <span className="text-xs text-muted">
              Owner {item.owner} · {isPrimary ? "SLA due Oct 11, 13:44 UTC" : "Sample case, read-only"}
            </span>
          </Link>
        );
      })}
    </nav>
  );
}
