"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, apiRequest } from "@/lib/api/client";
import { getAccessToken, useLiveSession } from "./session";

/** Live-mode calls to the FastAPI backend, authenticated with the user's Supabase access token. */

export interface Page<T> {
  items: T[];
  total: number | null;
  limit: number;
  offset: number;
}

export interface IngestionRun {
  id: string;
  file_id: string;
  status: "pending" | "running" | "completed" | "completed_with_errors" | "failed";
  schema_version: string;
  row_count: number | null;
  accepted_count: number | null;
  warning_count: number | null;
  rejected_count: number | null;
  error_message: string | null;
  started_at: string;
  completed_at: string | null;
  source_files?: { filename: string; source_system: string | null; data_owner: string | null; checksum_sha256?: string } | null;
}

export interface UploadResult {
  run: IngestionRun;
  issue_counts_by_type: Record<string, number>;
  issue_counts_by_severity: Record<string, number>;
}

export interface DqIssue {
  id: string;
  run_id: string;
  source_row_id: string | null;
  field_name: string | null;
  issue_type: string;
  severity: "info" | "warning" | "error" | "critical";
  rule_id: string;
  message: string;
  observed_value: string | null;
  expected_value: string | null;
  status: "open" | "acknowledged" | "remediated" | "waived";
  remediation_note: string | null;
  created_at: string;
  source_rows?: { row_number: number; status?: string } | null;
}

export interface SourceRowDetail {
  source_row: {
    id: string;
    row_number: number;
    raw_data: Record<string, string>;
    status: string;
    run_id: string;
    ingestion_runs?: { schema_version: string; field_mapping: Record<string, string> | null; source_files?: { filename: string; data_owner: string | null } | null } | null;
  };
  issues: DqIssue[];
  transaction: LiveTransaction | null;
  downstream: {
    features: Array<{ id: string; feature_set_version: string }>;
    risk_signals: Array<{ id: string; signal_name: string; typology: string | null; triggered: boolean }>;
    alerts: Array<{ role: string; alerts: { id: string; alert_number: number; typology: string; priority_band: string; status: string } }>;
  };
}

export interface Alert {
  id: string;
  alert_number: number;
  scope: "transaction" | "account" | "network";
  typology: string;
  priority_band: "low" | "medium" | "high" | "critical";
  priority_rank: number | null;
  rank_score: string | number | null;
  ranking_strategy: string | null;
  component_signals: Record<string, unknown> | unknown[] | null;
  window_start: string | null;
  window_end: string | null;
  total_amount: string | null;
  amount_currency: string | null;
  transaction_count: number | null;
  connected_account_count: number | null;
  has_dq_caveat: boolean;
  dq_caveat_notes: string | null;
  limitations: string | null;
  explanation: string | null;
  model_run_id: string;
  status: "new" | "under_review" | "in_case" | "closed_no_action" | "dismissed";
  created_at: string;
  model_runs?: { model_name: string; model_version: string; completed_at: string | null } | null;
}

export interface LiveTransaction {
  id: string;
  transaction_ref: string;
  occurred_at: string;
  amount: string;
  currency: string;
  amount_usd: string | null;
  channel: string;
  description?: string | null;
  origin_account_id: string;
  destination_account_id: string;
  source_row_id?: string | null;
  role?: string;
}

export interface RiskSignal {
  id: string;
  source: string;
  signal_name: string;
  signal_version: string | null;
  typology: string | null;
  score: string | number | null;
  threshold: string | number | null;
  triggered: boolean;
  details: Record<string, unknown> | null;
}

export interface AlertDetail extends Alert {
  signals: Array<{ risk_signals: RiskSignal }>;
  transactions: Array<{ role: string; transactions: LiveTransaction }>;
  accounts: Array<{ role: string; accounts: { id: string; account_ref: string; institution_ref: string | null; is_internal: boolean; status: string } }>;
  cases: Array<{ cases: { id: string; case_number: number; title: string; status: string } }>;
}

