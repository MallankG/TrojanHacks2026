import type { AnswerKey } from "@/lib/types";

export type Coverage = "Verified" | "Partially verified" | "Blocked before display" | "Denied" | "No claims made";

export interface AnswerItem {
  text: string;
  cites: string[];
}

export interface AnswerSection {
  heading: string;
  items: AnswerItem[];
}

export interface ToolCall {
  name: string;
  args: string;
  duration: string;
  status: "ok" | "partial" | "blocked";
}

export interface CopilotAnswer {
  title: string;
  coverage: Coverage;
  sections: AnswerSection[];
  tools: ToolCall[];
  policySources: string[];
  blocked?: Array<{ claim: string; reason: string }>;
}

export const PROMPT_CHIPS: Array<{ key: AnswerKey; label: string }> = [
  { key: "explain", label: "Explain this alert" },
  { key: "back", label: "Trace funds backward" },
  { key: "fwd", label: "Trace funds forward" },
  { key: "connected", label: "Why these accounts are connected?" },
  { key: "evidence", label: "Show evidence for each risk signal" },
  { key: "benign", label: "Find contradictory or benign explanations" },
  { key: "policy", label: "What policy guidance applies?" },
  { key: "missing", label: "What data is missing?" },
  { key: "similar", label: "Compare similar cases" },
  { key: "next", label: "What should I investigate next?" },
  { key: "inject", label: "Test: inject a false amount" },
];

const item = (text: string, cites: string[] = []): AnswerItem => ({ text, cites });
const section = (heading: string, items: AnswerItem[]): AnswerSection => ({ heading, items });

interface AnswerContext {
  fixed: boolean;
  graphDown: boolean;
}

