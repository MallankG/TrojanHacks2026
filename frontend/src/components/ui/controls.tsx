"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ComponentProps, ReactNode } from "react";
import { cn } from "@/lib/cn";

interface ChipProps extends Omit<ComponentProps<"button">, "type"> {
  pressed?: boolean;
  mono?: boolean;
}

/** Toggle chip for filters. Pressed state is exposed with `aria-pressed`. */
export function Chip({ pressed, mono, className, ...props }: ChipProps) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      className={cn(
        "inline-flex min-h-9 items-center gap-1.5 rounded-full border px-3 text-[13px] font-semibold transition-colors disabled:opacity-55",
        pressed ? "border-ink bg-ink text-white" : "border-line-strong bg-white text-ink-2 hover:border-[#8a97ab]",
        mono && "font-mono",
        className,
      )}
      {...props}
    />
  );
}

export interface SegmentOption<T extends string | number> {
  value: T;
  label: ReactNode;
}

interface SegmentedProps<T extends string | number> {
  label: string;
  value: T;
  options: Array<SegmentOption<T>>;
  onChange: (value: T) => void;
  disabled?: boolean;
}

export function Segmented<T extends string | number>({ label, value, options, onChange, disabled }: SegmentedProps<T>) {
  return (
    <div role="group" aria-label={label} className="inline-flex overflow-hidden rounded-[10px] border border-line-strong bg-white">
      {options.map((option) => (
        <button
          key={String(option.value)}
          type="button"
          aria-pressed={option.value === value}
          disabled={disabled}
          onClick={() => onChange(option.value)}
          className={cn(
            "min-h-9 border-r border-line-strong px-3 text-[13px] font-semibold last:border-r-0 disabled:text-[#8a97ab]",
            option.value === value ? "bg-ink text-white" : "bg-white text-ink-2 hover:bg-canvas",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export interface TabLink {
  href: string;
  label: ReactNode;
  /** Exact match by default; set `prefix` to also match nested paths. */
  prefix?: boolean;
  /** Marks the tab active for any path starting with this value. */
  matchPrefix?: string;
}

/** Route-backed tabs: each tab is a page, so state survives refresh and deep links work. */
export function TabNav({ label, tabs }: { label: string; tabs: TabLink[] }) {
  const pathname = usePathname();
  return (
    <nav aria-label={label} className="flex gap-0.5 overflow-x-auto border-b border-line">
      {tabs.map((tab) => {
        const active =
          pathname === tab.href ||
          (tab.prefix === true && pathname.startsWith(`${tab.href}/`)) ||
          (tab.matchPrefix !== undefined && pathname.startsWith(tab.matchPrefix));
        return (
          <Link
            key={tab.href}
            href={tab.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "inline-flex min-h-11 items-center whitespace-nowrap border-b-[3px] px-3.5 text-sm font-semibold transition-colors",
              active ? "border-brand text-ink" : "border-transparent text-muted hover:text-ink",
            )}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}

interface OptionCardProps extends Omit<ComponentProps<"button">, "type"> {
  pressed?: boolean;
}

/** Large selectable row used for choices and list selection. */
export function OptionCard({ pressed, className, ...props }: OptionCardProps) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      className={cn(
        "flex w-full items-center justify-between gap-3 rounded-xl border bg-white text-left text-[13.5px] transition-colors disabled:cursor-not-allowed disabled:opacity-70",
        pressed ? "border-2 border-brand bg-brand-soft px-[13px] py-[11px]" : "border-[#d5dce6] px-3.5 py-3 hover:border-[#9fb0c6]",
        className,
      )}
      {...props}
    />
  );
}
