import { BAND_THRESHOLDS, parseWeights } from "@/lib/domain/priority";
import { BENCHMARK, SUSPICIOUS_TYPOLOGIES, type BenchmarkItem } from "@/lib/seed/benchmark";
import type { WeightKey } from "@/lib/store/state";
import type { PriorityWeights } from "@/lib/types";

/**
 * Policy and threshold what-if sandbox (PRD v3.3 section 20.1). Scores the seeded synthetic benchmark
 * under a proposed configuration and under production, side by side. Nothing here writes policy.
 */

export interface SandboxConfig {
  weights: Record<WeightKey, string>;
  /** Score at or above which a candidate becomes an alert for review. */
  cutoff: string;
  bandMin: string;
  bandMax: string;
  /** Deposits inside the band needed for the structuring-like rule to fire. */
  minDeposits: string;
}

export const STRUCTURING_TYPOLOGY_SCORE = 0.67;

export interface SandboxResult {
  alerts: number;
  reviewFirst: number;
  truePositives: number;
  falsePositives: number;
  precision: number | null;
  recall: number;
  falseAlertsPer100: number;
  coverage: Array<{ typology: string; caught: number; total: number }>;
  falsePositiveExamples: Array<{ id: string; kind: string; score: number }>;
  missedExamples: Array<{ id: string; kind: string; score: number }>;
}

export function configError(config: SandboxConfig): string | null {
  if (!parseWeights(config.weights)) return "Weights must be whole numbers that add up to 100.";
  for (const [label, value] of [
    ["Review cutoff", config.cutoff],
    ["Band minimum", config.bandMin],
    ["Band maximum", config.bandMax],
    ["Deposits in band", config.minDeposits],
  ] as const) {
    if (!/^\d+$/.test(value.trim())) return `${label} must be a whole number.`;
  }
  if (Number(config.cutoff) < 1 || Number(config.cutoff) > 100) return "Review cutoff must be between 1 and 100.";
  if (Number(config.bandMin) >= Number(config.bandMax)) return "The band minimum must be lower than the maximum.";
  if (Number(config.minDeposits) < 1) return "At least 1 deposit in the band is required.";
  return null;
}

function scoreItem(item: BenchmarkItem, weights: PriorityWeights, bandMin: number, bandMax: number, minDeposits: number): number {
  const inBand = item.deposits.filter((d) => d >= bandMin && d <= bandMax).length;
  const typology = inBand >= minDeposits ? Math.max(item.typologyBase, STRUCTURING_TYPOLOGY_SCORE) : item.typologyBase;
  return item.xgb * weights.xgb + typology * weights.typology + item.graph * weights.graph + item.anomaly * weights.anomaly + item.age * weights.age;
}

/** Callers validate with `configError` first; an invalid config returns null. */
export function evaluate(config: SandboxConfig, items: BenchmarkItem[] = BENCHMARK): SandboxResult | null {
  if (configError(config)) return null;
  const weights = parseWeights(config.weights) as PriorityWeights;
  const cutoff = Number(config.cutoff);
  const scoredItems = items.map((item) => ({ item, score: scoreItem(item, weights, Number(config.bandMin), Number(config.bandMax), Number(config.minDeposits)) }));
  const flagged = scoredItems.filter((s) => s.score >= cutoff);
  const truePositives = flagged.filter((s) => s.item.suspicious).length;
  const falsePositives = flagged.length - truePositives;
  const positives = items.filter((i) => i.suspicious).length;
  const coverage = SUSPICIOUS_TYPOLOGIES.map((typology) => {
    const all = scoredItems.filter((s) => s.item.suspicious && s.item.kind === typology);
    return { typology, caught: all.filter((s) => s.score >= cutoff).length, total: all.length };
  });
  const byScore = (a: { score: number }, b: { score: number }) => b.score - a.score;
  const example = (s: { item: BenchmarkItem; score: number }) => ({ id: s.item.id, kind: s.item.kind, score: s.score });
  return {
    alerts: flagged.length,
    reviewFirst: flagged.filter((s) => s.score >= BAND_THRESHOLDS.first).length,
    truePositives,
    falsePositives,
    precision: flagged.length === 0 ? null : truePositives / flagged.length,
    recall: positives === 0 ? 0 : truePositives / positives,
    falseAlertsPer100: (falsePositives / items.length) * 100,
    coverage,
    falsePositiveExamples: flagged.filter((s) => !s.item.suspicious).sort(byScore).slice(0, 5).map(example),
    missedExamples: scoredItems.filter((s) => s.item.suspicious && s.score < cutoff).sort(byScore).slice(0, 5).map(example),
  };
}

export function benchmarkStats(items: BenchmarkItem[] = BENCHMARK): { size: number; positives: number } {
  return { size: items.length, positives: items.filter((i) => i.suspicious).length };
}

export function productionConfig(policy: { weights: Record<WeightKey, string>; bandMin: string; bandMax: string }): SandboxConfig {
  return { weights: { ...policy.weights }, cutoff: String(BAND_THRESHOLDS.next), bandMin: policy.bandMin, bandMax: policy.bandMax, minDeposits: "3" };
}