export interface GraphPayload {
  nodes: Array<{ id: string; account_ref?: string; degree: number; role: string | null; is_internal?: boolean }>;
  edges: Array<{ id: string; source: string; target: string; transaction_ref: string; occurred_at: string; amount: string; currency: string; channel: string }>;
  truncated: boolean;
}

export type CaseStatus = "open" | "in_review" | "pending_review" | "escalated" | "closed";
export type Disposition = "no_further_action" | "false_positive" | "monitor" | "needs_more_information" | "escalate_for_sar_consideration";

export interface LiveCase {
  id: string;
  case_number: number;
  title: string;
  summary: string | null;
  status: CaseStatus;
  priority_band: Alert["priority_band"];
  disposition: Disposition | null;
  disposition_note: string | null;
  disposed_at: string | null;
  created_at: string;
  updated_at: string;
  assignee?: { id: string; display_name: string | null; role: string } | null;
}

export interface CaseDecision {
  id: string;
  decision: "accept" | "edit" | "reject" | "escalate";
  disposition: Disposition | null;
  notes: string | null;
  decided_at: string;
  decided_by_profile?: { display_name: string | null; role: string } | null;
}

export interface CaseDetail extends LiveCase {
  alerts: Array<{ added_at: string; alerts: Alert }>;
  evidence: Array<{ id: string; evidence_type: string; explanation: string; source_ref: string | null; transaction_ids: string[]; added_by_type: string; created_at: string }>;
  narratives: Array<{ id: string; version: number; author_type: string; verification_status: string; created_at: string; llm_model: string | null }>;
  decisions: CaseDecision[];
  agent_runs: Array<{ id: string; graph_name: string; status: string; current_step: string | null; created_at: string }>;
}

export interface AuditRow {
  id: string;
  event_id: number;
  actor_id: string | null;
  actor_type: "user" | "system" | "agent";
  action: string;
  object_type: string;
  object_id: string | null;
  occurred_at: string;
  request_id: string | null;
  metadata: Record<string, unknown>;
}

/** Authenticated backend call. Feature modules (`lib/live/api-*.ts`) build their endpoints on this. */
export async function call<T>(path: string, init: Parameters<typeof apiRequest>[1] = {}): Promise<T> {
  const token = await getAccessToken();
  if (!token) throw new ApiError("Your session has ended. Sign in again.", 401);
  return apiRequest<T>(path, { cache: "no-store", ...init, headers: { Authorization: `Bearer ${token}`, ...init.headers } });
}

