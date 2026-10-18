"use client";

import { describePii, type PiiKind } from "@/lib/privacy/pii";
import { Button } from "./button";
import { Callout } from "./callout";

/**
 * Shown when free text about to leave the browser (a copilot question, a collaborator note)
 * contains personal data. The raw text is never sent: the user edits it or sends the redacted copy.
 */
export function PiiWarning({ found, onSendRedacted, onEdit }: { found: Array<{ kind: PiiKind; count: number }>; onSendRedacted: () => void; onEdit: () => void }) {
  return (
    <Callout
      tone="warn"
      role="alert"
      title="Personal data detected."
      action={
        <>
          <Button size="sm" onClick={onSendRedacted}>
            Send redacted
          </Button>
          <Button size="sm" variant="ghost" onClick={onEdit}>
            Edit
          </Button>
        </>
      }
    >
      {`This text contains ${describePii(found)}. Investigations reference accounts and cases, not personal details. Sending redacted replaces each value with a placeholder such as [EMAIL].`}
    </Callout>
  );
}
