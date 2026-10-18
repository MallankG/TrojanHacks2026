import type { AlertId, AlertSeed } from "@/lib/types";

/**
 * Seeded alerts. AL-1027 fields that depend on the data-quality fix are
 * resolved in `scoreAlerts` (lib/domain/priority.ts).
 */
export const ALERTS: AlertSeed[] = [
  { id: "AL-1027", typology: "Layering, rapid pass-through", amount: 58200, accounts: 7, age: "6 h", sla: "Due in 1 d 18 h", cluster: "CL-17", caseId: "CASE-0412", xgb: 0.71, anomaly: 0.84, typologyScore: 0.67, rulesFired: 2, graphScore: 0.4, ageScore: 0.2, graphText: "Path breaks at ACC-3315 (missing hop)", dataQuality: "Partial: 1 hop on hold" },
  { id: "AL-1029", typology: "Shared-device mule cluster", amount: 9600, accounts: 3, age: "6 h", sla: "Due in 1 d 18 h", cluster: "CL-17", caseId: "CASE-0412", xgb: 0.74, anomaly: 0.62, typologyScore: 0.33, rulesFired: 1, graphScore: 0.9, ageScore: 0.2, graphText: "Shares device DV-77 with ACC-2208", dataQuality: "Complete" },
  { id: "AL-1031", typology: "Funnel, fan-in", amount: 63200, accounts: 14, age: "1 d 2 h", sla: "Due in 22 h", xgb: 0.69, anomaly: 0.71, typologyScore: 0.67, rulesFired: 2, graphScore: 0.7, ageScore: 0.4, graphText: "In-degree 13 within 7 days", dataQuality: "Complete" },
  { id: "AL-1033", typology: "High-volume payroll (benign lookalike)", amount: 212400, accounts: 214, age: "1 d 5 h", sla: "Due in 19 h", xgb: 0.66, anomaly: 0.77, typologyScore: 0.33, rulesFired: 1, graphScore: 0.6, ageScore: 0.5, graphText: "Fan-out to 213 accounts, every 2 weeks", dataQuality: "Complete", benign: true },
  { id: "AL-1035", typology: "Structuring-like cash deposits", amount: 46750, accounts: 1, age: "2 d", sla: "Due in 3 d", xgb: 0.58, anomaly: 0.49, typologyScore: 0.33, rulesFired: 1, graphScore: 0.1, ageScore: 0.6, graphText: "Isolated account, no network", dataQuality: "Late feed: cash batch not received" },
  { id: "AL-1038", typology: "Dormant reactivation, dispersal", amount: 31050, accounts: 7, age: "3 d", sla: "Due in 2 d", xgb: 0.61, anomaly: 0.66, typologyScore: 0.67, rulesFired: 2, graphScore: 0.5, ageScore: 0.8, graphText: "Fan-out to 6 after 240 idle days", dataQuality: "Partial: 1 row on hold" },
  { id: "AL-1040", typology: "Profile and geography mismatch", amount: 12400, accounts: 2, age: "5 d 2 h", sla: "Past internal SLA", xgb: 0.52, anomaly: 0.58, typologyScore: 0.33, rulesFired: 1, graphScore: 0.2, ageScore: 1, graphText: "Low connectivity", dataQuality: "Complete" },
];

export const ALERT_IDS = ALERTS.map((a) => a.id);

export function isAlertId(value: string): value is AlertId {
  return (ALERT_IDS as string[]).includes(value);
}

export const DEFAULT_ALERT: AlertId = "AL-1027";

/** Open-alert totals are synthetic aggregates shown on the command center. */
export const OPEN_ALERT_TOTAL = 128;

export const TYPOLOGY_MIX: Array<[string, number]> = [
  ["Rapid pass-through", 22],
  ["Structuring-like", 19],
  ["Layering, multi-hop", 18],
  ["Funnel, fan-in", 15],
  ["Profile mismatch", 14],
  ["Fan-out, dispersal", 12],
  ["Mule cluster", 11],
  ["Dormant reactivation", 9],
  ["Round trip", 8],
];

export const ALERTS_CREATED_14D = [7, 9, 6, 11, 8, 10, 12, 9, 7, 13, 10, 8, 11, 12];
export const ALERTS_CLOSED_14D = [6, 7, 8, 9, 7, 9, 10, 8, 9, 10, 9, 8, 10, 9];

export const ALERT_AGE_BUCKETS: Array<{ label: string; count: number; pastSla: boolean }> = [
  { label: "Under 1 d", count: 41, pastSla: false },
  { label: "1 to 3 d", count: 52, pastSla: false },
  { label: "3 to 5 d", count: 23, pastSla: false },
  { label: "Over 5 d", count: 12, pastSla: true },
];

export interface ShapContribution {
  feature: string;
  value: string;
  contribution: number;
}

export function shapFor(alertId: AlertId, fixed: boolean): ShapContribution[] {
  if (alertId === "AL-1027") {
    return [
      { feature: "out_in_ratio_24h", value: "0.99", contribution: 0.84 },
      { feature: "median_interarrival_min", value: "108", contribution: 0.61 },
      { feature: "new_counterparty_share_7d", value: "1.00", contribution: 0.47 },
      { feature: "hop_count_from_seed", value: fixed ? "4" : "1", contribution: fixed ? 0.38 : 0.05 },
      { feature: "account_age_days", value: "21", contribution: 0.32 },
      { feature: "amount_vs_baseline_z", value: "6.4", contribution: 0.29 },
      { feature: "prior_alerts_90d", value: "0", contribution: -0.22 },
      { feature: "payee_tenure_days", value: "0", contribution: 0.14 },
      { feature: "source_kyc_tenure_months", value: "26", contribution: -0.09 },
    ];
  }
  if (alertId === "AL-1033") {
    return [
      { feature: "fan_out_count_1h", value: "213", contribution: 0.71 },
      { feature: "amount_sum_24h", value: "212,400", contribution: 0.44 },
      { feature: "periodicity_14d", value: "1.00", contribution: -0.69 },
      { feature: "recipient_overlap_prior_runs", value: "0.98", contribution: -0.58 },
      { feature: "account_age_days", value: "1,640", contribution: -0.31 },
      { feature: "new_counterparty_share_7d", value: "0.01", contribution: -0.27 },
    ];
  }
  return [
    { feature: "velocity_24h", value: "high", contribution: 0.42 },
    { feature: "unique_counterparties_7d", value: "high", contribution: 0.31 },
    { feature: "amount_vs_baseline_z", value: "3.2", contribution: 0.22 },
    { feature: "account_age_days", value: "medium", contribution: -0.12 },
  ];
}
