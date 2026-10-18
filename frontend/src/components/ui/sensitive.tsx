"use client";

import { cn } from "@/lib/cn";
import { maskRef, safeText } from "@/lib/privacy/pii";
import { conceal, reveal, usePrivacy } from "@/lib/privacy/store";
import { useRole } from "@/lib/store/hooks";
import { Icon } from "./icon";

/** Reveal is for roles with case access that can act on it; auditors and stewards see masked values only. */
export function useCanReveal(): boolean {
  const role = useRole();
  return role.canAccessCases && !role.readOnly;
}

/** An account reference as text: masked unless the global toggle is off or this value is revealed. Safe inside links. */
export function MaskedRef({ value, className }: { value: string | null | undefined; className?: string }) {
  const { masked, revealed } = usePrivacy();
  const shown = !value || !masked || revealed.has(value) ? (value ?? "Not recorded") : maskRef(value);
  return <span className={cn("font-mono", className)}>{shown}</span>;
}

/** An account reference with a reveal button for permitted roles. Do not place inside a link; use MaskedRef there. */
export function SensitiveRef({ value, className }: { value: string | null | undefined; className?: string }) {
  const { masked, revealed } = usePrivacy();
  const canReveal = useCanReveal();
  const isRevealed = !!value && revealed.has(value);
  return (
    <span className="inline-flex items-center gap-1">
      <MaskedRef value={value} className={className} />
      {value && masked && canReveal ? (
        <button
          type="button"
          className="inline-flex min-h-6 min-w-6 items-center justify-center rounded text-muted hover:bg-canvas hover:text-ink"
          aria-label={isRevealed ? "Hide account reference" : "Reveal account reference for 30 seconds"}
          title={isRevealed ? "Hide" : "Reveal for 30 seconds"}
          onClick={() => (isRevealed ? conceal(value) : reveal(value))}
        >
          <Icon name={isRevealed ? "eyeOff" : "eye"} size={14} />
        </button>
      ) : null}
    </span>
  );
}

/** Free text (memos, notes, narratives): PII always redacted; account references follow the mask toggle. */
export function SensitiveText({ value, className }: { value: string | null | undefined; className?: string }) {
  const { masked } = usePrivacy();
  return <span className={className}>{safeText(value, masked)}</span>;
}
