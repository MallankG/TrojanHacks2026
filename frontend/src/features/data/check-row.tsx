import type { ContractCheck } from "@/lib/types";
import { Pill } from "@/components/ui/pill";

export function CheckRow({ check }: { check: ContractCheck }) {
  return (
    <div className="flex justify-between gap-2.5 border-t border-line-soft pt-2">
      <div className="min-w-0">
        <div className="text-[13.5px] font-semibold">{check.label}</div>
        <div className="text-xs text-muted">{check.note}</div>
      </div>
      <div className="flex items-center gap-1.5">
        <span className="text-xs text-muted">{check.count}</span>
        <Pill tone={check.tone}>{check.result}</Pill>
      </div>
    </div>
  );
}
