import { ROUTES } from "@/lib/routes";
import type { DemoState } from "@/lib/store/state";
import type { CaseStatus, DispositionChoice, Tone } from "@/lib/types";

export type ClaimStatus = "Verified" | "Blocked" | "Waiting for data" | "Hypothesis" | "Policy context" | "Suggestion";

export interface NarrativeClaim {
  text: string;
  cites: string[];
  status: ClaimStatus;
}

export interface NarrativeSection {
  heading: string;
  claims: NarrativeClaim[];
}

export const BLOCKED_CLAIM_TEXT = "Total moved along the path was $158,200.00.";
export const VERIFIED_TOTAL_TEXT =
  "$58,200.00 entered the path; $46,800.00 left by cross-border wire, $9,600.00 went to ACC-3391 and $1,800.00 stayed in intermediary accounts.";

export function claimTone(status: ClaimStatus): Tone {
  switch (status) {
    case "Verified":
      return "ok";
    case "Blocked":
      return "bad";
    case "Waiting for data":
      return "warn";
    default:
      return "info";
  }
}

export function isCaseVerified(state: DemoState): boolean {
  return state.data.driftFixed && state.caseWork.claimFixed;
}

export function buildNarrative(state: DemoState): NarrativeSection[] {
  const fixed = state.data.driftFixed;
  const claim = (text: string, cites: string[], status: ClaimStatus): NarrativeClaim => ({ text, cites, status });
  const total = state.caseWork.claimFixed
    ? claim(VERIFIED_TOTAL_TEXT, ["TX-88201", "TX-88266", "TX-88222"], fixed ? "Verified" : "Waiting for data")
    : claim(BLOCKED_CLAIM_TEXT, ["TX-88201", "TX-88266"], "Blocked");
  return [
    {
      heading: "1 Observed facts",
      claims: [
        fixed
          ? claim("Between Oct 6 09:14 and Oct 7 10:20 UTC, funds moved from ACC-1104 through four accounts in five transfers.", ["TX-88201", "TX-88266"], "Verified")
          : claim("Between Oct 6 and Oct 7, funds moved from ACC-1104 through at least two accounts. Later hops are waiting for data.", ["TX-88201", "DQ-1042"], "Waiting for data"),
        total,
        claim("ACC-2208 was opened 21 days before the first transfer.", ["ACC-2208"], "Verified"),
      ],
    },
    {
      heading: "2 Model and rule inferences",
      claims: [
        claim(`xgb-aml v1.4.2 scored ${fixed ? "0.91" : "0.71"} against a 0.62 threshold.`, ["MR-2291"], "Verified"),
        claim(`Rules fired: rapid pass-through, new beneficiary${fixed ? ", layering." : "."}`, ["DF-1027"], "Verified"),
      ],
    },
    {
      heading: "3 Analyst hypotheses",
      claims: [
        claim("Funds may have been layered through newly opened accounts before leaving the bank.", [], "Hypothesis"),
        claim("Alternatively, ACC-1104 may have been tricked into paying a new supplier (scam proceeds).", [], "Hypothesis"),
      ],
    },
    {
      heading: "4 Contradictory evidence",
      claims: [
        claim("ACC-1104 has paid suppliers weekly for 26 months.", ["ACC-1104"], "Verified"),
        claim("Shared devices can indicate a household rather than coordination.", ["DV-77"], "Verified"),
      ],
    },
    {
      heading: "5 Data gaps",
      claims: [
        fixed
          ? claim("Resolved: the ACC-3315 to ACC-4471 transfer was reloaded after mapping fix v4.1.", ["DQ-1042"], "Verified")
          : claim("The ACC-3315 to ACC-4471 transfer is in a quarantined batch.", ["DQ-1042"], "Verified"),
        claim("Beneficiary details for the cross-border wire are synthetic metadata only.", ["TX-88266"], "Verified"),
      ],
    },
    {
      heading: "6 Relevant policy",
      claims: [
        claim("FFIEC BSA/AML Examination Manual, Suspicious Activity Reporting.", ["POL-FFIEC-SAR"], "Policy context"),
        claim("FinCEN public guidance on funds-movement red flags.", ["POL-FINCEN-ADV"], "Policy context"),
      ],
    },
    {
      heading: "7 Recommended next steps",
      claims: [
        claim("Request beneficiary details for the cross-border wire through the correspondent.", ["TX-88266"], "Suggestion"),
        claim("Review linked alert AL-1029 for the shared-device account.", ["AL-1029"], "Suggestion"),
      ],
    },
  ];
}

/**
 * Re-verifies an analyst's edit of the blocked claim. Returns an error message, or null when it passes.
 */
