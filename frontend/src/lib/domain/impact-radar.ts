import { formatMoney } from "@/lib/format";
import { ROUTES } from "@/lib/routes";
import { ALERTS, isAlertId } from "@/lib/seed/alerts";
import { BAD_ROWS, HELD_BATCH } from "@/lib/seed/data-ops";
import type { DemoState } from "@/lib/store/state";
import type { BadRowSeed, ScoredAlert, Tone } from "@/lib/types";

/**
 * Data-quality impact radar (PRD v3.3 section 20.1). From one late, malformed, missing or corrected
 * source record, walks the recorded dependencies outward (features and entity links, then alerts and
 * models, then cases and reports) and estimates the blast radius. It is an estimate: untracked
 * lineage steps are listed rather than assumed away.
 */

export type ImpactCategory = "Feature" | "Entity or graph link" | "Alert" | "Model" | "Case" | "Report";
export type IssueKind = "Malformed" | "Missing" | "Late" | "Corrected" | "Duplicate";

export const RING_OF: Record<ImpactCategory, 1 | 2 | 3> = {
  Feature: 1,
  "Entity or graph link": 1,
  Alert: 2,
  Model: 2,
  Case: 3,
  Report: 3,
};

export interface ImpactSource {
  key: string;
  label: string;
  kind: IssueKind;
  batch: string;
  field: string;
  detail: string;
  resolved: boolean;
}

export interface ImpactItem {
  id: string;
  category: ImpactCategory;
  label: string;
  status: string;
  tone: Tone;
  lineage: string[];
  href?: string;
}

export interface BlastRadius {
  level: "High" | "Medium" | "Low" | "None";
  tone: Tone;
  summary: string;
  facts: Array<[string, string]>;
  untracked: string[];
}

const LATE_CASH = "late-cash";

const MODELS_BY_FIELD: Record<string, string[]> = {
  target_account: ["xgb-aml v1.4.2 · monitoring window (target_account_missing_rate)"],
  transaction_id: ["iforest-anomaly v0.9 · velocity baseline"],
  posted_at: ["typology-rules v3.2 · inter-arrival detectors"],
  amount: ["iforest-anomaly v0.9 · amount baseline"],
};

function kindOf(row: BadRowSeed): IssueKind {
  if (row.mode === "auto") return "Corrected";
  if (row.type.startsWith("Duplicate")) return "Duplicate";
  if (row.type.startsWith("Missing") || row.type.startsWith("Unknown")) return "Missing";
  return "Malformed";
}

function rowResolved(row: BadRowSeed, state: DemoState): boolean {
  if (row.mode === "mapping") return state.data.driftFixed;
  if (row.mode === "auto") return true;
  return state.data.repairs[row.row]?.status === "applied";
}

export function impactSources(state: DemoState): ImpactSource[] {
  const rows = BAD_ROWS.map(
    (row): ImpactSource => ({
      key: String(row.row),
      label: `Row ${row.row} · ${row.type}`,
      kind: kindOf(row),
      batch: HELD_BATCH,
      field: row.field,
      detail: row.original,
      resolved: rowResolved(row, state),
    }),
  );
  return [...rows, { key: LATE_CASH, label: "cash_2026_10_09 · feed not received", kind: "Late", batch: "cash_2026_10_09", field: "(whole batch)", detail: "Branch cash feed is 3 h late against a 2 h SLA", resolved: false }];
}

function statusFor(category: ImpactCategory, resolved: boolean): [string, Tone] {
  if (resolved) {
    const done: Record<ImpactCategory, string> = { Feature: "Re-computed", "Entity or graph link": "Restored", Alert: "Re-scored", Model: "Current", Case: "Evidence updated", Report: "Regenerate to include fix" };
    return [done[category], category === "Report" ? "info" : "ok"];
  }
  const open: Record<ImpactCategory, string> = { Feature: "Stale", "Entity or graph link": "Missing", Alert: "Stale", Model: "Watch", Case: "Evidence partial", Report: "May be stale" };
  return [open[category], "warn"];
}

