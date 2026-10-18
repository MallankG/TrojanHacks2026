import { simulatedHash } from "@/lib/format";
import type { DemoState } from "@/lib/store/state";
import type { Tone } from "@/lib/types";

/**
 * One-click investigation replay (PRD v3.3 section 20.1). Re-runs the same bounded tools against the
 * data, graph, feature, model and policy versions pinned by a past run. A step that cannot reproduce
 * its output is reported as non-deterministic or unavailable; the replay never claims to be exact
 * unless every step reproduced.
 */

export type ReplayPin = "SR-2291" | "SR-2292";

export interface PinnedRun {
  pin: ReplayPin;
  label: string;
  at: string;
  available: boolean;
  unavailableReason?: string;
  versions: Array<[string, string]>;
}

/** Policy version and band in force when SR-2291 ran; replays use these, not the current settings. */
const PINNED_POLICY = { version: "v3.1", band: "$8000 to $9999" };

export function pinnedRuns(state: DemoState): PinnedRun[] {
  const fixed = state.data.driftFixed;
  const reloaded = fixed || state.recomputes.some((r) => r.after.driftFixed);
  return [
    {
      pin: "SR-2291",
      label: "SR-2291 · original alert",
      at: "Oct 9, 13:40 UTC",
      available: true,
      versions: [
        ["Data snapshot", "ach_2026_10_06_b quarantined · mapping v4.0"],
        ["Graph snapshot", "GS-0412-a · graph v183"],
        ["Feature set", "FS-7-20261009 · as-of Oct 9 13:40 UTC"],
        ["Model and rules", "xgb-aml v1.4.2 · typology-rules v3.2 · iforest v0.9"],
        ["Priority policy", `${PINNED_POLICY.version} · band ${PINNED_POLICY.band}`],
        ["Narrative", "r1 · prompt narrative_v3"],
      ],
    },
    {
      pin: "SR-2292",
      label: "SR-2292 · after mapping fix",
      at: "Oct 9, 14:06 UTC",
      available: reloaded,
      unavailableReason: reloaded ? undefined : "SR-2292 does not exist yet. Apply mapping fix v4.1 in Data operations first.",
      versions: [
        ["Data snapshot", "ach_2026_10_06_b reloaded · mapping v4.1 (derived version kept after rollback)"],
        ["Graph snapshot", "GS-0412-b · graph v184"],
        ["Feature set", "FS-7-20261009 · as-of Oct 9 14:05 UTC"],
        ["Model and rules", "xgb-aml v1.4.2 · typology-rules v3.2 · iforest v0.9"],
        ["Priority policy", `${PINNED_POLICY.version} · band ${PINNED_POLICY.band}`],
        ["Narrative", "Latest revision · prompt narrative_v3"],
      ],
    },
  ];
}

export type StepOutcome = "Reproduced" | "Non-deterministic" | "Unavailable";

export interface ReplayStep {
  name: string;
  tool: string;
  outcome: StepOutcome;
  original: string;
  replayed: string;
  note: string;
}

export function outcomeTone(outcome: StepOutcome): Tone {
  if (outcome === "Reproduced") return "ok";
  if (outcome === "Non-deterministic") return "warn";
  return "bad";
}

function digest(pin: ReplayPin, step: string, output: string, salt = ""): string {
  return simulatedHash(`${pin}|${step}|${output}${salt}`, 12);
}

