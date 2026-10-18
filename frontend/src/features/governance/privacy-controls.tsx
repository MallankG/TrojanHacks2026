"use client";

import { REVEAL_MS, setMasked, usePrivacy } from "@/lib/privacy/store";
import { SENSITIVE_FIELDS } from "@/lib/privacy/pii";
import { useLiveProfile } from "@/lib/live/session";
import { Button } from "@/components/ui/button";
import { CardHeader, Panel } from "@/components/ui/card";
import { Pill } from "@/components/ui/pill";
import { useCanReveal } from "@/components/ui/sensitive";

/** What the UI does with personal data, with each control's live state. */
export function PrivacyControls() {
  const { masked, revealed } = usePrivacy();
  const canReveal = useCanReveal();
  const live = useLiveProfile() !== null;
  const controls: Array<[string, string, boolean]> = [
    ["Account identifiers masked by default", masked ? `On. ${revealed.size} value${revealed.size === 1 ? "" : "s"} revealed right now; each reveal ends after ${REVEAL_MS / 1000} seconds.` : "Off for this page session. Reloading or signing in masks again.", masked],
    ["Reveal limited by role", canReveal ? "Your role can reveal single values." : "Your role sees masked values only.", true],
    ["Free text filtered for PII", "Emails, SSNs, card numbers (Luhn-checked), IBANs (checksum-validated) and phone numbers are replaced with placeholders wherever narratives, notes, memos and raw rows are shown. Digit-only values such as IDs are left alone.", true],
    ["Evidence keeps real identifiers", "Masking is display-only: paths and patterns pinned to a case are saved with the real account references.", true],
    ["Personal data held back before it leaves the browser", "Copilot questions and collaborator notes containing PII are not sent until the user edits them or sends a redacted copy.", true],
    ["Raw source rows masked", "Party names become initials, account columns are masked and contact columns are hidden.", true],
    ["Email never stored in the browser", live ? "Your session is shared across tabs so tokens rotate safely; the stored copy has no email." : "When signed in, the stored session has no email; it is held in memory only.", true],
    ["Error messages sanitized", "Database messages, which can echo submitted values, and long server faults are replaced with a generic message and a request ID; short messages are kept with PII removed.", true],
    ["No referrer leakage", "Referrer-Policy: no-referrer, so case and account URLs are never sent to other sites.", true],
  ];
  return (
    <Panel>
      <CardHeader
        title="Privacy and PII controls"
        description="Enforced in the browser on top of the backend's tenant isolation (row-level security), private storage and step-up authentication for exports."
        actions={
          <Button size="sm" variant="ghost" disabled={!canReveal} onClick={() => setMasked(!masked)}>
            {masked ? "Show identifiers" : "Mask identifiers"}
          </Button>
        }
      />
      <ul className="flex flex-col">
        {controls.map(([name, detail, on]) => (
          <li key={name} className="flex justify-between gap-3 border-t border-line-soft py-2 text-[13.5px]">
            <span>
              <b>{name}</b>
              <span className="block text-xs text-muted">{detail}</span>
            </span>
            <Pill tone={on ? "ok" : "warn"}>{on ? "Active" : "Paused"}</Pill>
          </li>
        ))}
      </ul>
      <p className="text-xs text-muted">
        Sensitive canonical fields: {Object.entries(SENSITIVE_FIELDS).map(([field, why]) => `${field} (${why.toLowerCase()})`).join(", ")}.
      </p>
    </Panel>
  );
}
