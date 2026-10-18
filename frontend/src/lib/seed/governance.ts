import type { AuditEvent, CaseSummary, ExportRecord, ModelEntry } from "@/lib/types";

export const MODELS: ModelEntry[] = [
  { key: "xgb", name: "xgb-aml", version: "v1.4.2", status: "Champion · deployed", tone: "ok", data: "IBM AMLworld HI-Small (synthetic), chronological split", commit: "a41c9e2", schema: "Feature set v7", threshold: "0.62", checksum: "sha256:5b1e…9a40 (simulated)", use: "Ranks transaction and account leads for human review.", limits: "Trained on synthetic data. Scores are ranking signals, not probabilities of crime." },
  { key: "rules", name: "typology-rules", version: "v3.2", status: "Baseline · deployed", tone: "ok", data: "Deterministic detectors, 9 typologies", commit: "77d03bf", schema: "Rule DSL v2", threshold: "Per-rule parameters", checksum: "sha256:c08d…11fe (simulated)", use: "Explainable typology signals beside the model.", limits: "Parameters are review settings, not legal thresholds." },
  { key: "iforest", name: "iforest-anomaly", version: "v0.9", status: "Shadow · not used in priority", tone: "info", data: "Account aggregates, rolling 30 d", commit: "19ab4e0", schema: "Aggregates v3", threshold: "Top 5% percentile", checksum: "sha256:2a7f…e3b9 (simulated)", use: "Unsupervised ranking signal.", limits: "Not a probability. Sensitive to seasonality." },
  { key: "gnn", name: "graphsage-exp", version: "v0.1", status: "Experimental · not deployed", tone: "mute", data: "History-only neighborhoods, chronological split", commit: "e2210c4", schema: "Graph features v1", threshold: "Not set", checksum: "sha256:8d44…0c1a (simulated)", use: "Research challenger only.", limits: "Only promoted if it beats XGBoost on the same split." },
];

export const CASES: CaseSummary[] = [
  { id: "CASE-0412", title: "Layering, rapid pass-through", owner: "M. Rivera", alerts: "AL-1027, AL-1029", amount: "$58,200.00" },
  { id: "CASE-0398", title: "High-volume payroll lookalike", owner: "M. Rivera", alerts: "AL-0977", amount: "$208,900.00", status: "Closed, no further action", summary: "Same 214 recipients every 2 weeks for 18 months, matched to a payroll file. Closed as benign after human review." },
  { id: "CASE-0405", title: "Funnel, fan-in", owner: "J. Okafor", alerts: "AL-0991", amount: "$71,400.00", status: "Escalated for SAR-support review", summary: "13 new senders to one account in 7 days. Bundle EXP-0405-01 exported for human review." },
  { id: "CASE-0409", title: "Dormant reactivation", owner: "M. Rivera", alerts: "AL-1002", amount: "$22,750.00", status: "Awaiting information", summary: "Source-of-funds documents requested from the business line (simulated)." },
];

export const PRIMARY_CASE = "CASE-0412" as const;

export function isCaseId(value: string): value is CaseSummary["id"] {
  return CASES.some((c) => c.id === value);
}

export function caseById(id: CaseSummary["id"]): CaseSummary {
  const found = CASES.find((c) => c.id === id);
  if (!found) throw new Error(`Unknown case ${id}`);
  return found;
}

export const SEED_EVENTS: AuditEvent[] = [
  { at: "2026-10-09 13:58:40 UTC", actor: "A. Lee (Auditor)", type: "Access", action: "Audit trail exported (CSV)", object: "audit_events", requestId: "REQ-8094", hash: "4be1c07a92d3" },
  { at: "2026-10-09 13:52:12 UTC", actor: "System", type: "AI", action: "Narrative r1 drafted. Verifier blocked 1 unsupported claim", object: "CASE-0412", requestId: "REQ-8091", hash: "0c93f1d4a7e2" },
  { at: "2026-10-09 13:44:30 UTC", actor: "M. Rivera (Analyst)", type: "Case", action: "Case opened from alert AL-1027", object: "CASE-0412", requestId: "REQ-8088", hash: "a7d2e90b1c44" },
  { at: "2026-10-09 13:41:02 UTC", actor: "System", type: "Alert", action: "AL-1027 and AL-1029 created and clustered", object: "CL-17", requestId: "REQ-8085", hash: "9e01b7cd3f58" },
  { at: "2026-10-09 13:40:55 UTC", actor: "System", type: "Model", action: "Scoring run SR-2291 completed (xgb-aml v1.4.2, rules v3.2)", object: "SR-2291", requestId: "REQ-8084", hash: "1f6ac8e2d9b0" },
  { at: "2026-10-09 13:22:10 UTC", actor: "System", type: "Data", action: "Owner notified: Payments Ops, P. Nair", object: "ach_2026_10_06_b", requestId: "REQ-8079", hash: "5d2c71e0a6f3" },
  { at: "2026-10-09 13:20:04 UTC", actor: "System", type: "Data", action: "Batch quarantined: required field target_account missing (schema drift)", object: "ach_2026_10_06_b", requestId: "REQ-8078", hash: "e48b03f2c71a" },
  { at: "2026-10-09 13:05:51 UTC", actor: "System", type: "Graph", action: "Graph sync GSR-917 completed, watermark 13:05:09", object: "GraphBase", requestId: "REQ-8072", hash: "3a9d6e1b0f27" },
];

export const SEED_EXPORTS: ExportRecord[] = [
  { id: "EXP-0405-01", caseId: "CASE-0405", by: "J. Okafor", at: "2026-10-08 17:12 UTC", revision: "r3", hash: "7c1e04ab93d2f5e8" },
];

export const FEATURE_FLAGS = [
  { key: "propagation", label: "Network propagation (1 to 2 hop re-review)" },
  { key: "passport", label: "Evidence passport per alert" },
  { key: "snapshotDiff", label: "Graph snapshot diff" },
  { key: "timeMachine", label: "Money-flow time machine (point-in-time replay)" },
  { key: "counterEvidence", label: "Why this alert, paired with why it might be benign" },
  { key: "alertDiff", label: "Alert diff and decision change log" },
  { key: "replay", label: "One-click investigation replay" },
  { key: "sandbox", label: "Policy and threshold what-if sandbox" },
  { key: "impactRadar", label: "Data-quality impact radar" },
  { key: "storyboard", label: "Animated flow explanation" },
  { key: "gnn", label: "GraphSAGE challenger (shadow)" },
] as const;

export type FeatureFlagKey = (typeof FEATURE_FLAGS)[number]["key"];

export const SIMILAR_CASES: Array<{ id: string; title: string; similarity: string; decision: string }> = [
  { id: "CASE-0287", title: "Layering through new accounts, ACH to P2P to wire", similarity: "0.82", decision: "Escalated" },
  { id: "CASE-0301", title: "Pass-through that was a treasury sweep", similarity: "0.77", decision: "Closed, benign" },
  { id: "CASE-0244", title: "Device-linked mule cluster", similarity: "0.71", decision: "Escalated" },
  { id: "CASE-0352", title: "Rapid movement, insufficient evidence", similarity: "0.64", decision: "Closed, no action" },
  { id: "CASE-0198", title: "Round trip through correspondent", similarity: "0.58", decision: "Escalated" },
];
