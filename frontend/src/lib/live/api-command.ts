"use client";

import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";
import { call, query, useLive, type Loadable } from "./api";
import { useLiveProfile } from "./session";

/** Command center endpoints: dashboard aggregates, global search and derived notifications. */

export type Band = "critical" | "high" | "medium" | "low";
export type FlagStatus = "ok" | "attention" | "failed" | "not_run";

export interface ModelFreshness {
  id: string;
  model_name: string | null;
  model_version: string | null;
  completed_at: string | null;
  age_hours: number | null;
}

export interface DashboardSummary {
  generated_at: string;
  alerts: {
    open_total: number;
    by_status: Record<string, number>;
    by_priority_band: Record<Band, number>;
    review_first: number;
    by_typology: Array<{ typology: string; count: number }>;
    age: {
      sla_days: number;
      oldest_hours: number | null;
      median_hours: number | null;
      past_sla: number;
      buckets: Array<{ label: string; min_days: number; max_days: number | null; count: number; past_sla: boolean }>;
    };
    /** Exact decimal strings per currency; an estimate, never a loss figure. */
    estimated_amount: { is_estimate: true; note: string; by_currency: Record<string, string>; alerts_without_amount: number };
    truncated: boolean;
    run_alert_total: number;
    connected_accounts: number;
    connected_accounts_truncated: boolean;
    model_run: { id: string; model_name: string; model_version: string; completed_at: string | null } | null;
  };
  /** Null for roles without case access (data stewards). */
  cases: { total: number; open_total: number; pending_review: number; by_status: Record<string, number>; truncated: boolean } | null;
  ingestion: {
    run_count: number;
    latest_run: { id: string; status: string; started_at: string | null; completed_at: string | null; filename: string | null; row_count: number; rejected_count: number } | null;
    total_rows: number;
    accepted_rows: number;
    warning_rows: number;
    rejected_rows: number;
    rejected_rate: number | null;
    failed_runs: number;
    partial_runs: number;
    open_issues_by_severity: Record<"critical" | "error" | "warning" | "info", number>;
    open_issues_total: number;
    truncated: boolean;
  };
  model_freshness: Record<"rule_engine" | "supervised" | "anomaly" | "graph_model" | "ranker", ModelFreshness | null>;
  /** The agents' configured LLM (no network check): `configured` means calls are attempted; `reason` says why not. */
  llm?: { provider: string | null; model: string | null; configured: boolean; reason: string | null };
  pipeline: Array<{ key: "ingestion" | "data_quality" | "scoring"; status: FlagStatus; detail: string }>;
}

export type SearchType = "alert" | "case" | "account" | "transaction";

export interface SearchHit {
  type: SearchType;
  id: string;
  label: string;
  description: string;
  /** Transactions only: the origin account, used to open its entity profile. */
  account_id?: string;
}

export interface SearchResponse {
  query: string;
  results: { alerts: SearchHit[]; cases: SearchHit[]; accounts: SearchHit[]; transactions: SearchHit[] };
  cases_included: boolean;
}

export interface LiveNotification {
  id: string;
  kind: "case_pending_review" | "new_alerts" | "ingestion_failed" | "ingestion_partial" | "critical_data_quality";
  at: string | null;
  title: string;
  detail: string | null;
  target_type: "case" | "cases" | "alerts" | "ingestion_run" | "data_quality";
  target_id: string | null;
}

export const commandApi = {
  summary: () => call<DashboardSummary>("/dashboard/summary"),
  search: (q: string) => call<SearchResponse>(`/search${query({ q })}`),
  notifications: () => call<{ items: LiveNotification[]; total: number }>("/notifications"),
};

/** Shell numbers refresh on this cadence and whenever the user moves to another screen (e.g. after an upload). */
const REFRESH_MS = 60_000;

function useRefresh(query: Loadable<unknown>, enabled: boolean) {
  const { reload } = query;
  const pathname = usePathname();
  const lastPath = useRef(pathname);
  useEffect(() => {
    if (!enabled || lastPath.current === pathname) return;
    lastPath.current = pathname;
    reload();
  }, [enabled, pathname, reload]);
  useEffect(() => {
    if (!enabled) return;
    const timer = window.setInterval(reload, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [enabled, reload]);
}

/** The dashboard summary; `data` stays null without a live session. */
export function useDashboardSummary(): Loadable<DashboardSummary> & { live: boolean } {
  const live = useLiveProfile() !== null;
  const result = useLive(async () => (live ? await commandApi.summary() : null), `dashboard:summary:${live}`) as Loadable<DashboardSummary>;
  useRefresh(result, live);
  return { ...result, live };
}

export function useLiveNotifications(): Loadable<{ items: LiveNotification[]; total: number }> & { live: boolean } {
  const live = useLiveProfile() !== null;
  const result = useLive(async () => (live ? await commandApi.notifications() : null), `dashboard:notifications:${live}`) as Loadable<{
    items: LiveNotification[];
    total: number;
  }>;
  useRefresh(result, live);
  return { ...result, live };
}
