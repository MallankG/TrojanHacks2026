"use client";

import type { ComponentProps } from "react";
import { cn } from "@/lib/cn";
import { openEvidence } from "@/lib/store/actions";

interface CiteChipProps extends Omit<ComponentProps<"button">, "type" | "children"> {
  id: string;
  pressed?: boolean;
}

/** A citation to a piece of evidence. Defaults to opening the evidence viewer. */
export function CiteChip({ id, pressed, onClick, className, ...props }: CiteChipProps) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      aria-label={`Open evidence ${id}`}
      onClick={onClick ?? (() => openEvidence(id))}
      className={cn(
        "my-0.5 mr-1 inline-flex min-h-[26px] items-center rounded-md border px-2 font-mono text-[11.5px] font-medium transition-colors",
        pressed ? "border-select bg-select text-white" : "border-select-line bg-select-soft text-select-ink hover:bg-[#e1ecfa]",
        className,
      )}
      {...props}
    >
      {id}
    </button>
  );
}

export function CiteList({ ids, className }: { ids: string[]; className?: string }) {
  if (ids.length === 0) return null;
  return (
    <span className={cn("inline-flex flex-wrap", className)}>
      {ids.map((id) => (
        <CiteChip key={id} id={id} />
      ))}
    </span>
  );
}
