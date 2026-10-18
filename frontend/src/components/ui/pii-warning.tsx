"use client";

import { describePii } from "@/lib/privacy/pii";
import type { HeldText } from "@/lib/privacy/use-pii-guard";
import { Button } from "./button";
import { Callout } from "./callout";

/**
 * Shown when free text about to leave the browser (a copilot question, a collaborator note)
 * contains personal data. The raw text is never sent: the user edits it or sends the redacted copy.
 * `sendBlocked` explains why the redacted copy cannot be sent (too short once redacted, sending unavailable).
 */
export function PiiWarning({ held, sendBlocked, onSendRedacted, onEdit }: { held: HeldText; sendBlocked?: string | null; onSendRedacted: () => void; onEdit: () => void }) {
  return (
    <Callout
      tone="warn"
      role="alert"
      title="Personal data detected."
      action={
        <>
          <Button size="sm" disabled={Boolean(sendBlocked)} onClick={onSendRedacted}>
            Send redacted
          </Button>
          <Button size="sm" variant="ghost" onClick={onEdit}>
            Edit
          </Button>
        </>
      }
    >
      {`This text contains ${describePii(held.found)}. Investigations reference accounts and cases, not personal details. Sending redacted replaces each value with a placeholder such as [EMAIL].${sendBlocked ? ` ${sendBlocked}` : ""}`}
    </Callout>
  );
}
