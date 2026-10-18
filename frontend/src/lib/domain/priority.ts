import { ALERTS } from "@/lib/seed/alerts";
import { DEFAULT_WEIGHTS, type DemoState, type WeightKey } from "@/lib/store/state";
import type { AlertSeed, PriorityBand, PriorityWeights, ScoredAlert, Tone } from "@/lib/types";

export const WEIGHT_KEYS: WeightKey[] = ["xgb", "typology", "graph", "anomaly", "age"];

export const WEIGHT_LABELS: Record<WeightKey, string> = {
  xgb: "XGBoost",
  typology: "Typology",
  graph: "Graph",
  anomaly: "Anomaly",
  age: "Age and SLA",
};

export const BAND_THRESHOLDS = { first: 75, next: 55 } as const;

/** Weights are valid when every value is a non-negative whole number and they sum to 100. */
export function parseWeights(raw: Record<WeightKey, string>): PriorityWeights | null {
  const parsed = WEIGHT_KEYS.map((k) => (/^\d+$/.test(raw[k].trim()) ? Number(raw[k]) : NaN));
  if (parsed.some((v) => Number.isNaN(v))) return null;
  if (parsed.reduce((a, b) => a + b, 0) !== 100) return null;
  const [xgb, typology, graph, anomaly, age] = parsed;
  return { xgb, typology, graph, anomaly, age };
}

export function weightSum(raw: Record<WeightKey, string>): number {
  return WEIGHT_KEYS.reduce((sum, k) => sum + (Number(raw[k]) || 0), 0);
}

function bandFor(score: number): { band: PriorityBand; label: string; tone: Tone } {
  if (score >= BAND_THRESHOLDS.first) return { band: 1, label: "Review first", tone: "dark" };
  if (score >= BAND_THRESHOLDS.next) return { band: 2, label: "Review next", tone: "blue" };
  return { band: 3, label: "Review when able", tone: "info" };
}

/** Applies data-quality-dependent values for the seeded alerts. */
function resolveSeed(seed: AlertSeed, fixed: boolean): AlertSeed {
  if (seed.id === "AL-1027" && fixed) {
    return { ...seed, xgb: 0.91, typologyScore: 1, rulesFired: 3, graphScore: 0.8, graphText: "Cycle member, 2-hop exposure 7 accounts", dataQuality: "Complete" };
  }
  if (seed.id === "AL-1038" && fixed) return { ...seed, dataQuality: "Complete" };
  return seed;
}

export function scoreAlerts(state: DemoState): ScoredAlert[] {
  const weights = parseWeights(state.policy.weights) ?? (parseWeights(DEFAULT_WEIGHTS) as PriorityWeights);
  const fixed = state.data.driftFixed;
  return ALERTS.map((seed): ScoredAlert => {
    const a = resolveSeed(seed, fixed);
    const parts = [
      { label: "XGBoost", value: a.xgb, weight: weights.xgb },
      { label: "Typology rules", value: a.typologyScore, weight: weights.typology },
      { label: "Graph signals", value: a.graphScore, weight: weights.graph },
      { label: "Anomaly", value: a.anomaly, weight: weights.anomaly },
      { label: "Age and SLA", value: a.ageScore, weight: weights.age },
    ].map((p) => ({ ...p, points: p.value * p.weight }));
    const score = parts.reduce((sum, p) => sum + p.points, 0);
    const band = bandFor(score);
    const inCase = a.caseId === "CASE-0412";
    const status = inCase ? (state.caseWork.status === "In review" ? "In CASE-0412" : `CASE-0412: ${state.caseWork.status}`) : "New";
    return {
      ...a,
      parts,
      score,
      band: band.band,
      bandLabel: band.label,
      bandTone: band.tone,
      dataQualityTone: a.dataQuality === "Complete" ? "ok" : "warn",
      status,
    };
  }).sort((x, y) => y.score - x.score);
}
