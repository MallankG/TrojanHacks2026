import { edgeFlags } from "@/lib/domain/graph";
import { formatMoney } from "@/lib/format";
import { NODES, edgeByKey } from "@/lib/seed/graph";
import type { EdgeKey, EvidenceItem } from "@/lib/types";

/**
 * Animated flow explanation (PRD v3.3 section 20.1). Turns the verified path into a storyboard, one
 * frame per hop. Every narrated sentence must cite evidence, and every dollar amount in it must equal
 * a cited transaction's amount or a declared derivation. Frames that fail are shown as blocked.
 */

export interface StoryClaim {
  text: string;
  cites: string[];
  /** Amounts computed from cited transactions, declared so the verifier can check them. */
  derived?: number[];
}

export type FrameKind = "hop" | "split" | "hold" | "lead" | "summary";

export interface StoryFrame {
  key: string;
  kind: FrameKind;
  title: string;
  /** Edges drawn on the canvas up to and including this frame. */
  edges: EdgeKey[];
  focus: EdgeKey | null;
  placeholder: EdgeKey | null;
  claims: StoryClaim[];
  signals: string[];
  caveat: StoryClaim;
  issues: string[];
}

const AMOUNT = /\$([\d,]+\.\d{2})/g;

function amountsIn(text: string): number[] {
  return Array.from(text.matchAll(AMOUNT), (m) => Number(m[1].replace(/,/g, "")));
}

/** Unresolved references and amounts that no cited transaction supports. Empty means verified. */
export function verifyClaim(claim: StoryClaim, registry: Record<string, EvidenceItem>): string[] {
  const issues: string[] = [];
  if (claim.cites.length === 0) issues.push(`No evidence cited: "${claim.text}"`);
  for (const id of claim.cites) if (!registry[id]) issues.push(`${id} is not in the evidence registry`);
  const supported = new Set<number>(claim.derived ?? []);
  for (const id of claim.cites) {
    const edge = registry[id]?.edgeKey;
    if (edge) supported.add(edgeByKey(edge).amount);
  }
  for (const amount of amountsIn(claim.text)) if (!supported.has(amount)) issues.push(`${formatMoney(amount)} does not match any cited transaction`);
  return issues;
}

function hopClaim(key: EdgeKey, lead: string): StoryClaim {
  const e = edgeByKey(key);
  return { text: `${lead} ${formatMoney(e.amount)} moved from ${NODES[e.from].id} to ${NODES[e.to].id} by ${e.rail} at ${e.initiated} UTC.`, cites: [e.tx] };
}

export function buildStoryboard(fixed: boolean, registry: Record<string, EvidenceItem>): StoryFrame[] {
  const frames: Array<Omit<StoryFrame, "issues">> = [];
  const shown: EdgeKey[] = [];
  const hop = (key: EdgeKey, kind: FrameKind, title: string, claims: StoryClaim[], caveat: StoryClaim) => {
    shown.push(key);
    frames.push({ key, kind, title, edges: [...shown], focus: key, placeholder: null, claims, signals: edgeFlags(key, fixed), caveat });
  };

  hop("e1", "hop", "Funds enter the path", [hopClaim("e1", "Start:"), { text: "ACC-2208 had been open for 21 days.", cites: ["ACC-2208"] }], {
    text: "ACC-1104 pays suppliers weekly, so a business payment is possible.",
    cites: ["ACC-1104"],
  });
  hop("e2", "hop", "Rapid pass-through", [hopClaim("e2", "1 h 48 m later,"), { text: "ACC-3315 was a new counterparty for ACC-2208.", cites: ["ACC-3315", "DF-1027"] }], {
    text: "Balances mix inside an account; this shows where funds plausibly went, not a proven trace.",
    cites: ["GS-0412"],
  });
  hop("e7", "split", "Split to a device-linked account", [hopClaim("e7", "Separately,"), { text: "ACC-3391 shares device token DV-77 with ACC-2208.", cites: ["DV-77"] }], {
    text: "Households and shared phones also share devices.",
    cites: ["DV-77"],
  });

  if (!fixed) {
    frames.push({
      key: "hold",
      kind: "hold",
      title: "The path pauses at a data hold",
      edges: [...shown],
      focus: null,
      placeholder: "e3",
      claims: [{ text: "The next transfer out of ACC-3315 is in a quarantined batch, so it is not narrated as fact.", cites: ["DQ-1042"] }],
      signals: ["Data hold"],
      caveat: { text: "Missing data is not treated as low risk. Resolve the hold to continue the story.", cites: ["DQ-1042"] },
    });
    return frames.map((f) => ({ ...f, issues: [...f.claims, f.caveat].flatMap((c) => verifyClaim(c, registry)) }));
  }

  hop("e3", "hop", "Back to ACH", [hopClaim("e3", "4 h 29 m later,")], { text: "This hop was reloaded after mapping fix v4.1.", cites: ["DQ-1042"] });
  hop("e4", "hop", "Into a new business beneficiary", [hopClaim("e4", "The next morning,")], { text: "ACC-5590 is a new beneficiary; its business purpose is not on file.", cites: ["ACC-5590"] });
  hop("e5", "hop", "Cross-border exit", [hopClaim("e5", "Finally,")], { text: "Beneficiary details for the cross-border wire are synthetic metadata only.", cites: ["TX-88266"] });
  hop("e6", "lead", "Possible round trip (lead only)", [hopClaim("e6", "Two days later,")], {
    text: "Cycle search is bounded and the return could be unrelated. This is a lead, not a finding.",
    cites: ["DF-1027"],
  });

  const entered = edgeByKey("e1").amount;
  const exited = edgeByKey("e5").amount;
  const split = edgeByKey("e7").amount;
  frames.push({
    key: "summary",
    kind: "summary",
    title: "Where the funds went",
    edges: [...shown],
    focus: null,
    placeholder: null,
    claims: [
      {
        text: `${formatMoney(entered)} entered; ${formatMoney(exited)} left by cross-border wire, ${formatMoney(split)} went to ACC-3391 and ${formatMoney(entered - exited - split)} stayed in intermediary accounts.`,
        cites: ["TX-88201", "TX-88266", "TX-88222"],
        derived: [entered - exited - split],
      },
      { text: "Elapsed from first to last hop: 1 d 1 h 6 m.", cites: ["TX-88201", "TX-88266"] },
    ],
    signals: ["Layering, multi-hop", "Rapid pass-through"],
    caveat: { text: "A human reviewer decides what this means. The storyboard does not.", cites: ["GS-0412"] },
  });

  return frames.map((f) => ({ ...f, issues: [...f.claims, f.caveat].flatMap((c) => verifyClaim(c, registry)) }));
}
