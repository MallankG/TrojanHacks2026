"use client";

import { call, query, type IngestionRun, type UploadResult } from "./api";

/** Data operations endpoints: source-feed health, canonical schema, DQ rule catalog and mapping preview. */

export type SourceStatus = "current" | "late" | "has_rejections" | "failed" | "no_runs";

export interface SourceFeed {
  source_system: string;
  owners: string[];
  schema_versions: string[];
  is_synthetic: boolean;
  file_count: number;
  run_count: number;
  last_upload_at: string | null;
  hours_since_upload: number | null;
  freshness_sla_hours: number;
  rows: number;
  accepted: number;
  warnings: number;
  rejected: number;
  accepted_rate: number | null;
  warning_rate: number | null;
  rejected_rate: number | null;
  open_issue_count: number;
  latest_run: { id: string; status: IngestionRun["status"]; started_at: string; row_count: number | null; accepted_count: number | null; rejected_count: number | null } | null;
  status: SourceStatus;
  status_reasons: string[];
}

export interface SchemaField {
  field: string;
  required: boolean;
  type: string;
  description: string;
  rule_ids: string[];
}

export interface CanonicalSchema {
  schema_version: string;
  rules_version: string;
  required_fields: string[];
  optional_fields: string[];
  fields: SchemaField[];
  channels: string[];
  currencies: string[];
}

export interface DqRule {
  rule_id: string;
  issue_type: string;
  severity: "info" | "warning" | "error" | "critical";
  scope: "file" | "row";
  fields: string[];
  checks: string;
  outcome: string;
}

export interface PreviewIssue {
  rule_id: string;
  severity: DqRule["severity"];
  field_name: string | null;
  message: string;
}

export interface MappingPreview {
  filename: string;
  schema_version: string;
  headers: string[];
  mapping: Record<string, string>;
  fields: Array<{
    field: string;
    required: boolean;
    type: string;
    description: string;
    column: string | null;
    /** How the column was chosen: same name, known alias, set by the user, or deliberately left unmapped. */
    match: "exact" | "alias" | "manual" | "unmapped" | null;
    column_present: boolean;
  }>;
  missing_required: string[];
  unmapped_columns: string[];
  schema_issues: Array<PreviewIssue & { observed_value: string | null }>;
  can_upload: boolean;
  rows: Array<{ row_number: number; values: Record<string, string>; status: "accepted" | "accepted_with_warnings" | "rejected" | null; issues: PreviewIssue[] }>;
  sampled_rows: number;
}

/** `GET /ingestion/runs/{id}`: the run with its full source-file record and the field mapping it used. */
export interface RunDetail extends UploadResult {
  run: IngestionRun & {
    field_mapping: Record<string, string> | null;
    pipeline_version: string | null;
    source_files: {
      id: string;
      filename: string;
      source_system: string;
      data_owner: string;
      checksum_sha256: string;
      byte_size: number | null;
      schema_version: string;
      is_synthetic: boolean;
      uploaded_at: string;
    } | null;
  };
}

export const dataOpsApi = {
  sources: (freshnessSlaHours?: number) =>
    call<{ items: SourceFeed[]; total: number; freshness_sla_hours: number; as_of: string }>(`/ingestion/sources${query({ freshness_sla_hours: freshnessSlaHours })}`),
  schema: () => call<CanonicalSchema>("/ingestion/schema"),
  rules: () => call<{ rules_version: string; items: DqRule[]; total: number }>("/data-quality/rules"),
  run: (runId: string) => call<RunDetail>(`/ingestion/runs/${runId}`),
  previewMapping: (form: FormData) => call<MappingPreview>("/ingestion/preview-mapping", { method: "POST", body: form, timeoutMs: 30_000 }),
  downloadUrl: (fileId: string) => call<{ url: string; expires_in: number; filename: string }>(`/ingestion/source-files/${fileId}/download-url`),
};