/** Scripted, evidence-grounded answers. A production build replaces this with the agent API. */
export function buildAnswer(key: AnswerKey, { fixed, graphDown }: AnswerContext): CopilotAnswer {
  const graphTool = (args: string, ms: string): ToolCall =>
    graphDown ? { name: "trace_funds", args, duration: "failed, used snapshot", status: "partial" } : { name: "trace_funds", args, duration: ms, status: "ok" };

  switch (key) {
    case "explain":
      return {
        title: "Why AL-1027 was flagged",
        coverage: fixed ? "Verified" : "Partially verified",
        sections: [
          section("Observed facts", [
            item("ACC-2208 received $58,200.00 by ACH from ACC-1104 on Oct 6 at 09:14 UTC.", ["TX-88201"]),
            item("It sent $48,100.00 by P2P to ACC-3315 1 h 48 m later, and $9,600.00 to ACC-3391 at 12:10.", ["TX-88214", "TX-88222"]),
            item("ACC-2208 was opened 21 days before the first transfer.", ["ACC-2208"]),
          ]),
          section("Model and rule inferences", [
            item(`XGBoost scored ${fixed ? "0.91" : "0.71"} against a 0.62 threshold. Largest contributions: out/in ratio, short time between transfers, all-new counterparties.`, ["MR-2291"]),
            item(`Rules fired: rapid pass-through and new beneficiary${fixed ? ", and layering (4 hops)." : ". Layering was not evaluated because the path is incomplete."}`, ["DF-1027"]),
          ]),
          section("Data gaps", [
            fixed
              ? item("No open data gaps on this path.", ["DQ-1042"])
              : item("The ACC-3315 to ACC-4471 transfer is in quarantined batch ach_2026_10_06_b, so later hops are missing from this answer.", ["DQ-1042"]),
          ]),
        ],
        tools: [
          { name: "transaction_lookup", args: "TX-88201, TX-88214, TX-88222", duration: "84 ms", status: "ok" },
          { name: "feature_model_explanation", args: "AL-1027, MR-2291", duration: "112 ms", status: "ok" },
          { name: "quality_lineage_lookup", args: "path of AL-1027", duration: "65 ms", status: "ok" },
        ],
        policySources: [],
      };
    case "back":
      return {
        title: "Funds traced backward from ACC-2208",
        coverage: graphDown ? "Partially verified" : "Verified",
        sections: [
          section("Observed facts", [
            item("The money came from ACC-1104, a business operating account open for 26 months.", ["TX-88201", "ACC-1104"]),
            item("ACC-1104 later received a $12,000.00 wire back from the correspondent, on Oct 9.", ["TX-88290"]),
          ]),
          section("Data gaps", [
            item("No earlier hop was found within max_hops 3 and 72 hours. Older history exists outside this window."),
            ...(graphDown ? [item("GraphBase was unavailable, so this answer used saved snapshot GS-0412-a.", ["GS-0412"])] : []),
          ]),
        ],
        tools: [graphTool("seed ACC-2208, backward, max_hops 3, 72 h", "238 ms"), { name: "transaction_lookup", args: "TX-88201", duration: "41 ms", status: "ok" }],
        policySources: [],
      };
    case "fwd":
      return {
        title: "Funds traced forward from ACC-2208",
        coverage: fixed && !graphDown ? "Verified" : "Partially verified",
        sections: [
          section(
            "Observed facts",
            fixed
              ? [
                  item("ACC-2208 to ACC-3315: $48,100.00 by P2P at 11:02.", ["TX-88214"]),
                  item("ACC-3315 to ACC-4471: $47,650.00 by ACH at 15:31.", ["TX-88237"]),
                  item("ACC-4471 to ACC-5590: $47,100.00 by wire on Oct 7 at 08:05.", ["TX-88259"]),
                  item("ACC-5590 to EXT-CB-77: $46,800.00 by cross-border wire at 10:20. Elapsed from the first deposit: 1 d 1 h 6 m.", ["TX-88266"]),
                ]
              : [item("ACC-2208 to ACC-3315: $48,100.00 by P2P at 11:02.", ["TX-88214"]), item("The trace stops at ACC-3315. Its next transfer is in a quarantined batch.", ["DQ-1042"])],
          ),
          section("Limitations", [item("Funds in a shared balance are mixed, so the path shows where money plausibly moved, not which exact dollars.")]),
        ],
        tools: [graphTool("seed ACC-2208, forward, max_hops 5, 72 h, time-respecting", "311 ms")],
        policySources: [],
      };
    case "connected":
      return {
        title: "Why these accounts are connected",
        coverage: "Verified",
        sections: [
          section("Observed facts", [
            item("ACC-2208 and ACC-3391 used the same device token, DV-77, on 4 logins.", ["DV-77"]),
            item("ACC-2208 sent $9,600.00 to ACC-3391 on Oct 6.", ["TX-88222"]),
            item("An address-token match (confidence 0.83) is a candidate only and has not been approved.", ["ACC-3391"]),
          ]),
          section("Counter-evidence", [item("Shared devices are common in households. This is a review lead, not proof.", ["DV-77"])]),
        ],
        tools: [{ name: "related_entity_lookup", args: "ACC-2208, edge types SHARES_*", duration: "96 ms", status: "ok" }],
        policySources: [],
      };
    case "evidence":
      return {
        title: "Evidence for each risk signal",
        coverage: fixed ? "Verified" : "Partially verified",
        sections: [
          section("Rapid pass-through", [item("Out/in ratio 0.99 within 24 hours on ACC-2208.", ["TX-88201", "TX-88214", "TX-88222"])]),
          section("New beneficiary", [item("All 3 counterparties first seen in the last 7 days.", ["ACC-2208"])]),
          section("Layering", [fixed ? item("Four hops in 25 h 6 m with rail switches ACH, P2P, ACH, wire.", ["TX-88237", "TX-88259", "TX-88266"]) : item("Not evaluated: path incomplete.", ["DQ-1042"])]),
          section("Shared token", [item("Device DV-77 shared with ACC-3391.", ["DV-77"])]),
        ],
        tools: [
          { name: "feature_model_explanation", args: "AL-1027", duration: "104 ms", status: "ok" },
          { name: "cycle_motif_search", args: "bounded 3 to 10 hops", duration: "420 ms", status: "ok" },
        ],
        policySources: [],
      };
    case "benign":
      return {
        title: "Contradictory and benign explanations",
        coverage: "Verified",
        sections: [
          section("Possible benign explanations", [
            item("ACC-1104 pays suppliers weekly. A first payment to a new supplier is possible.", ["ACC-1104"]),
            item("ACC-5590 is a 2-month-old import and export business. Cross-border payments fit its declared purpose.", ["ACC-5590"]),
            item("Shared devices can be a shared household.", ["DV-77"]),
          ]),
          section("What would help tell them apart", [
            item("Invoices or contracts for the ACC-1104 payment are not available.", ["DQ-GAP-INV"]),
            item("No reversal or refund is linked to these transfers.", ["TX-88201"]),
          ]),
        ],
        tools: [
          { name: "transaction_lookup", args: "reversal links for path", duration: "52 ms", status: "ok" },
          { name: "related_entity_lookup", args: "ACC-1104, ACC-5590 profiles", duration: "88 ms", status: "ok" },
        ],
        policySources: [],
      };
    case "policy":
      return {
        title: "Relevant policy guidance",
        coverage: "Verified",
        sections: [
          section("Policy context", [
            item("FFIEC BSA/AML Examination Manual, Suspicious Activity Reporting: the retrieved passage describes how institutions identify and review unusual activity.", ["POL-FFIEC-SAR"]),
            item("FinCEN public guidance retrieved for funds-movement red flags. The exact passage is shown word for word in the evidence panel.", ["POL-FINCEN-ADV"]),
          ]),
          section("Limitations", [item("Policy text explains process. It does not prove that anyone did anything wrong.")]),
        ],
        tools: [{ name: "policy_retrieval", args: "topic: suspicious activity, jurisdiction US, top 3", duration: "388 ms", status: "ok" }],
        policySources: ["POL-FFIEC-SAR", "POL-FINCEN-ADV"],
      };
    case "missing":
      return {
        title: "What data is missing",
        coverage: "Verified",
        sections: [
          section("Data gaps", [
            ...(fixed ? [] : [item("The ACC-3315 to ACC-4471 transfer is in a quarantined batch.", ["DQ-1042"])]),
            item("Invoices or contracts for the ACC-1104 payment.", ["DQ-GAP-INV"]),
            item("Expected-activity details for the ACC-4471 prepaid wallet.", ["ACC-4471"]),
            item("Beneficiary details for the cross-border wire are synthetic metadata only.", ["TX-88266"]),
          ]),
        ],
        tools: [{ name: "quality_lineage_lookup", args: "CASE-0412 evidence set", duration: "73 ms", status: "ok" }],
        policySources: [],
      };
    case "similar":
      return {
        title: "Similar synthetic cases",
        coverage: "Verified",
        sections: [
          section("Top 5 by flow shape, rail mix and new counterparties", [
            item("CASE-0287 · similarity 0.82 · prior decision: escalated.", ["CASE-0287"]),
            item("CASE-0301 · 0.77 · closed as benign (treasury sweep).", ["CASE-0301"]),
            item("CASE-0244 · 0.71 · escalated (device-linked cluster).", ["CASE-0244"]),
            item("CASE-0352 · 0.64 · closed, insufficient evidence.", ["CASE-0352"]),
            item("CASE-0198 · 0.58 · escalated (round trip).", ["CASE-0198"]),
          ]),
          section("Limitations", [item("Similarity is not proof, and prior decisions can be wrong.")]),
        ],
        tools: [{ name: "similar_case_retrieval", args: "typology layering, last 365 d, top 5", duration: "154 ms", status: "ok" }],
        policySources: [],
      };
    case "next":
      return {
        title: "Suggested next steps",
        coverage: "Verified",
        sections: [
          section("Suggestions for the investigator", [
            item("Request beneficiary details for the cross-border wire through the correspondent.", ["TX-88266"]),
            item("Review linked alert AL-1029 for the shared-device account.", ["AL-1029"]),
            item("Confirm the business purpose of ACC-5590 with the relationship team.", ["ACC-5590"]),
            item("Check whether ACC-1104 authorized the payment; it could be scam proceeds rather than layering.", ["ACC-1104"]),
          ]),
          section("Boundaries", [item("These are suggestions only. No customer contact, account restriction or filing is done by the copilot.")]),
        ],
        tools: [{ name: "investigation_planner", args: "4 allowlisted intents ranked", duration: "61 ms", status: "ok" }],
        policySources: [],
      };
    case "inject":
      return {
        title: "Adversarial test: verifier check",
        coverage: "Blocked before display",
        blocked: [
          { claim: "TX-88299", reason: "This transaction ID does not exist in the CASE-0412 scope." },
          { claim: "$158,200.00", reason: "Does not match evidence. $58,200.00 entered the path (TX-88201)." },
        ],
        sections: [
          section("Shown instead: verified facts only", [
            item("$58,200.00 entered the path, $46,800.00 left by cross-border wire and $9,600.00 went to ACC-3391. $1,800.00 stayed in intermediary accounts.", ["TX-88201", "TX-88266", "TX-88222"]),
          ]),
        ],
        tools: [
          { name: "draft_answer", args: "injected test claims", duration: "640 ms", status: "ok" },
          { name: "claim_verifier", args: "2 of 3 claims failed", duration: "35 ms", status: "blocked" },
        ],
        policySources: [],
      };
    case "deny":
      return {
        title: "Request narrowed",
        coverage: "Denied",
        sections: [
          section("What happened", [
            item("That request reaches outside CASE-0412 or asks for an unbounded graph query. No records outside your case scope were read."),
            item("An audit event was created for this request."),
          ]),
          section("Try instead", [item("Trace funds forward from ACC-2208, max 5 hops, Oct 6 to 9.")]),
        ],
        tools: [{ name: "scope_guard", args: "tenant and case check", duration: "4 ms", status: "blocked" }],
        policySources: [],
      };
    case "clarify":
    default:
      return {
        title: "I can answer about this case",
        coverage: "No claims made",
        sections: [section("Try one of these", [item("Explain this alert, trace funds backward or forward, show evidence for each risk signal, or ask what data is missing.")])],
        tools: [],
        policySources: [],
      };
  }
}