export function query(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const liveApi = {
  runs: (limit = 50) => call<Page<IngestionRun>>(`/ingestion/runs${query({ limit })}`),
  /** Every issue of a run, paged 500 at a time up to `cap`; `total` is the true count even when capped. */
  runIssues: async (runId: string, cap = 5000): Promise<Page<DqIssue>> => {
    const items: DqIssue[] = [];
    let total: number | null = null;
    while (items.length < cap) {
      const page = await call<Page<DqIssue>>(`/ingestion/runs/${runId}/issues${query({ limit: 500, offset: items.length })}`);
      items.push(...page.items);
      total = page.total;
      if (page.items.length < 500 || (total !== null && items.length >= total)) break;
    }
    return { items, total, limit: items.length, offset: 0 };
  },
  upload: (form: FormData) => call<UploadResult>("/ingestion/upload", { method: "POST", body: form, timeoutMs: 120_000 }),
  issues: (status?: string, limit = 100) => call<Page<DqIssue>>(`/data-quality/issues${query({ status, limit })}`),
  updateIssue: (id: string, body: { status: DqIssue["status"]; remediation_note?: string }) =>
    call<DqIssue>(`/data-quality/issues/${id}`, { method: "PATCH", body }),
  sourceRow: (id: string) => call<SourceRowDetail>(`/data-quality/source-rows/${id}`),
  alerts: (params: { status?: string; all_runs?: boolean; limit?: number } = {}) => call<Page<Alert> & { model_run_id: string | null }>(`/alerts${query(params)}`),
  alert: (id: string) => call<AlertDetail>(`/alerts/${id}`),
  alertGraph: (id: string) => call<GraphPayload>(`/alerts/${id}/graph`),
  alertLineage: (id: string) => call<Record<string, unknown>>(`/lineage/alerts/${id}`),
  updateAlert: (id: string, status: Alert["status"]) => call<Alert>(`/alerts/${id}`, { method: "PATCH", body: { status } }),
  cases: (limit = 50) => call<Page<LiveCase>>(`/cases${query({ limit })}`),
  case: (id: string) => call<CaseDetail>(`/cases/${id}`),
  createCase: (body: { title: string; summary?: string; priority_band?: Alert["priority_band"]; alert_ids: string[] }) =>
    call<LiveCase>("/cases", { method: "POST", body }),
  updateCase: (id: string, body: { status?: "open" | "in_review" | "pending_review"; summary?: string }) =>
    call<LiveCase>(`/cases/${id}`, { method: "PATCH", body }),
  addNote: (id: string, explanation: string) =>
    call<unknown>(`/cases/${id}/evidence`, { method: "POST", body: { evidence_type: "analyst_note", explanation } }),
  decide: (id: string, body: { decision: CaseDecision["decision"]; disposition?: Disposition | null; notes?: string }) =>
    call<{ decision: CaseDecision; case: LiveCase }>(`/cases/${id}/decisions`, { method: "POST", body }),
  exportUrl: (id: string) => call<{ url: string; path: string; expires_in: number }>(`/cases/${id}/export-url`),
  audit: (params: { object_type?: string; object_id?: string; limit?: number } = {}) => call<Page<AuditRow>>(`/audit/events${query(params)}`),
};

export interface Loadable<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

const invalidators = new Set<(prefix: string) => void>();

/** Refetches every mounted live query whose key starts with `prefix` (e.g. after an upload or a decision). */
export function invalidateLive(prefix: string): void {
  invalidators.forEach((fn) => fn(prefix));
}

/** Last good result per (user, key), so remounted screens render immediately while they revalidate. */
const lastResults = new Map<string, unknown>();

/**
 * Loads data for a live screen; `key` changes refetch. Errors are shown, never thrown.
 * The signed-in user is part of the cache key, so another user (or signing in after a failed
 * attempt) never sees a previous session's results.
 */
export function useLive<T>(load: () => Promise<T>, baseKey: string): Loadable<T> {
  const user = useLiveSession()?.profile?.id ?? "signed-out";
  const key = `${user}|${baseKey}`;
  const initial = (): { key: string; data: T | null; error: string | null; done: boolean } =>
    lastResults.has(key) ? { key, data: lastResults.get(key) as T, error: null, done: true } : { key, data: null, error: null, done: false };
  const [state, setState] = useState(initial);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    const onInvalidate = (prefix: string) => {
      if (baseKey.startsWith(prefix)) reload();
    };
    invalidators.add(onInvalidate);
    return () => {
      invalidators.delete(onInvalidate);
    };
  }, [baseKey, reload]);

  useEffect(() => {
    let cancelled = false;
    load()
      .then((data) => {
        lastResults.set(key, data);
        if (!cancelled) setState({ key, data, error: null, done: true });
      })
      .catch((error: unknown) => {
        if (!cancelled) setState({ key, data: null, error: errorMessage(error), done: true });
      });
    return () => {
      cancelled = true;
    };
    // `load` is recreated every render; `key` and `tick` decide when to refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, tick]);

  const fresh = state.key === key;
  const cached = fresh ? null : ((lastResults.get(key) as T | undefined) ?? null);
  return { data: fresh ? state.data : cached, error: fresh ? state.error : null, loading: !fresh ? cached === null : !state.done, reload };
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 0) return "The backend is not configured. Set NEXT_PUBLIC_API_BASE_URL.";
    return error.requestId ? `${error.message} (request ${error.requestId})` : error.message;
  }
  if (error instanceof DOMException && error.name === "AbortError") return "The backend did not respond in time.";
  if (error instanceof TypeError) return "Cannot reach the backend. Is it running?";
  return error instanceof Error ? error.message : "Something went wrong.";
}