export function impactItems(state: DemoState, source: ImpactSource): ImpactItem[] {
  const row = BAD_ROWS.find((r) => String(r.row) === source.key);
  const features = row ? row.features : ["cash_deposit_count_7d · ACC-7702", "amount_sum_24h · ACC-7702"];
  const links = row ? row.links : [];
  const alerts = row ? row.alerts : ["AL-1035"];
  const cases = row ? row.cases : [];
  const models = row ? (MODELS_BY_FIELD[row.field] ?? []) : ["typology-rules v3.2 · structuring-like detector"];

  const origin = row ? [`${source.batch}.csv`, `Raw row ${row.row}`, row.mode === "mapping" && !source.resolved ? "No canonical record" : "Canonical record"] : [`${source.batch}.csv`, "Not received"];
  const firstFeature = features[0];
  const items: ImpactItem[] = [];
  const add = (category: ImpactCategory, label: string, via: string[], href?: string) => {
    const [status, tone] = statusFor(category, source.resolved);
    items.push({ id: `${category}:${label}`, category, label, status, tone, lineage: [...origin, ...via, label], href });
  };

  features.forEach((f) => add("Feature", f, [], ROUTES.governance()));
  links.forEach((l) => add("Entity or graph link", l, [], ROUTES.graph));
  alerts.forEach((a) => add("Alert", a, firstFeature ? [firstFeature] : [], isAlertId(a) ? ROUTES.riskAlert(a) : undefined));
  models.forEach((m) => add("Model", m, firstFeature ? [firstFeature] : [], ROUTES.governance("models")));
  cases.forEach((c) => add("Case", c, alerts.length ? [alerts[0]] : [], ROUTES.case(c)));
  if (cases.includes("CASE-0412")) {
    const via = alerts.length ? [alerts[0], "CASE-0412"] : ["CASE-0412"];
    add("Report", `Narrative r${state.caseWork.revisions[0].revision}`, via, ROUTES.case("CASE-0412", "narrative"));
    state.exports.filter((x) => x.caseId === "CASE-0412").forEach((x) => add("Report", `Evidence bundle ${x.id}`, via, ROUTES.case("CASE-0412", "report")));
  }
  return items;
}

export function blastRadius(source: ImpactSource, items: ImpactItem[], scored: ScoredAlert[]): BlastRadius {
  const count = (c: ImpactCategory) => items.filter((i) => i.category === c).length;
  const alertIds = items.filter((i) => i.category === "Alert").map((i) => i.label);
  const amount = ALERTS.filter((a) => alertIds.includes(a.id)).reduce((sum, a) => sum + a.amount, 0);
  const affected = scored.filter((a) => alertIds.includes(a.id));
  const topBand = affected.length ? affected.reduce((best, a) => (a.band < best.band ? a : best)) : null;
  const untracked = source.field === "target_account" ? ["Legacy memo parser reads this field with no lineage edge"] : [];

  let level: BlastRadius["level"] = "Low";
  if (source.resolved) level = "None";
  else if (count("Case") > 0 || topBand?.band === 1) level = "High";
  else if (alertIds.length > 0 || count("Model") > 0) level = "Medium";
  const tone: Tone = level === "High" ? "bad" : level === "Medium" ? "warn" : level === "Low" ? "info" : "ok";

  const parts = (["Feature", "Entity or graph link", "Alert", "Model", "Case", "Report"] as ImpactCategory[])
    .map((c) => [c, count(c)] as const)
    .filter(([, n]) => n > 0)
    .map(([c, n]) => `${n} ${c.toLowerCase()}${n === 1 ? "" : "s"}`);
  const summary = source.resolved
    ? `Resolved. ${parts.length ? `${parts.join(", ")} were updated; regenerate any report created before the fix.` : "Nothing downstream depended on it."}`
    : parts.length
      ? `Estimated ${parts.join(", ")} may be affected.`
      : "No recorded downstream dependency.";

  return {
    level,
    tone,
    summary,
    facts: [
      ["Issue", `${source.kind} · ${source.field}`],
      ["Alerted amount exposed", alertIds.length ? formatMoney(amount) : "None"],
      ["Highest review band affected", topBand ? `${topBand.bandLabel} (${topBand.id})` : "None"],
      ["Lineage coverage", untracked.length ? `${untracked.length} untracked step, so the radius may be larger` : "All recorded steps linked"],
    ],
    untracked,
  };
}
