import type { ContractCheck } from "@/lib/types";

export type OnboardFileKey = "ach7" | "wire8" | "seed" | "pdf" | "big" | "empty" | "dup";
export type TargetMapping = "" | "bene_acct_no" | "memo" | "skip";

export interface OnboardFile {
  key: OnboardFileKey;
  name: string;
  note: string;
  /** When set, the file is rejected at step 1 with this message. */
  error?: string;
}

export const ONBOARD_FILES: OnboardFile[] = [
  { key: "ach7", name: "ach_2026_10_07.csv", note: "ACH · 19,880 rows · 4.1 MB" },
  { key: "wire8", name: "wire_2026_10_08.csv", note: "Wire · 1,104 rows · 0.3 MB" },
  { key: "seed", name: "Seeded scenario suite A to J", note: "Synthetic · fixed seed 20261017 · 5,000 rows" },
  { key: "pdf", name: "statement_oct.pdf", note: "PDF document", error: "Only .csv files are accepted. Export the data as UTF-8 CSV and try again." },
  { key: "big", name: "ach_full_year.csv", note: "640 MB", error: "This file is 640 MB and the limit is 200 MB. Split it by day or rail and upload each part." },
  { key: "empty", name: "empty_export.csv", note: "Header row only", error: "The file has a header row but no transactions. Check the export filters." },
  { key: "dup", name: "ach_2026_10_06.csv", note: "Already ingested", error: "This exact file (same SHA-256) was ingested as ach_2026_10_06 on Oct 6. Nothing new to load." },
];

export const ONBOARD_STEPS = ["Source", "Profile", "Map", "Validate", "Confirm"] as const;
export const ONBOARD_TITLES = ["Choose a source", "Profile the file", "Map columns to the contract", "Validate the data contract", "Confirm and commit"] as const;

export interface ProfileColumn {
  column: string;
  type: string;
  nulls: string;
  example: string;
  finding: string;
}

export function profileFor(file: OnboardFileKey): ProfileColumn[] {
  if (file === "ach7") {
    return [
      { column: "txn_id", type: "string", nulls: "0%", example: "TX-88401", finding: "" },
      { column: "posted_at", type: "timestamp", nulls: "0%", example: "2026-10-07 09:03:11", finding: "2 formats: ISO (99.9%), MM/DD/YYYY (0.1%)" },
      { column: "amt", type: "decimal", nulls: "0%", example: "4980.00", finding: "" },
      { column: "ccy", type: "string", nulls: "0%", example: "USD", finding: "USD 99.97%, US$ 0.02%, XXX 0.01%" },
      { column: "acct_from", type: "token", nulls: "0%", example: "ACC-2208", finding: "" },
      { column: "bene_acct_no", type: "token", nulls: "0.4%", example: "ACC-7712", finding: "New column: not in contract v4.0" },
      { column: "rail", type: "enum", nulls: "0%", example: "ACH", finding: "" },
      { column: "memo", type: "text", nulls: "31%", example: "INV 2231", finding: "Untrusted free text" },
    ];
  }
  return [
    { column: "txn_id", type: "string", nulls: "0%", example: "TX-90112", finding: "" },
    { column: "posted_at", type: "timestamp", nulls: "0%", example: "2026-10-08 10:12:40", finding: "ISO 100%" },
    { column: "amt", type: "decimal", nulls: "0%", example: "12500.00", finding: "" },
    { column: "ccy", type: "string", nulls: "0%", example: "USD", finding: "USD 100%" },
    { column: "acct_from", type: "token", nulls: "0%", example: "ACC-5590", finding: "" },
    { column: "acct_to", type: "token", nulls: "0%", example: "EXT-CB-12", finding: "" },
    { column: "rail", type: "enum", nulls: "0%", example: "WIRE", finding: "" },
  ];
}

export interface MappingRow {
  field: string;
  column: string;
  confidence: string;
  status: string;
  ok: boolean;
}

