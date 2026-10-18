import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import type { Tone } from "@/lib/types";
import { TONE_CLASSES } from "./tone";

interface PillProps {
  tone?: Tone;
  children: ReactNode;
  className?: string;
  mono?: boolean;
}

/** Status label. Always put the status in words; tone only supports it. */
export function Pill({ tone = "mute", children, className, mono }: PillProps) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-semibold leading-normal", TONE_CLASSES[tone], mono && "font-mono font-medium", className)}>
      {children}
    </span>
  );
}
