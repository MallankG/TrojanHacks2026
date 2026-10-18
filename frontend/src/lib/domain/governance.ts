import type { DemoState } from "@/lib/store/state";
import type { Tone } from "@/lib/types";

export interface LineageStep {
  title: string;
  object: string;
  detail: string;
  owner: string;
  at: string;
  linked: boolean;
}

export function lineageSteps(state: DemoState): LineageStep[] {
  const fixed = state.data.driftFixed;
  const decided = state.caseWork.status !== "In review";
  const rev = state.caseWork.revisions[0].revision;
  return [
    { title: "Source file", object: "ach_2026_10_06_b.csv", detail: "sha256:9c2e…41aa (simulated)", owner: "Payments Ops · P. Nair", at: "Oct 6, 16:40 UTC", linked: true },
    { title: "Raw row", object: "Row 1042", detail: "Original fields kept unchanged", owner: "Ingestion agent", at: "Oct 6, 16:41 UTC", linked: true },
    { title: "Canonical transaction", object: fixed ? "TX-88237" : "Not created", detail: fixed ? "Normalized via mapping v4.1" : "Blocked: batch quarantined", owner: "Data steward", at: fixed ? "Oct 9, 14:04 UTC" : "n/a", linked: fixed },
    { title: "Normalized entity", object: "ACC-3315 to ACC-4471", detail: "Entity resolution v2, exact token", owner: "Entity service", at: "Oct 9, 14:04 UTC", linked: fixed },
    { title: "Feature snapshot", object: "FS-7-20261009", detail: "Feature set v7, versioned not overwritten", owner: "Feature service", at: fixed ? "Oct 9, 14:05 UTC" : "Oct 9, 13:40 UTC", linked: true },
    { title: "Detector result", object: fixed ? "SR-2292 · DF-1027" : "SR-2291 · DF-1027", detail: "xgb-aml v1.4.2 · rules v3.2", owner: "Model owner", at: fixed ? "Oct 9, 14:06 UTC" : "Oct 9, 13:40 UTC", linked: true },
    { title: "Alert", object: "AL-1027", detail: "Priority policy v3.1", owner: "Alert fusion", at: "Oct 9, 13:41 UTC", linked: true },
    { title: "Graph snapshot", object: fixed ? "GS-0412-b" : "GS-0412-a", detail: `max_hops 5, Oct 6 to 9, graph v${fixed ? "184" : "183"}`, owner: "Graph builder", at: fixed ? "Oct 9, 14:06 UTC" : "Oct 9, 13:42 UTC", linked: true },
    { title: "Case evidence", object: "CASE-0412", detail: `${state.graph.pinned.length} pinned items`, owner: "M. Rivera", at: "Oct 9, 13:44 UTC", linked: true },
    { title: "Narrative revision", object: `r${rev}`, detail: state.caseWork.claimFixed ? "0 blocked claims" : "1 blocked claim", owner: "Verifier", at: "Oct 9", linked: state.caseWork.claimFixed },
    { title: "Reviewer decision", object: decided ? state.caseWork.status : "Pending", detail: decided ? "Rationale saved" : "Not decided yet", owner: decided ? "Analyst or officer" : "n/a", at: decided ? "Now" : "n/a", linked: decided },
  ];
}

export type ImpactField = "target_account" | "amount" | "posted_at";

export function fieldImpact(field: ImpactField, fixed: boolean): Array<{ kind: string; value: string; status: string; tone: Tone }> {
  const rows: Record<ImpactField, Array<[string, string, string, Tone]>> = {
    target_account: [
      ["Features", "fan_in_7d, out_in_ratio_24h, hop_count_from_seed", "3 of 14 shown", "ok"],
      ["Alerts", "AL-1027, AL-1029, AL-1038", fixed ? "Re-scored" : "Stale", fixed ? "ok" : "warn"],
      ["Graph edges", "1,206 transaction edges", fixed ? "Restored" : "Missing", fixed ? "ok" : "warn"],
      ["Cases and reports", "CASE-0412 evidence set", fixed ? "Updated" : "Partial", fixed ? "ok" : "warn"],
      ["Untracked step", "Legacy memo parser has no lineage edge", "Lineage gap", "warn"],
    ],
    amount: [
      ["Features", "amount_vs_baseline_z, amount_sum_30d, out_in_ratio_24h", "Current", "ok"],
      ["Alerts", "128 open alerts use amount features", "Current", "ok"],
      ["Models", "xgb-aml v1.4.2, iforest v0.9", "Current", "ok"],
    ],
    posted_at: [
      ["Features", "median_interarrival_min, burstiness_24h", "Current", "ok"],
      ["Graph", "Time-respecting path search", "Current", "ok"],
      ["Alerts", "AL-1038 depends on row 3310", "Stale until repair", "warn"],
    ],
  };
  return rows[field].map(([kind, value, status, tone]) => ({ kind, value, status, tone }));
}

