"use client";

import { call, query, type GraphPayload } from "./api";

/** Graph workbench endpoints: account search, neighborhoods, time-respecting paths and bounded pattern search. */

export type GraphDirection = "incoming" | "both" | "outgoing";
export type Rail = "ach" | "wire" | "card" | "cash" | "check" | "p2p" | "internal_transfer" | "crypto" | "other";
export type PatternOperation = "cycles" | "fan_in" | "fan_out" | "pass_through";

export const RAILS: Rail[] = ["ach", "wire", "p2p", "card", "cash", "check", "internal_transfer", "crypto", "other"];

/** Server caps, mirrored so the controls never offer a value the API would reject with 422. */
export const GRAPH_CAPS = { neighborhoodHops: 3, pathHops: 6, cycleHops: 10, patternWindowDays: 31, windowDays: 366 } as const;

export interface GraphAccount {
  id: string;
  account_ref: string;
  institution_ref: string | null;
  is_internal: boolean;
  status: string;
  in_count: number;
  out_count: number;
  degree: number;
  counterparty_count: number;
  volume_usd: string | number;
  first_seen: string | null;
  last_seen: string | null;
}

export interface AccountSearch {
  items: GraphAccount[];
  suggested: { account_id: string; reason: "top_alert" | "most_connected"; alert_id: string | null; alert_number: number | null } | null;
}

export interface GraphNode {
  id: string;
  account_ref?: string;
  institution_ref?: string | null;
  is_internal?: boolean;
  degree: number;
  in_degree?: number;
  out_degree?: number;
  role: string | null;
  layer?: number | null;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  transaction_ref: string | null;
  occurred_at: string;
  amount: string;
  currency: string;
  amount_usd?: string | null;
  channel: string;
  source_row_id?: string | null;
}

export interface WorkbenchGraph extends Omit<GraphPayload, "nodes" | "edges"> {
  nodes: GraphNode[];
  edges: GraphEdge[];
  limits?: { hops: number; direction: GraphDirection; max_nodes: number; max_edges: number; timeout_s: number; stopped_by: string | null };
}

export interface Hop {
  transaction_id: string;
  transaction_ref: string | null;
  from: string;
  to: string;
  occurred_at: string;
  amount: string;
  currency: string;
  amount_usd: string | null;
  channel?: string | null;
  gap_hours: number | null;
}

export interface TracedPath {
  hops: Hop[];
  length: number;
  start_amount_usd: number;
  end_amount_usd: number;
  amount_delta_usd: string;
  max_gap_hours: number | null;
  elapsed_hours: number;
}

export interface PathResult {
  paths: TracedPath[];
  graph: WorkbenchGraph;
  parameters: Record<string, unknown>;
  searched_edges: number;
  truncated: boolean;
  caveat: string;
}

export interface PatternFinding {
  kind: "cycle" | "fan_in" | "fan_out" | "pass_through";
  anchor_account_id: string;
  account_ids: string[];
  transaction_ids: string[];
  hops: Hop[];
  elapsed_hours: number;
  summary: string;
  hop_count?: number;
  counterparty_count?: number;
  transaction_count?: number;
  amount_delta_usd?: string;
  total_amount_usd?: string;
  pass_through_ratio?: number;
}

export interface PatternResult {
  operation: PatternOperation;
  account_id: string;
  window: { start: string | null; end: string | null };
  parameters: Record<string, unknown>;
  findings: PatternFinding[];
  graph: WorkbenchGraph | null;
  searched: { edges: number; accounts: number; truncated: boolean; timed_out: boolean; budget_exhausted?: boolean; edge_cap: number; timeout_s: number };
  elapsed_ms: number;
  complete: false;
  limitation: string;
  caveat: string;
}

export interface GraphFilters {
  start?: string;
  end?: string;
  rails: Rail[];
  minAmount: number;
}

/** Repeated `channel=` parameters for the rail filter (an empty list means every rail). */
function withRails(base: string, rails: Rail[]): string {
  if (rails.length === 0) return base;
  const extra = rails.map((r) => `channel=${encodeURIComponent(r)}`).join("&");
  return base ? `${base}&${extra}` : `?${extra}`;
}

export const graphApi = {
  accounts: (q?: string, limit = 25) => call<AccountSearch>(`/graph/accounts${query({ q, limit })}`),
  neighborhood: (accountId: string, p: GraphFilters & { hops: number; direction: GraphDirection }) =>
    call<WorkbenchGraph>(
      `/graph/accounts/${accountId}/neighborhood${withRails(query({ hops: p.hops, direction: p.direction, start: p.start, end: p.end, min_amount: p.minAmount || undefined }), p.rails)}`,
      { timeoutMs: 30_000 },
    ),
  paths: (p: GraphFilters & { source: string; target: string; maxHops: number; maxGapHours: number }) =>
    call<PathResult>(
      `/graph/paths${withRails(
        query({ source_account_id: p.source, target_account_id: p.target, max_hops: p.maxHops, max_gap_hours: p.maxGapHours, start: p.start, end: p.end, min_amount: p.minAmount || undefined }),
        p.rails,
      )}`,
      { timeoutMs: 30_000 },
    ),
  patterns: (body: {
    operation: PatternOperation;
    account_id: string;
    start?: string;
    end?: string;
    channels?: Rail[];
    min_amount?: number;
    max_hops?: number;
    max_gap_hours?: number;
    min_counterparties?: number;
    max_span_hours?: number;
  }) => call<PatternResult>("/graph/patterns/search", { method: "POST", body, timeoutMs: 30_000 }),
  pinEvidence: (caseId: string, body: { evidence_type: "transaction" | "transaction_path"; explanation: string; transaction_ids: string[]; source_ref?: string; payload?: Record<string, unknown> }) =>
    call<{ id: string }>(`/cases/${caseId}/evidence`, { method: "POST", body }),
};
