import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export function Skeleton({ lines = 3, label = "Loading" }: { lines?: number; label?: string }) {
  const widths = ["w-[92%]", "w-[74%]", "w-[84%]", "w-[66%]"];
  return (
    <div role="status" aria-label={label} className="flex flex-col gap-2.5">
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className={cn("h-3 animate-pulse rounded-md bg-[#e6ebf1]", widths[i % widths.length])} />
      ))}
    </div>
  );
}

export function EmptyState({ title, description, action }: { title: string; description?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-1 px-6 py-8 text-center">
      <b>{title}</b>
      {description ? <p className="text-[13.5px] text-muted">{description}</p> : null}
      {action ? <div className="mt-2.5">{action}</div> : null}
    </div>
  );
}

/** Horizontal meter; the value is always printed next to it. */
export function Meter({ value, color = "var(--color-brand)", className, label }: { value: number; color?: string; className?: string; label: string }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <div role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} className={cn("h-2 flex-1 overflow-hidden rounded bg-line-soft", className)}>
      <div className="h-full rounded" style={{ width: `${pct}%`, background: color }} />
    </div>
  );
}

export function Sparkline({ values, className }: { values: number[]; className?: string }) {
  const max = Math.max(...values);
  const min = Math.min(...values);
  const range = max - min || 1;
  const d = values
    .map((v, i) => `${i === 0 ? "M" : "L"}${((i * 120) / Math.max(values.length - 1, 1)).toFixed(1)},${(28 - ((v - min) / range) * 26).toFixed(1)}`)
    .join(" ");
  return (
    <svg viewBox="0 0 120 30" className={cn("h-[30px] w-full", className)} preserveAspectRatio="none" aria-hidden="true">
      <path d={d} fill="none" stroke="var(--color-brand)" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function Legend({ items }: { items: Array<{ label: string; swatch: ReactNode }> }) {
  return (
    <div className="flex flex-wrap gap-4 text-[12.5px] font-semibold">
      {items.map((item) => (
        <span key={item.label} className="inline-flex items-center gap-1.5">
          {item.swatch}
          {item.label}
        </span>
      ))}
    </div>
  );
}

export function Swatch({ color }: { color: string }) {
  return <span aria-hidden="true" className="inline-block size-2.5 rounded-sm" style={{ background: color }} />;
}

export function CodeBlock({ children }: { children: ReactNode }) {
  return <pre className="whitespace-pre-wrap break-words rounded-[10px] bg-ink px-3.5 py-3 font-mono text-xs text-[#d7e3f1]">{children}</pre>;
}