export function agentRun(state: DemoState): Array<{ name: string; status: "Done" | "Warning" | "Failed" | "Skipped" | "Waiting"; output: string; duration: string }> {
  const fixed = state.data.driftFixed;
  const decided = state.caseWork.status !== "In review";
  const firstRevision = state.caseWork.revisions[state.caseWork.revisions.length - 1].revision;
  return [
    { name: "1 Ingestion and data steward", status: fixed ? "Done" : "Warning", output: fixed ? "1 batch reloaded after fix" : "1 batch quarantined", duration: "2.1 s" },
    { name: "2 Entity resolution", status: "Done", output: "1 candidate held for human review", duration: "0.8 s" },
    { name: "3 Graph builder", status: state.sim.graph ? "Failed" : "Done", output: state.sim.graph ? "GraphBase unreachable; snapshot served" : `graph v${fixed ? "184" : "183"}`, duration: state.sim.graph ? "timeout 10 s" : "1.2 s" },
    { name: "4 Feature service", status: "Done", output: "As-of Oct 9 13:40 UTC", duration: "0.9 s" },
    { name: "5 Detection workers", status: "Done", output: "XGBoost, anomaly, 9 rules in parallel", duration: "1.6 s" },
    { name: "6 Alert fusion", status: "Done", output: "2 alerts clustered as CL-17", duration: "0.3 s" },
    { name: "7 Investigation planner", status: "Done", output: "4 allowlisted intents", duration: "0.2 s" },
    { name: "8 Policy RAG", status: "Done", output: "Retried once after timeout", duration: "1.9 s" },
    { name: "9 Similar-case retriever", status: "Done", output: "Top 5 returned", duration: "0.6 s" },
    { name: "10 Narrative drafter", status: state.sim.llm ? "Failed" : "Done", output: state.sim.llm ? "LLM unavailable; manual editing open" : `Draft r${firstRevision}`, duration: state.sim.llm ? "n/a" : "3.4 s" },
    { name: "11 Claim verifier", status: state.sim.llm ? "Skipped" : "Done", output: state.caseWork.claimFixed ? "0 claims blocked" : "1 claim blocked", duration: "0.04 s" },
    { name: "12 Human reviewer", status: decided ? "Done" : "Waiting", output: decided ? state.caseWork.status : "Awaiting disposition", duration: "n/a" },
  ];
}

export function agentTone(status: string): Tone {
  if (status === "Done") return "ok";
  if (status === "Failed") return "bad";
  if (status === "Waiting" || status === "Skipped") return "mute";
  return "warn";
}

export function graphSyncRuns(graphDown: boolean): Array<{ id: string; status: string; watermark: string; lag: string; tone: Tone }> {
  return [
    { id: graphDown ? "GSR-919" : "GSR-918", status: graphDown ? "Failed: GraphBase unreachable" : "Done", watermark: "Oct 9 13:59:18 UTC", lag: graphDown ? "n/a" : "42 s", tone: graphDown ? "bad" : "ok" },
    { id: "GSR-917", status: "Done", watermark: "Oct 9 13:05:09 UTC", lag: "38 s", tone: "ok" },
    { id: "GSR-916", status: "Done", watermark: "Oct 9 12:04:51 UTC", lag: "51 s", tone: "ok" },
  ];
}

export function bandError(min: string, max: string): string | null {
  if (!/^\d+$/.test(min.trim()) || !/^\d+$/.test(max.trim())) return "Enter whole-dollar amounts.";
  if (Number(min) >= Number(max)) return "The minimum must be lower than the maximum.";
  return null;
}
