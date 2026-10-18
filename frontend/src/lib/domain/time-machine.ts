import type { CitedPoint } from "@/lib/domain/alert-insights";
import { formatMoney } from "@/lib/format";
import { EDGES, MAIN_PATH, edgeByKey } from "@/lib/seed/graph";
import type { EdgeKey, GraphEdge } from "@/lib/types";

/**
 * Money-flow time machine (PRD v3.3 section 20.1). A snapshot "as of" an instant contains only the
 * transfers the system had *received* by then, not merely those that had occurred. Every metric,
 * rule result and sentence in a snapshot is computed from that set, so later data cannot leak into
 * an earlier explanation.
 */

const oct = (day: number, hour: number, minute: number) => Date.UTC(2026, 9, day, hour, minute);

/** When each transfer happened (event time). */
const EVENT_AT: Record<EdgeKey, number> = {
  e1: oct(6, 9, 14),
  e2: oct(6, 11, 2),
  e7: oct(6, 12, 10),
  e3: oct(6, 15, 31),
  e4: oct(7, 8, 5),
  e5: oct(7, 10, 20),
  e6: oct(9, 12, 47),
};

/** When the batch carrying the quarantined hop arrived, and when mapping fix v4.1 reloaded it. */
const HELD_FILE_AT = oct(6, 16, 40);
const HELD_RELOAD_AT = oct(9, 14, 4);

