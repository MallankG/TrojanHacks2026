"use client";

import { call, query, type Alert, type CaseStatus, type DqIssue, type Page } from "./api";

/** Live account search and the point-in-time account profile (`GET /accounts/{id}/profile`). */

export interface LiveAccount {
  id: string;
  account_ref: string;
  institution_ref: string | null;
  is_internal: boolean;
  customer_ref?: string | null;
  account_type?: string | null;
  open_date?: string | null;
  status: "active" | "dormant" | "restricted" | "closed";
  customer_risk_rating?: "low" | "medium" | "high" | null;
  jurisdiction?: string | null;
  risk_context?: Record<string, unknown> | null;
  is_synthetic?: boolean;
  created_at?: string;
}

/** Decimal values arrive as strings and stay exact until display. */
type Dec = string;

export interface FlowWindow {
  inflow_count: number;
  inflow_amount: Dec;
  outflow_count: number;
  outflow_amount: Dec;
  net_flow: Dec;
  unique_counterparties: number;
}

export interface TicketStats {
  count: number;
  median: Dec | null;
  p25: Dec | null;
  p75: Dec | null;
  p90: Dec | null;
  max: Dec | null;
}

export interface RobustZ {
  z: Dec | null;
  method: "median_absolute_deviation" | "mean_absolute_deviation_fallback" | null;
  baseline_median: Dec | null;
  scale: Dec | null;
  reason: string | null;
}

type AccountBrief = Pick<LiveAccount, "id" | "account_ref" | "institution_ref" | "is_internal"> | null;

export interface ProfileCounterparty {
  account_id: string;
  account: AccountBrief;
  inflow_count: number;
  outflow_count: number;
  inflow_amount: Dec;
  outflow_amount: Dec;
  total_amount: Dec;
  first_seen_at: string;
  last_seen_at: string;
  status: "new" | "known" | "outside_windows";
}

export interface AccountProfile {
  account: LiveAccount;
  windows: {
    as_of: string;
    observed: { days: number; start: string; end: string };
    baseline: { days: number; start: string; end: string };
    lookback_start: string;
    flow_windows: string[];
    boundaries: string;
  };
  flows: Record<"1h" | "24h" | "7d" | "30d", FlowWindow>;
  baseline_vs_observed: {
    baseline: FlowWindow & { transactions: number; daily_volume_median: Dec | null; daily_count_median: Dec | null; ticket: TicketStats };
    observed: FlowWindow & { transactions: number; daily_volume_mean: Dec; daily_count_mean: Dec; ticket: TicketStats };
    volume_z: RobustZ;
    count_z: RobustZ;
    statistic: string;
  };
  ratios: {
    window: string;
    inflow_outflow_ratio: Dec | null;
    pass_through_ratio: Dec | null;
    net_flow: Dec;
    retained_balance_proxy: Dec | null;
    retained_balance_note: string;
    inter_arrival_median_hours: Dec | null;
    inter_arrival_gaps: number;
    counterparty_hhi: Dec | null;
    top_counterparty_share: Dec | null;
  };
  counterparties: ProfileCounterparty[];
  counterparties_total: number;
  new_counterparties: Array<{ account_id: string; account: AccountBrief; first_seen_at: string; direction: "in" | "out" }>;
  daily_series: Array<{ date: string; inflow: Dec; outflow: Dec; count: number }>;
  counts: { transactions_used: number; excluded_after_as_of: number; excluded_no_usd_amount: number; excluded_self_transfers: number };
  activity: { first_at: string | null; last_at: string | null };
  limits: { max_transactions: number; truncated: boolean; transactions_read: number; history_notes: string[] };
  related: {
    alerts: Array<Pick<Alert, "id" | "alert_number" | "typology" | "priority_band" | "status" | "created_at"> & { role: string }>;
    cases: Array<{ id: string; case_number: number; title: string; status: CaseStatus; priority_band: Alert["priority_band"] }>;
  };
  data_quality: {
    transaction_issues: Array<Pick<DqIssue, "id" | "source_row_id" | "run_id" | "field_name" | "issue_type" | "severity" | "rule_id" | "message" | "status"> & { source_rows?: { row_number: number; status: string } | null }>;
    rejected_rows: Array<{ source_row_id: string; run_id: string; row_number: number; side: "origin_account" | "destination_account"; issues: Array<Pick<DqIssue, "id" | "rule_id" | "issue_type" | "severity" | "message">> }>;
    rejected_rows_scanned: number;
    rejected_scan_capped: boolean;
    note: string;
  };
  not_available: { expected_activity: string | null; geography: string | null; shared_tokens: string | null };
}

export const entityApi = {
  /** Accounts whose reference contains `q` (case-insensitive). */
  searchAccounts: (q: string, limit = 25) => call<Page<LiveAccount>>(`/accounts${query({ q: q.trim(), limit })}`),
  profile: (id: string, params: { as_of?: string; observed_days?: number; baseline_days?: number } = {}) =>
    call<AccountProfile>(`/accounts/${id}/profile${query(params)}`),
};
