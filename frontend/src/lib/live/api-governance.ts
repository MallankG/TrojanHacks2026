"use client";

import { call, query } from "./api";

/** Governance, model and audit center endpoints (read-only aggregates). */

export interface LineageStep {
  key: string;
  title: string;
  linked: boolean;
  count: number;
  items: Array<Record<string, unknown>>;
  note: string | null;
}

export interface LineageLookup {
  ref: string;
  parsed: string;
  matches: Array<{ kind: string; id: string; label: string; detail: string }>;
  selected: { kind: string; id: string; label: string; detail: string } | null;
  chain: LineageStep[];
}

export interface RegistryModel {
  model_name: string;
  model_version: string;
  model_kind: string;
  status: string;
  run_count: number;
  completed_run_count: number;
  dataset_version: string;
  code_version: string | null;
  config: Record<string, unknown>;
  metrics: Record<string, unknown>;
  headline_metrics: Record<string, unknown>;
  last_completed_at?: string | null;
}

export interface OfflineCard {
  artifact_set: string;
  source: string;
  metrics_are_demonstration: boolean;
  card: Record<string, unknown> | null;
  card_error: string | null;
}

export interface PipelineItem {
  kind: string;
  id: string;
  name: string;
  version: string | null;
  model_kind: string | null;
  status: string;
  started_at: string;
  completed_at: string | null;
  duration_seconds: number | null;
  rows: Record<string, number | null>;
  error_message: string | null;
}

export interface HealthRun {
  run_id: string;
  filename: string;
  status: string;
  started_at: string;
  row_count: number;
  rejected_count: number;
  warning_count: number;
  rejected_rate: number;
  warning_rate: number;
  issue_count: number;
}

export interface DataHealth {
  runs: HealthRun[];
  rejected_rate_trend: { status: string; runs_available: number; baseline_rejected_rate: number | null; latest_rejected_rate: number | null; change: number | null };
  issue_totals: Record<string, number>;
}

export interface ExportItem {
  event_id: string;
  case_id: string;
  case_number: number;
  case_title: string;
  path: string;
  sha256: string | null;
  manifest_sha256: string | null;
  occurred_at: string;
  actor_name: string | null;
  actor_role: string | null;
}

export interface DetectorRun {
  model_run_id: string;
  model_name: string;
  model_version: string;
  model_kind: string;
  dataset_version: string;
  completed_at: string | null;
  metrics: Record<string, unknown>;
  metrics_are_demonstration: boolean;
  signal_count: number;
  triggered_count: number;
  typologies: Record<string, number>;
  alert_count: number;
}

export const governanceApi = {
  lookup: (ref: string) => call<LineageLookup>(`/lineage/lookup${query({ ref })}`),
  registry: () => call<{ models: RegistryModel[]; run_count: number; offline_model_cards: OfflineCard[] }>("/models/registry"),
  pipelines: () => call<{ items: PipelineItem[]; summary: Record<string, Record<string, number>> }>("/governance/pipelines"),
  health: () => call<DataHealth>("/governance/health"),
  exports: () => call<{ items: ExportItem[]; hashed_count: number }>("/governance/exports"),
  detectors: () =>
    call<{ detectors: DetectorRun[]; typologies: string[]; overlap: Record<string, number>; offline_model_cards: OfflineCard[] }>("/governance/detectors"),
};