/** When each transfer became a canonical record (knowledge time). The held hop depends on the fix. */
function knownAt(key: EdgeKey, fixed: boolean): number | null {
  const at: Record<Exclude<EdgeKey, "e3">, number> = {
    e1: oct(6, 10, 5),
    e2: oct(6, 11, 3),
    e7: oct(6, 12, 11),
    e4: oct(7, 8, 32),
    e5: oct(7, 11, 3),
    e6: oct(9, 13, 21),
  };
  if (key === "e3") return fixed ? HELD_RELOAD_AT : null;
  return at[key];
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function formatInstant(ms: number): string {
  const d = new Date(ms);
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()} ${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`;
}

export interface Checkpoint {
  label: string;
  asOf: number;
  note: string;
}

export function checkpoints(fixed: boolean): Checkpoint[] {
  return [
    { label: "Oct 6 10:05", asOf: oct(6, 10, 5), note: "ACH credit lands at ACC-2208" },
    { label: "Oct 6 11:05", asOf: oct(6, 11, 5), note: "First P2P transfer out" },
    { label: "Oct 6 12:15", asOf: oct(6, 12, 15), note: "Second P2P transfer out" },
    { label: "Oct 6 16:45", asOf: oct(6, 16, 45), note: "ACH batch b arrives and is quarantined" },
    { label: "Oct 7 11:05", asOf: oct(7, 11, 5), note: "Wires to the correspondent are received" },
    { label: "Oct 9 13:40", asOf: oct(9, 13, 40), note: "SR-2291 scores AL-1027 (original alert)" },
    { label: "Oct 9 14:06", asOf: oct(9, 14, 6), note: fixed ? "Mapping fix v4.1 reloads the held hop; SR-2292" : "Data hold still open; no reload yet" },
  ];
}

export type RuleResult = "Fired" | "Not fired" | "Not evaluated" | "Lead only";

export interface PointInTimeSnapshot {
  checkpoint: Checkpoint;
  /** Received and occurred by the as-of instant. */
  visible: EdgeKey[];
  /** Occurred by the as-of instant but not yet received; excluded from every computation. */
  pending: Array<{ edge: GraphEdge; reason: string }>;
  /** The quarantined hop, when its file had arrived but it was not usable. */
  held: EdgeKey | null;
  /** Contiguous main path from ACC-1104 through the seed. */
  linkedPath: EdgeKey[];
  metrics: Array<[string, string]>;
  rules: Array<{ name: string; result: RuleResult; basis: string }>;
  explanation: CitedPoint[];
}

const PASS_THROUGH_RATIO = 0.9;
const LAYERING_HOPS = 4;

export function snapshotAt(checkpoint: Checkpoint, fixed: boolean): PointInTimeSnapshot {
  const t = checkpoint.asOf;
  const visible: EdgeKey[] = [];
  const pending: PointInTimeSnapshot["pending"] = [];
  for (const e of EDGES) {
    if (EVENT_AT[e.key] > t) continue;
    const known = knownAt(e.key, fixed);
    if (known !== null && known <= t) visible.push(e.key);
    else pending.push({ edge: e, reason: e.key === "e3" ? (t >= HELD_FILE_AT ? "In quarantined batch ach_2026_10_06_b" : "Batch file not received yet") : "Not received yet" });
  }
  const has = (k: EdgeKey) => visible.includes(k);
  const held = has("e3") || EVENT_AT.e3 > t || t < HELD_FILE_AT ? null : "e3";

  const linkedPath: EdgeKey[] = [];
  for (const k of MAIN_PATH) {
    if (!has(k)) break;
    linkedPath.push(k);
  }

  const inflow = has("e1") ? edgeByKey("e1").amount : 0;
  const outflow = (["e2", "e7"] as EdgeKey[]).filter(has).reduce((sum, k) => sum + edgeByKey(k).amount, 0);
  const ratio = inflow > 0 ? outflow / inflow : null;
  const exitLinked = linkedPath.includes("e5");
  const cycleLead = has("e6") && exitLinked;

  const metrics: Array<[string, string]> = [
    ["Transfers received", String(visible.length)],
    ["Occurred, not yet received", String(pending.length)],
    ["ACC-2208 in / out (24 h)", inflow > 0 ? `${formatMoney(inflow)} / ${formatMoney(outflow)} (ratio ${ratio?.toFixed(2)})` : "No inflow yet"],
    ["Linked hops from source", `${linkedPath.length} of ${MAIN_PATH.length}`],
    ["Left by cross-border wire", exitLinked ? formatMoney(edgeByKey("e5").amount) : has("e5") ? "Visible, not linked to the seed" : "Not visible"],
    ["Round trip", cycleLead ? "Lead only" : has("e6") ? "Return wire visible, path incomplete" : "None visible"],
  ];

  const rules: PointInTimeSnapshot["rules"] = [
    ratio === null
      ? { name: "Rapid pass-through", result: "Not evaluated", basis: "No inflow received yet" }
      : { name: "Rapid pass-through", result: ratio >= PASS_THROUGH_RATIO ? "Fired" : "Not fired", basis: `Out/in ${ratio.toFixed(2)} within 24 h (fires at ${PASS_THROUGH_RATIO})` },
    linkedPath.length >= LAYERING_HOPS
      ? { name: "Layering, multi-hop", result: "Fired", basis: `${linkedPath.length} linked hops (fires at ${LAYERING_HOPS})` }
      : held
        ? { name: "Layering, multi-hop", result: "Not evaluated", basis: "Path incomplete: hop on hold" }
        : { name: "Layering, multi-hop", result: "Not fired", basis: `${linkedPath.length} linked hops` },
    has("e2") ? { name: "New beneficiary", result: "Fired", basis: "Receiver first seen within 7 d" } : { name: "New beneficiary", result: "Not evaluated", basis: "No outbound transfer yet" },
    cycleLead
      ? { name: "Round trip", result: "Lead only", basis: "Return wire to the source, bounded search" }
      : has("e6")
        ? { name: "Round trip", result: "Not evaluated", basis: "Return wire visible, path incomplete" }
        : { name: "Round trip", result: "Not fired", basis: "No return transfer received" },
  ];

  const explanation: CitedPoint[] = [];
  const say = (text: string, cites: string[]) => explanation.push({ text, cites });
  if (has("e1")) say(`${formatMoney(edgeByKey("e1").amount)} arrived at ACC-2208 from ACC-1104 at Oct 6 09:14.`, ["TX-88201"]);
  if (has("e2")) say(`${formatMoney(edgeByKey("e2").amount)} left to ACC-3315 at Oct 6 11:02, 1 h 48 m later.`, ["TX-88214"]);
  if (has("e7")) say(`${formatMoney(edgeByKey("e7").amount)} left to ACC-3391 at Oct 6 12:10.`, ["TX-88222"]);
  if (held) say("A transfer out of ACC-3315 sits in a quarantined batch. Its values are not used.", ["DQ-1042"]);
  if (exitLinked) say(`The path reaches a cross-border wire of ${formatMoney(edgeByKey("e5").amount)} at Oct 7 10:20.`, ["TX-88237", "TX-88266"]);
  else if (has("e5")) say("Wires from ACC-4471 to EXT-CB-77 are visible but not linked to the seed.", ["TX-88259", "TX-88266"]);
  if (has("e6")) say(`${formatMoney(edgeByKey("e6").amount)} returned to ACC-1104 on Oct 9${cycleLead ? " (round-trip lead only)" : ""}.`, ["TX-88290"]);

  return { checkpoint, visible, pending, held, linkedPath, metrics, rules, explanation };
}

/** Transaction citations in a snapshot that it could not have known about. Should always be empty. */
export function leakageViolations(snapshot: PointInTimeSnapshot): string[] {
  const known = new Set(snapshot.visible.map((k) => edgeByKey(k).tx));
  return snapshot.explanation.flatMap((p) => p.cites).filter((id) => id.startsWith("TX-") && !known.has(id));
}

export interface SnapshotDiff {
  added: Array<{ edge: GraphEdge; late: boolean; note: string }>;
  metricChanges: Array<{ label: string; before: string; after: string }>;
  ruleChanges: Array<{ name: string; before: RuleResult; after: RuleResult }>;
}

/** What B knows that A did not. "Late" means it had already happened at A but was received after A. */
export function diffSnapshots(a: PointInTimeSnapshot, b: PointInTimeSnapshot, fixed: boolean): SnapshotDiff {
  const added = b.visible
    .filter((k) => !a.visible.includes(k))
    .map((k) => {
      const edge = edgeByKey(k);
      const late = EVENT_AT[k] <= a.checkpoint.asOf;
      const known = knownAt(k, fixed);
      const receivedText = known === null ? "not received" : `received ${formatInstant(known)}`;
      return { edge, late, note: late ? `Occurred ${formatInstant(EVENT_AT[k])}, before A, but ${receivedText}` : `Occurred ${formatInstant(EVENT_AT[k])}, ${receivedText}` };
    });
  const metricChanges = b.metrics
    .map(([label, after], i) => ({ label, before: a.metrics[i][1], after }))
    .filter((m) => m.before !== m.after);
  const ruleChanges = b.rules.map((r, i) => ({ name: r.name, before: a.rules[i].result, after: r.result })).filter((r) => r.before !== r.after);
  return { added, metricChanges, ruleChanges };
}