export function mappingFor(file: OnboardFileKey, target: TargetMapping): MappingRow[] {
  const needsChoice = file === "ach7";
  const targetColumn = !needsChoice ? "acct_to" : target === "bene_acct_no" ? "bene_acct_no" : target === "skip" ? "Left unmapped" : target === "memo" ? "memo" : "Needs your choice";
  const rows: Array<[string, string, string]> = [
    ["transaction_id", "txn_id", "99%"],
    ["timestamp_utc", "posted_at", "97%"],
    ["amount", "amt", "98%"],
    ["currency", "ccy", "99%"],
    ["source_account", "acct_from", "96%"],
    ["target_account", targetColumn, needsChoice ? "62%" : "99%"],
    ["rail", "rail", "99%"],
  ];
  return rows.map(([field, column, confidence]) => {
    const low = field === "target_account" && needsChoice;
    const ok = !low || target === "bene_acct_no";
    return { field, column, confidence, status: low ? (target === "bene_acct_no" ? "Confirmed by you" : "Low confidence") : "Auto-matched", ok };
  });
}

export function previewRows(file: OnboardFileKey, target: TargetMapping): string[][] {
  const missing = file === "ach7" && target !== "bene_acct_no";
  return [
    ["TX-88401", "2026-10-07T14:03:11Z", "4,980.00", "USD", "ACC-2208", missing ? "(missing)" : "ACC-7712"],
    ["TX-88402", "2026-10-07T14:05:52Z", "1,250.00", "USD", "ACC-1180", missing ? "(missing)" : "ACC-3302"],
    ["TX-88403", "2026-10-07T14:06:30Z", "312.40", "USD (from US$)", "ACC-9021", missing ? "(missing)" : "ACC-4410"],
  ];
}

function vcheck(label: string, count: string, ok: boolean, note: string): ContractCheck {
  const flagged = note.includes("held") || note.includes("normalized");
  return { label, count, result: ok ? "Pass" : flagged ? "Flagged" : "Fail", tone: ok ? "ok" : "warn", note };
}

export function validationFor(file: OnboardFileKey, target: TargetMapping): ContractCheck[] {
  if (file === "ach7") {
    const required = target === "bene_acct_no";
    return [
      vcheck("Required fields", required ? "0" : "19,880", required, required ? "All present" : "target_account unmapped: whole batch quarantined"),
      vcheck("Duplicate transaction IDs", "2", false, "2 rows rejected"),
      vcheck("Invalid timestamps", "1", false, "1 row rejected (ambiguous MM/DD)"),
      vcheck("Impossible amounts", "0", true, "None"),
      vcheck("Currency mismatch", "3", false, "2 normalized from US$, 1 unknown XXX rejected"),
      vcheck("Unknown accounts", "1", false, "1 row rejected"),
      vcheck("Reversal mismatch", "0", true, "None"),
      vcheck("Future dates", "0", true, "None"),
      vcheck("Schema drift", "1", true, "bene_acct_no mapped in v4.1"),
      vcheck("Unresolved entity links", "4", false, "4 rows held for entity review"),
    ];
  }
  if (file === "seed") return [vcheck("All 10 contract checks", "6", false, "Scenario J: 6 malformed rows rejected on purpose")];
  return [vcheck("All 10 contract checks", "0", true, "No exceptions")];
}

export interface OnboardSummary {
  accepted: string;
  rejected: string;
  flagged?: string;
  mapping: string;
  quarantine: boolean;
}

export function summaryFor(file: OnboardFileKey, target: TargetMapping): OnboardSummary {
  if (file === "ach7") {
    if (target !== "bene_acct_no") return { accepted: "0", rejected: "19,880", mapping: "v4.0, target_account unmapped", quarantine: true };
    return { accepted: "19,875", rejected: "5", flagged: "6", mapping: "v4.1 (new version)", quarantine: false };
  }
  if (file === "seed") return { accepted: "4,994", rejected: "6", mapping: "scenario.v1", quarantine: false };
  return { accepted: "1,104", rejected: "0", mapping: "wire.v3", quarantine: false };
}
