"use client";

import { call } from "./api";

/** Case workflow endpoints: evidence checklist, hashed evidence packages and escalation requests. */

export interface ChecklistItem {
  key: string;
  label: string;
  status: "complete" | "partial" | "missing";
  detail: string;
  evidence_refs: string[];
}

export interface ExportFile {
  name: string;
  bytes: number;
  sha256: string;
  content_type: string;
  url: string | null;
}

export interface EvidencePackage {
  id: string;
  case_id: string;
  manifest_sha256: string;
  package_sha256: string;
  files: ExportFile[];
  created_at: string;
  creator?: { display_name: string | null; role: string } | null;
}

export const casesApi = {
  checklist: (id: string) => call<{ case_id: string; as_of: string; items: ChecklistItem[] }>(`/cases/${id}/checklist`),
  exportPackage: (id: string) => call<EvidencePackage>(`/cases/${id}/evidence-export`, { method: "POST", timeoutMs: 60_000 }),
  exports: (id: string) => call<EvidencePackage[]>(`/cases/${id}/exports`),
  requestEscalation: (id: string, reason: string) => call<unknown>(`/cases/${id}/escalation-request`, { method: "POST", body: { reason } }),
};

/** SHA-256 of a downloaded file, computed in the browser, to check it against the recorded hash. */
export async function sha256Hex(url: string): Promise<string> {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`Download failed (status ${response.status})`);
  const digest = await crypto.subtle.digest("SHA-256", await response.arrayBuffer());
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
