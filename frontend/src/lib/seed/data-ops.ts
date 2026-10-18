import type { BadRowSeed, Batch, ContractCheck, Tone } from "@/lib/types";

export const HELD_BATCH = "ach_2026_10_06_b";

export interface SourceFeed {
  name: string;
  owner: string;
  contract: string;
  freshness: string;
  sla: string;
  status: string;
  tone: Tone;
  trust: string;
  gap: boolean;
}

export function sourceFeeds(fixed: boolean, checkScope: boolean): SourceFeed[] {
  return [
    { name: "Core ACH feed", owner: "Payments Ops · P. Nair", contract: "ach.v4", freshness: "12 min ago", sla: "SLA 30 min", status: fixed ? "Current" : "Hold", tone: fixed ? "ok" : "warn", trust: fixed ? "Complete 99.8% · Valid 99.9%" : "Complete 93.4% · Valid 99.9%", gap: false },
    { name: "Wire feed", owner: "Treasury Ops · D. Sato", contract: "wire.v3", freshness: "8 min ago", sla: "SLA 30 min", status: "Current", tone: "ok", trust: "Complete 100% · Valid 100%", gap: false },
    { name: "P2P feed", owner: "Digital Banking · R. Gomez", contract: "p2p.v2", freshness: "5 min ago", sla: "SLA 15 min", status: "Current", tone: "ok", trust: "Complete 99.9% · Valid 99.9%", gap: false },
    { name: "Card feed", owner: "Card Services · L. Park", contract: "card.v2", freshness: "22 min ago", sla: "SLA 60 min", status: "Current", tone: "ok", trust: "Complete 99.7% · Valid 99.8%", gap: false },
    { name: "Branch cash feed", owner: "Branch Ops · K. Adams", contract: "cash.v1", freshness: "3 h ago", sla: "SLA 2 h", status: "Late", tone: "warn", trust: "Last validation Oct 9, 10:58 UTC", gap: false },
    { name: "Check feed", owner: "Unassigned", contract: "Not defined", freshness: "Never", sla: "No SLA", status: checkScope ? "Onboarding" : "Not monitored", tone: checkScope ? "info" : "warn", trust: checkScope ? "Contract draft created" : "Coverage gap", gap: !checkScope },
  ];
}

export function seededBatches(fixed: boolean): Batch[] {
  return [
    { name: HELD_BATCH, rail: "ACH", rows: "18,240", accepted: fixed ? "18,212" : "0", rejected: fixed ? "28" : "18,240", status: fixed ? "Loaded after fix" : "Quarantined", tone: fixed ? "ok" : "warn", owner: "Payments Ops · P. Nair", schema: fixed ? "ach.v4 · mapping v4.1" : "ach.v4 · mapping v4.0", hash: "9c2e…41aa" },
    { name: "ach_2026_10_06", rail: "ACH", rows: "21,904", accepted: "21,889", rejected: "15", status: "Loaded", tone: "ok", owner: "Payments Ops · P. Nair", schema: "ach.v4 · mapping v4.0", hash: "3f9a…c21e" },
    { name: "p2p_2026_10_06", rail: "P2P", rows: "9,870", accepted: "9,861", rejected: "9", status: "Loaded", tone: "ok", owner: "Digital Banking · R. Gomez", schema: "p2p.v2", hash: "6b0d…77a3" },
    { name: "wire_2026_10_07", rail: "Wire", rows: "1,312", accepted: "1,312", rejected: "0", status: "Loaded", tone: "ok", owner: "Treasury Ops · D. Sato", schema: "wire.v3", hash: "a51c…02e9" },
    { name: "wire_2026_10_09", rail: "Wire", rows: "1,288", accepted: "1,288", rejected: "0", status: "Loaded", tone: "ok", owner: "Treasury Ops · D. Sato", schema: "wire.v3", hash: "d7e4…9b10" },
    { name: "cash_2026_10_09", rail: "Cash", rows: "Not received", accepted: "0", rejected: "0", status: "Late", tone: "warn", owner: "Branch Ops · K. Adams", schema: "cash.v1", hash: "n/a" },
  ];
}

function check(label: string, count: string, ok: boolean, note: string): ContractCheck {
  return { label, count, result: ok ? "Pass" : "Fail", tone: ok ? "ok" : "warn", note };
}

export function batchChecks(batch: Batch, fixed: boolean): ContractCheck[] {
  if (batch.name === HELD_BATCH) {
    return [
      check("Required fields present", fixed ? "0" : "1,206", fixed, fixed ? "target_account mapped from bene_acct_no (v4.1)" : "target_account missing in 1,206 rows"),
      check("Schema drift", fixed ? "0" : "1 column", fixed, fixed ? "Mapping v4.1 approved" : "New column bene_acct_no not in contract"),
      check("Duplicate transaction IDs", "1", false, "Row 2087 rejected"),
      check("Invalid timestamps", "1", false, "Row 3310 rejected"),
      check("Impossible amounts", "1", false, "Row 5518 rejected"),
      check("Currency mismatch", "1", true, "Row 4125 normalized, original kept"),
      check("Unknown accounts", "1", false, "Row 7104 held"),
      check("Future dates", "1", false, "Row 6020 rejected"),
      check("Reversal mismatch", "0", true, "No issues"),
      check("Unresolved entity links", "1", false, "Row 7104 sent to review"),
    ];
  }
  if (batch.status === "Late") {
    return [{ label: "Feed not received", count: "-", result: "Late", tone: "warn", note: "Owner reminded at 12:58 UTC" }];
  }
  if (batch.checks) return batch.checks;
  const clean = batch.rejected === "0";
  return [check("All 10 contract checks", batch.rejected, clean, clean ? "No exceptions" : `${batch.rejected} rows rejected; see quality exceptions`)];
}

