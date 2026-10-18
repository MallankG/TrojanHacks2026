import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

interface KeyValueListProps {
  items: Array<[ReactNode, ReactNode]>;
  className?: string;
  mono?: boolean;
}

export function KeyValueList({ items, className, mono }: KeyValueListProps) {
  return (
    <dl className={cn("grid grid-cols-[minmax(120px,38%)_1fr] gap-x-3.5 gap-y-1.5 text-[13.5px]", mono && "font-mono text-xs", className)}>
      {items.map(([key, value], index) => (
        <div key={index} className="contents">
          <dt className="text-muted">{key}</dt>
          <dd className="break-words font-medium">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function StatBox({ label, value, className }: { label: ReactNode; value: ReactNode; className?: string }) {
  return (
    <div className={cn("rounded-[10px] bg-subtle px-3 py-2.5", className)}>
      <div className="text-xs text-muted">{label}</div>
      <div className="text-[17px] font-bold">{value}</div>
    </div>
  );
}
