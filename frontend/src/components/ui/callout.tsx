import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import type { Tone } from "@/lib/types";
import { piiFiltered } from "./pii-filter";
import { TONE_CLASSES } from "./tone";

interface CalloutProps {
  tone?: Tone;
  title?: ReactNode;
  children?: ReactNode;
  className?: string;
  /** `alert` for errors that need attention now, `status` for confirmations. */
  role?: "alert" | "status" | "note";
  action?: ReactNode;
}

export function Callout({ tone = "info", title, children, className, role = "note", action }: CalloutProps) {
  return (
    <div role={role} className={cn("flex flex-col gap-2 rounded-[10px] px-3.5 py-2.5 text-[13.5px]", TONE_CLASSES[tone], tone === "bad" && "border border-danger-line", className)}>
      <div>
        {title ? <b>{piiFiltered(title)} </b> : null}
        {piiFiltered(children)}
      </div>
      {action ? <div className="flex flex-wrap gap-2">{action}</div> : null}
    </div>
  );
}
