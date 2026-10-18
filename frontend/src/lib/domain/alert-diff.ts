import { graphContext, typologySignals, whyFlagged } from "@/lib/domain/alert-insights";
import { scoreAlertsFor, WEIGHT_KEYS } from "@/lib/domain/priority";
import type { AlertRecompute, ScoringInputs } from "@/lib/store/state";
import type { AlertId, CaseStatus, ScoredAlert } from "@/lib/types";

/**
 * Alert diff and decision change log (PRD v3.3 section 20.1). A recompute stores the inputs before
 * and after a change (data fix, policy weights, review band). Both sides are re-scored from those
 * inputs with the same pure functions the queue uses, so the diff cannot drift from the queue.
 */

export interface ValueChange {
  label: string;
  before: string;
  after: string;
}

export interface AlertDiff {
  alertId: AlertId;
  typology: string;
  before: ScoredAlert;
  after: ScoredAlert;
  rankBefore: number;
  rankAfter: number;
  changes: ValueChange[];
  explanationAdded: string[];
  explanationRemoved: string[];
  typologyChanges: ValueChange[];
  changed: boolean;
}

function scored(inputs: ScoringInputs, caseStatus: CaseStatus) {
  return scoreAlertsFor(inputs, caseStatus);
}

function changeList(pairs: Array<[string, string, string]>): ValueChange[] {
  return pairs.filter(([, before, after]) => before !== after).map(([label, before, after]) => ({ label, before, after }));
}

export function diffAlert(alertId: AlertId, record: AlertRecompute, caseStatus: CaseStatus): AlertDiff {
  const beforeList = scored(record.before, caseStatus);
  const afterList = scored(record.after, caseStatus);
  const rankBefore = beforeList.findIndex((a) => a.id === alertId) + 1;
  const rankAfter = afterList.findIndex((a) => a.id === alertId) + 1;
  const before = beforeList[rankBefore - 1];
  const after = afterList[rankAfter - 1];

  const graphBefore = graphContext(before, record.before.driftFixed)[0][1];
  const graphAfter = graphContext(after, record.after.driftFixed)[0][1];
  const changes = changeList([
    ["Priority score", before.score.toFixed(1), after.score.toFixed(1)],
    ["Review band", before.bandLabel, after.bandLabel],
    ["Queue position", `#${rankBefore}`, `#${rankAfter}`],
    ["XGBoost", before.xgb.toFixed(2), after.xgb.toFixed(2)],
    ["Typology rules fired", String(before.rulesFired), String(after.rulesFired)],
    ["Graph signal", before.graphScore.toFixed(2), after.graphScore.toFixed(2)],
    ["Graph path", before.graphText, after.graphText],
    ["Connected component", graphBefore, graphAfter],
    ["Data quality", before.dataQuality, after.dataQuality],
  ]);

  const textsBefore = whyFlagged(before, record.before.driftFixed).map((p) => p.text);
  const textsAfter = whyFlagged(after, record.after.driftFixed).map((p) => p.text);
  const rulesBefore = typologySignals(before, record.before.driftFixed, record.before.bandMin, record.before.bandMax);
  const rulesAfter = typologySignals(after, record.after.driftFixed, record.after.bandMin, record.after.bandMax);
  const typologyChanges = rulesAfter
    .map((rule, i) => {
      const prior = rulesBefore[i];
      return { label: rule.name, before: `${prior.result} · ${prior.parameters}`, after: `${rule.result} · ${rule.parameters}` };
    })
    .filter((c) => c.before !== c.after);

  const explanationAdded = textsAfter.filter((t) => !textsBefore.includes(t));
  const explanationRemoved = textsBefore.filter((t) => !textsAfter.includes(t));
  return {
    alertId,
    typology: after.typology,
    before,
    after,
    rankBefore,
    rankAfter,
    changes,
    explanationAdded,
    explanationRemoved,
    typologyChanges,
    changed: changes.length + explanationAdded.length + explanationRemoved.length + typologyChanges.length > 0,
  };
}

export function diffRecompute(record: AlertRecompute, caseStatus: CaseStatus): AlertDiff[] {
  return scored(record.after, caseStatus).map((a) => diffAlert(a.id, record, caseStatus));
}

/** Version-level changes: mapping, scoring run, graph snapshot, priority policy and review band. */
export function versionChanges(record: AlertRecompute): ValueChange[] {
  const b = record.before;
  const a = record.after;
  return changeList([
    ["Mapping version", b.driftFixed ? "v4.1" : "v4.0", a.driftFixed ? "v4.1" : "v4.0"],
    ["Scoring run", b.driftFixed ? "SR-2292" : "SR-2291", a.driftFixed ? "SR-2292" : "SR-2291"],
    ["Graph snapshot", b.driftFixed ? "GS-0412-b (v184)" : "GS-0412-a (v183)", a.driftFixed ? "GS-0412-b (v184)" : "GS-0412-a (v183)"],
    ["Priority policy", b.policyVersion, a.policyVersion],
    ["Priority weights", WEIGHT_KEYS.map((k) => b.weights[k]).join("/"), WEIGHT_KEYS.map((k) => a.weights[k]).join("/")],
    ["Structuring-like band", `$${b.bandMin} to $${b.bandMax}`, `$${a.bandMin} to $${a.bandMax}`],
  ]);
}

/** Recomputes that changed anything about one alert, newest first. */
export function changesForAlert(records: AlertRecompute[], alertId: AlertId, caseStatus: CaseStatus): Array<{ record: AlertRecompute; diff: AlertDiff }> {
  return records.map((record) => ({ record, diff: diffAlert(alertId, record, caseStatus) })).filter(({ diff }) => diff.changed);
}
