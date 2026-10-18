"use client";

import { useCallback, useState } from "react";
import { findPii, redactText, type PiiKind } from "./pii";

export interface HeldText {
  text: string;
  found: Array<{ kind: PiiKind; count: number }>;
  /** The copy that may be sent instead: every PII value replaced with a placeholder. */
  redacted: string;
  /** Length of the redacted copy without placeholders, to tell whether anything meaningful is left. */
  substance: number;
}

/**
 * Holds back free text that contains PII before it leaves the browser. Scans only on submit, not
 * on every keystroke. `check` returns true when the text may be sent unchanged.
 */
export function usePiiGuard() {
  const [held, setHeld] = useState<HeldText | null>(null);
  const check = useCallback((text: string) => {
    const found = findPii(text);
    if (found.length === 0) {
      setHeld(null);
      return true;
    }
    const redacted = redactText(text).trim();
    setHeld({ text, found, redacted, substance: redacted.replace(/\[(EMAIL|SSN|CARD|IBAN|PHONE)\]/g, "").trim().length });
    return false;
  }, []);
  const clear = useCallback(() => setHeld(null), []);
  return { held, check, clear };
}