export function verifyClaimEdit(text: string): string | null {
  const t = text.trim();
  if (t.length < 10) return "Write a full sentence.";
  if (!t.includes("TX-")) return "Still unsupported. Cite an evidence ID such as TX-88201.";
  if (t.includes("158,200") || t.includes("158200")) return "Still unsupported: $158,200.00 does not match evidence. $58,200.00 entered the path (TX-88201).";
  return null;
}

export interface ChecklistItem {
  label: string;
  done: boolean;
  note: string;
  href?: string;
}

export function buildChecklist(state: DemoState): ChecklistItem[] {
  const fixed = state.data.driftFixed;
  const verified = isCaseVerified(state);
  const decided = state.caseWork.status !== "In review";
  return [
    { label: "Source rows reviewed", done: state.caseWork.seenSource, note: "Open any transaction citation to view its source row", href: ROUTES.graph },
    { label: "Data gaps resolved or documented", done: fixed, note: fixed ? "Hop C to D restored by mapping fix v4.1" : "Hop C to D is in a quarantined batch", href: ROUTES.data },
    { label: "Graph evidence pinned and saved", done: state.graph.pinSaved, note: "Save the evidence subgraph in the graph workbench", href: ROUTES.graph },
    { label: "Model and rule outputs captured", done: true, note: "SR-2291 and DF-1027 attached automatically" },
    { label: "Policy reference cited", done: true, note: "FFIEC and FinCEN passages in narrative section 6" },
    { label: "Counter-evidence reviewed", done: state.caseWork.counterReviewed, note: "Ask the copilot for benign explanations, or tick when done", href: ROUTES.copilot },
    { label: "Narrative verified", done: verified, note: verified ? "All factual claims match evidence" : "At least one claim is blocked or waiting for data", href: ROUTES.case("CASE-0412", "narrative") },
    { label: "Disposition recorded", done: decided, note: decided ? `Status: ${state.caseWork.status}` : "Choose a disposition with a rationale", href: ROUTES.case("CASE-0412", "disposition") },
  ];
}

export const DISPOSITION_OPTIONS: Array<{ key: DispositionChoice; label: string; description: string }> = [
  { key: "escalate", label: "Escalate for SAR-support review", description: "Needs a verified narrative and a BSA officer approval." },
  { key: "close", label: "Close, no further action", description: "Records a benign or insufficient-evidence outcome." },
  { key: "more", label: "Request more information", description: "Pauses the case and assigns a data request." },
];

export const MIN_RATIONALE = 20;

export function caseStatusTone(status: CaseStatus): Tone {
  switch (status) {
    case "In review":
      return "info";
    case "Pending approval":
    case "Awaiting information":
      return "warn";
    case "Escalated for SAR-support review":
      return "blue";
    default:
      return "mute";
  }
}

export interface BundleComponent {
  label: string;
  note: string;
  ready: boolean;
}

export function bundleComponents(state: DemoState): BundleComponent[] {
  const fixed = state.data.driftFixed;
  const verified = isCaseVerified(state);
  const revision = state.caseWork.revisions[0].revision;
  return [
    { label: "Narrative draft", note: verified ? `Ready, revision r${revision}` : "Blocked claim or waiting for data", ready: verified },
    { label: "Transaction schedule", note: fixed ? "7 transactions with source rows" : "6 of 7 transactions (1 on hold)", ready: fixed },
    { label: "Related entity table", note: "8 accounts and tokens, tokenized IDs", ready: true },
    { label: "Graph snapshot", note: state.graph.pinSaved ? `${fixed ? "GS-0412-b" : "GS-0412-a"} with pinned subgraph` : "Not saved yet", ready: state.graph.pinSaved },
    { label: "Source lineage", note: "11-step chain per transaction", ready: true },
    { label: "Detector and model outputs", note: "SR-2291, DF-1027, feature contributions", ready: true },
    { label: "Policy references", note: "2 passages with chunk IDs", ready: true },
    { label: "Discrepancy log", note: "Data-quality issues and repairs", ready: true },
    { label: "Analyst notes", note: `${state.caseWork.notes.length} notes`, ready: true },
    { label: "Audit timeline", note: `${state.events.length} events`, ready: true },
  ];
}

export function bundleBlockers(state: DemoState, readOnly: boolean): string[] {
  const blockers: string[] = [];
  const f = state.caseWork.report.formats;
  if (!isCaseVerified(state)) blockers.push("Verify the narrative (no blocked claims, data hold resolved).");
  if (state.caseWork.status === "In review") blockers.push("Record a disposition.");
  if (!state.graph.pinSaved) blockers.push("Save the evidence subgraph in the graph workbench.");
  if (readOnly) blockers.push("Auditors can download existing bundles but not create new ones.");
  if (!f.json && !f.csv && !f.pdf) blockers.push("Choose at least one format.");
  return blockers;
}
