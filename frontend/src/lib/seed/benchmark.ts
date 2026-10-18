/**
 * Synthetic labeled benchmark for the what-if sandbox. Generated from a fixed seed so every run and
 * every viewer sees the same 600 candidates. Labels are synthetic ground truth, not real outcomes.
 */

export const BENCHMARK_SEED = 2026;
export const BENCHMARK_SIZE = 600;

export const SUSPICIOUS_TYPOLOGIES = ["Rapid pass-through", "Layering, multi-hop", "Funnel, fan-in", "Mule cluster", "Structuring-like", "Dormant reactivation"] as const;
export const BENIGN_KINDS = ["Payroll run", "Treasury sweep", "Cash-heavy retail", "Household transfers", "Seasonal supplier"] as const;

export interface BenchmarkItem {
  id: string;
  suspicious: boolean;
  /** Typology for suspicious items, benign lookalike kind otherwise. */
  kind: string;
  xgb: number;
  anomaly: number;
  /** Typology score before the structuring-like rule is applied. */
  typologyBase: number;
  graph: number;
  age: number;
  /** Cash deposits in the window; evaluated by the structuring-like review band. */
  deposits: number[];
}

/** mulberry32: small, fast, deterministic PRNG. */
function prng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const clamp = (v: number) => Math.max(0, Math.min(1, v));

function generate(): BenchmarkItem[] {
  const rand = prng(BENCHMARK_SEED);
  const around = (center: number, spread: number) => clamp(center + (rand() + rand() + rand() - 1.5) * spread);
  const pick = <T,>(list: readonly T[]) => list[Math.floor(rand() * list.length)];
  const items: BenchmarkItem[] = [];
  for (let i = 0; i < BENCHMARK_SIZE; i++) {
    const suspicious = rand() < 0.14;
    const kind = suspicious ? pick(SUSPICIOUS_TYPOLOGIES) : pick(BENIGN_KINDS);
    // Benign lookalikes (payroll, sweeps) score high on graph and anomaly signals on purpose.
    const lookalike = kind === "Payroll run" || kind === "Treasury sweep";
    const graphHeavy = kind === "Layering, multi-hop" || kind === "Mule cluster" || kind === "Funnel, fan-in";
    const deposits: number[] = [];
    if (kind === "Structuring-like") {
      const n = 3 + Math.floor(rand() * 4);
      // Most deposits sit just under $10,000; some sit lower to show what a narrow band misses.
      const low = rand() < 0.35;
      for (let d = 0; d < n; d++) deposits.push(Math.round((low ? 7000 + rand() * 1000 : 8200 + rand() * 1750) / 10) * 10);
    } else if (kind === "Cash-heavy retail") {
      const n = 2 + Math.floor(rand() * 5);
      for (let d = 0; d < n; d++) deposits.push(Math.round((1500 + rand() * 11000) / 10) * 10);
    }
    items.push({
      id: `BM-${String(i + 1).padStart(4, "0")}`,
      suspicious,
      kind,
      xgb: suspicious ? around(kind === "Structuring-like" ? 0.6 : 0.7, 0.3) : around(lookalike ? 0.55 : 0.32, 0.35),
      anomaly: suspicious ? around(0.65, 0.4) : around(lookalike ? 0.72 : 0.4, 0.4),
      typologyBase: suspicious ? (kind === "Structuring-like" ? 0.2 : around(0.62, 0.4)) : around(lookalike ? 0.4 : 0.18, 0.3),
      graph: suspicious ? around(graphHeavy ? 0.78 : 0.42, 0.35) : around(lookalike ? 0.62 : 0.25, 0.35),
      age: around(0.45, 0.8),
      deposits,
    });
  }
  return items;
}

export const BENCHMARK: BenchmarkItem[] = generate();
