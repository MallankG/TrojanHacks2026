"use client";

import type { AuditEvent, AuditType, Batch, ContractCheck, Tone } from "@/lib/types";
import { liveApi, useLive, type AuditRow, type DqIssue, type IngestionRun, type Loadable } from "./api";
import { utc, words } from "./format";
import { useLiveProfile } from "./session";

/**
 * Adapters from backend records to the UI's seeded shapes, so the existing screens render live
 * data with their own components. Each hook returns `null` data when there is no live session.
 */

const RUN_STATUS: Record<IngestionRun["status"], [string, Tone]> = {
  completed: ["Loaded", "ok"],
  completed_with_errors: ["Partially accepted", "info"],
  failed: ["Failed", "bad"],
  running: ["Running", "info"],
  pending: ["Pending", "mute"],
};

const count = (n: number | null | undefined) => (n ?? 0).toLocaleString("en-US");

export function runToBatch(run: IngestionRun): Batch {
  const [status, tone] = RUN_STATUS[run.status];
  const file = run.source_files?.filename ?? "upload.csv";
  return {
    name: `${file.replace(/\.csv$/i, "")} · ${run.id.slice(0, 6)}`,
    rail: run.source_files?.source_system ?? "Not recorded",
    rows: count(run.row_count),
    accepted: count(run.accepted_count),
    rejected: count(run.rejected_count),
    status,
    tone,
    owner: run.source_files?.data_owner ?? "Not recorded",
    schema: run.schema_version,
    hash: run.source_files?.checksum_sha256 ? `${run.source_files.checksum_sha256.slice(0, 4)}…${run.source_files.checksum_sha256.slice(-4)}` : "n/a",
    runId: run.id,
  };
}

/** Live ingestion runs as batches; `data` is null when signed in to the demo only. */
export function useLiveBatches(): Loadable<Batch[]> & { live: boolean } {
  const live = useLiveProfile() !== null;
  const query = useLive(async () => (live ? (await liveApi.runs()).items.map(runToBatch) : []), `runs:${live}`);
  return { ...query, live };
}

/** Contract-check rows for a live run, grouped by data-quality rule. */
export function issuesToChecks(issues: DqIssue[]): ContractCheck[] {
  if (issues.length === 0) return [{ label: "All data-quality rules", count: "0", result: "Pass", tone: "ok", note: "No exceptions" }];
  const byRule = new Map<string, DqIssue[]>();
  for (const issue of issues) byRule.set(issue.rule_id, [...(byRule.get(issue.rule_id) ?? []), issue]);
  return [...byRule.entries()].map(([rule, list]) => {
    const blocking = list.some((i) => i.severity === "error" || i.severity === "critical");
    const rows = list.map((i) => i.source_rows?.row_number).filter((n): n is number => typeof n === "number");
    return {
      label: `${words(list[0].issue_type)} (${rule})`,
      count: String(list.length),
      result: blocking ? "Fail" : "Flagged",
      tone: "warn",
      note: rows.length > 0 ? `Row${rows.length > 1 ? "s" : ""} ${rows.slice(0, 6).join(", ")}${rows.length > 6 ? "…" : ""} ${blocking ? "rejected" : "kept with a caveat"}` : list[0].message,
    };
  });
}

const OBJECT_TYPE: Record<string, AuditType> = {
  ingestion_run: "Data",
  data_quality_issue: "Data",
  source_file: "Data",
  model_run: "Model",
  alert: "Alert",
  case: "Case",
  case_decision: "Case",
  case_narrative: "AI",
  agent_run: "AI",
  policy_document: "Policy",
  profile: "Access",
};

/** Audit rows in the audit trail's display shape. The backend log has no payload hash yet. */
export function auditToEvent(row: AuditRow, names: Record<string, string>): AuditEvent {
  const actor = row.actor_type === "user" ? (row.actor_id ? (names[row.actor_id] ?? `User ${row.actor_id.slice(0, 8)}`) : "User") : words(row.actor_type);
  return {
    at: utc(row.occurred_at),
    actor,
    type: row.action.endsWith(".exported") ? "Export" : (OBJECT_TYPE[row.object_type] ?? "Data"),
    action: row.action,
    object: row.object_id ? `${words(row.object_type)} ${row.object_id.slice(0, 8)}` : words(row.object_type),
    requestId: row.request_id ? row.request_id.slice(0, 8) : `#${row.event_id}`,
    hash: "Not recorded",
    live: true,
  };
}

/** The tenant's live audit log (newest first) in the audit trail's display shape. */
export function useLiveAuditEvents(params: { object_type?: string; object_id?: string } = {}): Loadable<AuditEvent[]> & { live: boolean } {
  const profile = useLiveProfile();
  const query = useLive(async () => {
    if (!profile) return [];
    const page = await liveApi.audit({ ...params, limit: 200 });
    const names = { [profile.id]: profile.display_name ?? profile.email ?? "You" };
    return page.items.map((row) => auditToEvent(row, names));
  }, `audit:${profile?.id ?? "demo"}:${params.object_type ?? ""}:${params.object_id ?? ""}`);
  return { ...query, live: profile !== null };
}