export function replaySteps(state: DemoState, pin: ReplayPin): ReplayStep[] {
  const post = pin === "SR-2292";
  const graphDown = state.sim.graph;
  const llmDown = state.sim.llm;
  const policyDrift = state.policy.version !== PINNED_POLICY.version || `$${state.policy.bandMin} to $${state.policy.bandMax}` !== PINNED_POLICY.band;

  const same = (name: string, tool: string, output: string, note: string): ReplayStep => {
    const hash = digest(pin, name, output);
    return { name, tool, outcome: "Reproduced", original: hash, replayed: hash, note };
  };

  const steps: ReplayStep[] = [
    same("Load pinned data snapshot", "load_snapshot(batch hashes)", post ? "18212 accepted" : "0 accepted, quarantined", post ? "Reads the derived v4.1 version, not the live feed." : "Reads the quarantined batch as it was, even if it was fixed later."),
    same("Entity resolution", "resolve_entities(v2, exact tokens)", "1 candidate held", "Deterministic matching rules; candidates still need human review."),
    graphDown
      ? { ...same("Trace funds", "trace_funds(ACC-2208, both, 5 hops, Oct 6 to 9)", post ? "8 nodes 8 edges" : "4 nodes 3 edges", ""), note: `GraphBase is unreachable, so the saved snapshot ${post ? "GS-0412-b" : "GS-0412-a"} was read instead. Its hash matches.` }
      : same("Trace funds", "trace_funds(ACC-2208, both, 5 hops, Oct 6 to 9)", post ? "8 nodes 8 edges" : "4 nodes 3 edges", "Bounded, allowlisted graph query against the pinned graph version."),
    same("Point-in-time features", "features(FS-7, as_of)", post ? "as-of 14:05" : "as-of 13:40", "Feature snapshots are versioned, not overwritten."),
    same("Supervised score", "score(xgb-aml v1.4.2)", post ? "0.91" : "0.71", `AL-1027 scored ${post ? "0.91" : "0.71"}. The artifact checksum matches.`),
    same("Typology rules", "rules(v3.2)", post ? "3 fired" : "2 fired", "Uses the pinned review band, not the current one."),
    same("Anomaly score", "iforest(v0.9, seed 7)", "0.84", "Fixed random seed, so the score is repeatable."),
    same(
      "Priority policy",
      `priority(${PINNED_POLICY.version})`,
      post ? "Review first" : "Review next",
      policyDrift ? `Pinned ${PINNED_POLICY.version} was used. The current policy (${state.policy.version}, band $${state.policy.bandMin} to $${state.policy.bandMax}) differs and was ignored.` : "Pinned policy matches the current policy.",
    ),
    {
      name: "Policy retrieval",
      tool: "policy_search(FFIEC, FinCEN)",
      outcome: "Unavailable",
      original: digest(pin, "rag", "POL-FFIEC-SAR-03,POL-FINCEN-11"),
      replayed: "n/a",
      note: "The policy index snapshot was not pinned when the run happened, so retrieval cannot be replayed as it was. The original chunk IDs are kept in the case.",
    },
    llmDown
      ? { name: "Narrative draft", tool: "draft_narrative(narrative_v3)", outcome: "Unavailable", original: digest(pin, "llm", "draft"), replayed: "n/a", note: "The LLM is unavailable. The stored draft is kept unchanged." }
      : {
          name: "Narrative draft",
          tool: "draft_narrative(narrative_v3)",
          outcome: "Non-deterministic",
          original: digest(pin, "llm", "draft"),
          replayed: digest(pin, "llm", "draft", "|replay"),
          note: "LLM wording varies between runs, so the text differs. The set of verified claims and cited IDs matches the original.",
        },
    same("Claim verifier", "verify_claims(evidence registry)", post && state.caseWork.claimFixed ? "0 blocked" : "1 blocked", "Deterministic checks of IDs, amounts and dates against stored evidence."),
  ];
  return steps;
}

export function replaySummary(steps: ReplayStep[]): { reproduced: number; nonDeterministic: number; unavailable: number; exact: boolean; text: string } {
  const reproduced = steps.filter((s) => s.outcome === "Reproduced").length;
  const nonDeterministic = steps.filter((s) => s.outcome === "Non-deterministic").length;
  const unavailable = steps.filter((s) => s.outcome === "Unavailable").length;
  const exact = nonDeterministic === 0 && unavailable === 0;
  const text = exact
    ? `Exact replay: all ${reproduced} steps reproduced their recorded outputs.`
    : `Partial replay: ${reproduced} of ${steps.length} steps reproduced, ${nonDeterministic} non-deterministic, ${unavailable} unavailable. This is not an exact replay.`;
  return { reproduced, nonDeterministic, unavailable, exact, text };
}