/** Maps free text to an allowlisted intent. Anything outside case scope is denied. */
export function routeIntent(text: string): AnswerKey {
  const t = text.toLowerCase();
  if (/case-0(?!412)\d{3}/.test(t) || ["all accounts", "every account", "entire", "all customers", "whole bank"].some((w) => t.includes(w))) return "deny";
  if (t.includes("back") || t.includes("came from")) return "back";
  if (t.includes("forward") || t.includes("went") || t.includes("trace")) return "fwd";
  if (t.includes("polic") || t.includes("ffiec") || t.includes("fincen")) return "policy";
  if (t.includes("similar")) return "similar";
  if (t.includes("benign") || t.includes("legit") || t.includes("contradict")) return "benign";
  if (t.includes("missing") || t.includes("gap")) return "missing";
  if (t.includes("connect") || t.includes("device")) return "connected";
  if (t.includes("next")) return "next";
  if (t.includes("evidence") || t.includes("signal")) return "evidence";
  if (t.includes("why") || t.includes("flag") || t.includes("explain")) return "explain";
  return "clarify";
}

export function coverageTone(coverage: Coverage) {
  switch (coverage) {
    case "Verified":
      return "ok" as const;
    case "Partially verified":
      return "warn" as const;
    case "Blocked before display":
    case "Denied":
      return "bad" as const;
    default:
      return "mute" as const;
  }
}