export const BLAST_RADIUS: Array<[string, string]> = [
  ["Rows held", "1,206"],
  ["Features to recompute", "14"],
  ["Alerts to re-score", "AL-1027, AL-1029, AL-1038"],
  ["Entity links", "2"],
  ["Graph snapshot", "GS-0412-a, replaced by GS-0412-b"],
  ["Cases", "CASE-0412"],
];

export const BAD_ROWS: BadRowSeed[] = [
  { row: 1042, type: "Missing identifier", group: "rejected", field: "target_account", original: "(empty) · source column bene_acct_no = ACC-4471", mode: "mapping", features: ["fan_in_7d · ACC-4471", "out_in_ratio_24h · ACC-3315", "hop_count · AL-1027"], alerts: ["AL-1027", "AL-1029"], links: ["Edge ACC-3315 to ACC-4471"], cases: ["CASE-0412"] },
  { row: 2087, type: "Duplicate transaction ID", group: "rejected", field: "transaction_id", original: "TX-88241 (same as row 1881)", mode: "repair", features: ["velocity_24h · ACC-6620 (double count)"], alerts: [], links: [], cases: [], before: '{ "row": 2087, "transaction_id": "TX-88241", "amount": "1250.00" }', after: '{ "row": 2087, "status": "dropped_duplicate", "kept_row": 1881 }', fixNote: "Keep row 1881, drop row 2087", result: "Rejected: duplicate of row 1881" },
  { row: 3310, type: "Invalid timestamp", group: "rejected", field: "posted_at", original: "2026-13-06 14:22:09", mode: "repair", features: ["interarrival_median · ACC-7120"], alerts: ["AL-1038"], links: [], cases: [], before: '{ "posted_at": "2026-13-06 14:22:09" }', after: '{ "posted_at": "2026-10-06T19:22:09Z", "tz_original": "America/Chicago" }', fixNote: "Proposed 2026-10-06 14:22:09 local (month and day swapped in source)", result: "Rejected: month 13 is out of range" },
  { row: 4125, type: "Currency mismatch", group: "flagged", field: "currency", original: "US$", mode: "auto", features: ["amount_sum_30d · ACC-5012"], alerts: [], links: [], cases: [], result: "Flagged: not an ISO 4217 code", normalized: "USD (auto-normalized, original kept)" },
  { row: 5518, type: "Impossible amount", group: "rejected", field: "amount", original: "-48,000.00 on a credit entry", mode: "repair", features: ["amount_vs_baseline_z · ACC-8101"], alerts: [], links: [], cases: [], before: '{ "amount": "-48000.00", "direction": "credit" }', after: '{ "amount": "48000.00", "direction": "credit", "note": "sign corrected" }', fixNote: "Correct the sign to match the credit direction", result: "Rejected: sign does not match direction" },
  { row: 6020, type: "Future date", group: "rejected", field: "posted_at", original: "2026-11-06 10:00:00", mode: "owner", features: [], alerts: [], links: [], cases: [], result: "Rejected: after the file cutoff (Oct 6)", normalized: "Not normalized. Needs a corrected export from the source owner." },
  { row: 7104, type: "Unknown account", group: "flagged", field: "source_account", original: "ACC-9X12", mode: "entity", features: ["all features for this row"], alerts: [], links: ["Unresolved entity link"], cases: [], result: "Held: not in the account master", normalized: "2 candidates: ACC-9012 (0.74), ACC-9112 (0.61)" },
];

export const CONTRACT_FIELDS: Array<{ field: string; type: string; required: string; rule: string }> = [
  { field: "transaction_id", type: "string", required: "Required", rule: "Unique per source" },
  { field: "timestamp_utc", type: "timestamp", required: "Required", rule: "ISO 8601; original time zone kept" },
  { field: "amount", type: "decimal(18,2)", required: "Required", rule: "Sign matches direction" },
  { field: "currency", type: "ISO 4217", required: "Required", rule: "USD for domestic rails" },
  { field: "source_account", type: "token", required: "Required", rule: "Must exist in account master" },
  { field: "target_account", type: "token", required: "Required", rule: "Must exist in account master" },
  { field: "rail", type: "enum", required: "Required", rule: "ACH, WIRE, P2P, CARD, CHECK, CASH, BOOK" },
  { field: "reference", type: "text", required: "Optional", rule: "Untrusted free text, never executed" },
];

export const DATA_OWNERS = ["Payments Ops · P. Nair", "Core Banking · T. Brooks"] as const;
