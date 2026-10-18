import { buildNarrative } from "@/lib/domain/case";
import { whyBenign, whyFlagged } from "@/lib/domain/alert-insights";
import { shapFor } from "@/lib/seed/alerts";
import { BAD_ROWS } from "@/lib/seed/data-ops";
import { EDGES, MAIN_PATH, NODES, edgeByKey } from "@/lib/seed/graph";
import { ROLES } from "@/lib/seed/roles";
import type { DemoState } from "@/lib/store/state";
import type { EvidenceItem, ScoredAlert } from "@/lib/types";

/**
 * Evidence passport (PRD v3.3 section 20.1). Same format as backend/app/services/passport.py:
 * each section is hashed with SHA-256 over canonical JSON, and the package hash covers the header
 * plus the section hashes. Amounts travel as decimal strings so both sides canonicalize identically.
 */
export const PASSPORT_FORMAT = "tracelayer.evidence-passport/v1";
export const CANONICALIZATION = "rfc8785-jcs";
export const SIGNATURE_NOTE = "Not signed: key management is not configured for this prototype. Hashes give tamper evidence only.";

export const PASSPORT_SECTIONS = [
  "source_references",
  "data_quality",
  "model",
  "feature_contributions",
  "graph_paths",
  "policy_passages",
  "copilot_claims",
  "human_decisions",
] as const;

export type PassportSection = (typeof PASSPORT_SECTIONS)[number];

export const SECTION_LABELS: Record<PassportSection, string> = {
  source_references: "Source references",
  data_quality: "Data-quality warnings",
  model: "Model and policy versions",
  feature_contributions: "Feature contributions",
  graph_paths: "Graph paths",
  policy_passages: "Policy passages",
  copilot_claims: "Copilot claims",
  human_decisions: "Human decisions",
};

type Json = string | number | boolean | null | Json[] | { [key: string]: Json };

export interface PassportHeader {
  format: string;
  subject: { type: "alert" | "case"; id: string };
  issued_at: string;
  issued_by: string;
}

export interface EvidencePassport extends PassportHeader {
  sections: Record<PassportSection, Json>;
  manifest: {
    algorithm: "sha256";
    canonicalization: string;
    sections: Record<PassportSection, string>;
    package_hash: string;
  };
  signature: null;
  signature_note: string;
}

export interface SectionCheck {
  section: PassportSection;
  status: "match" | "modified" | "missing";
}

export interface PassportVerification {
  valid: boolean;
  reason: string | null;
  sections: SectionCheck[];
  packageHashMatches: boolean;
}

/** RFC 8785 canonical JSON with UTF-16 key ordering and ECMAScript numbers. */
export function canonicalJson(value: unknown): string {
  if (value === null || typeof value === "boolean" || typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("Non-finite numbers cannot be canonicalized");
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map((v) => (v === undefined ? "null" : canonicalJson(v))).join(",")}]`;
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, v]) => v !== undefined)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
    return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${canonicalJson(v)}`).join(",")}}`;
  }
  throw new Error(`Cannot canonicalize ${typeof value}`);
}

export function webCryptoAvailable(): boolean {
  return typeof crypto !== "undefined" && typeof crypto.subtle?.digest === "function";
}

export async function sha256Hex(value: unknown): Promise<string> {
  if (!webCryptoAvailable()) throw new Error("Web Crypto is unavailable. Open the app on https or localhost.");
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonicalJson(value)));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

function headerOf(doc: PassportHeader): PassportHeader {
  return { format: doc.format, subject: doc.subject, issued_at: doc.issued_at, issued_by: doc.issued_by };
}

function packageHash(header: PassportHeader, sectionHashes: Record<string, string | undefined>): Promise<string> {
  return sha256Hex({ ...headerOf(header), sections: sectionHashes });
}

export async function sealPassport(header: PassportHeader, sections: Record<PassportSection, Json>): Promise<EvidencePassport> {
  const hashes = {} as Record<PassportSection, string>;
  for (const name of PASSPORT_SECTIONS) hashes[name] = await sha256Hex(sections[name]);
  return {
    ...headerOf(header),
    sections,
    manifest: { algorithm: "sha256", canonicalization: CANONICALIZATION, sections: hashes, package_hash: await packageHash(header, hashes) },
    signature: null,
    signature_note: SIGNATURE_NOTE,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Recomputes every hash and names the sections that changed. */
export async function verifyPassport(doc: unknown): Promise<PassportVerification> {
  if (!isRecord(doc) || doc.format !== PASSPORT_FORMAT) {
    return { valid: false, reason: `Not a ${PASSPORT_FORMAT} document.`, sections: [], packageHashMatches: false };
  }
  const manifest = isRecord(doc.manifest) ? doc.manifest : {};
  const recorded = (isRecord(manifest.sections) ? manifest.sections : {}) as Record<string, string | undefined>;
  const sections = isRecord(doc.sections) ? doc.sections : {};
  const checks: SectionCheck[] = [];
  for (const name of PASSPORT_SECTIONS) {
    if (!(name in sections)) {
      checks.push({ section: name, status: "missing" });
      continue;
    }
    checks.push({ section: name, status: recorded[name] === (await sha256Hex(sections[name])) ? "match" : "modified" });
  }
  const recordedHashes = Object.fromEntries(PASSPORT_SECTIONS.map((name) => [name, recorded[name]]));
  const packageHashMatches = (await packageHash(doc as unknown as PassportHeader, recordedHashes)) === manifest.package_hash;
  const sectionsOk = checks.every((c) => c.status === "match");
  const reason = !sectionsOk ? "One or more sections changed after the passport was issued." : !packageHashMatches ? "The header or manifest changed after the passport was issued." : null;
  return { valid: sectionsOk && packageHashMatches, reason, sections: checks, packageHashMatches };
}

const money = (amount: number) => amount.toFixed(2);

/** Sections for one alert, built only from evidence already in the case registry. */
export function buildPassportSections(state: DemoState, alert: ScoredAlert, registry: Record<string, EvidenceItem>): Record<PassportSection, Json> {
  const fixed = state.data.driftFixed;
  const inCase = alert.caseId === "CASE-0412";
  const main = alert.id === "AL-1027";
  const pathEdges = main ? EDGES : alert.id === "AL-1029" ? EDGES.filter((e) => e.key === "e7") : [];

  const sourceReferences: Json = pathEdges.map((e) => {
    const held = Boolean(e.held) && !fixed;
    return {
      transaction_id: e.tx,
      source_file: `${e.batch}.csv`,
      source_row: e.row,
      amount: money(e.amount),
      currency: "USD",
      initiated_utc: e.initiated,
      status: held ? "held: quarantined batch, raw row only" : "canonical",
    };
  });

  const dataQuality: Json = {
    alert_status: alert.dataQuality,
    issues: BAD_ROWS.filter((r) => r.alerts.includes(alert.id)).map((r) => {
      const repaired = state.data.repairs[r.row]?.status === "applied";
      const resolved = r.mode === "mapping" ? fixed : repaired;
      return { source_row: r.row, field: r.field, issue: r.type, status: resolved ? "resolved" : "open" };
    }),
    note: "Missing data is not treated as low risk.",
  };

  const model: Json = {
    scoring_run: fixed && main ? "SR-2292" : "SR-2291",
    supervised: { name: "xgb-aml", version: "v1.4.2", score: alert.xgb.toFixed(2), threshold: "0.62", feature_set: "v7" },
    rules: { name: "typology-rules", version: "v3.2", fired: alert.rulesFired, structuring_band: `${state.policy.bandMin}-${state.policy.bandMax}` },
    anomaly: { name: "iforest-anomaly", version: "v0.9", score: alert.anomaly.toFixed(2), use: "shadow" },
    priority: { policy_version: state.policy.version, weights: { ...state.policy.weights }, score: alert.score.toFixed(1), band: alert.bandLabel },
  };

  const contributions: Json = shapFor(alert.id, fixed).map((s) => ({ feature: s.feature, value: s.value, contribution: s.contribution.toFixed(2) }));

  const graphPaths: Json = main
    ? {
        snapshot: fixed ? "GS-0412-b" : "GS-0412-a",
        query: "trace_funds(seed ACC-2208, both, max_hops 5, Oct 6 to 9)",
        hops: MAIN_PATH.map(edgeByKey)
          .filter((e) => fixed || !e.held)
          .map((e) => ({ transaction_id: e.tx, from: NODES[e.from].id, to: NODES[e.to].id, amount: money(e.amount), initiated_utc: e.initiated })),
        breaks_at: fixed ? null : "ACC-3315 (next hop in quarantined batch, DQ-1042)",
        caveat: "Investigative context, not proof. Funds in shared balances are mixed.",
      }
    : { snapshot: null, hops: [], breaks_at: null, caveat: "No graph path in case scope for this alert." };

  const policyPassages: Json = ["POL-FFIEC-SAR", "POL-FINCEN-ADV"].map((id) => {
    const item = registry[id];
    const field = (name: string) => item?.fields.find(([k]) => k === name)?.[1] ?? null;
    return { evidence_id: id, title: item?.title ?? id, source: field("Source"), chunk: field("Chunk"), quote: item?.quote ?? null };
  });

  const revision = state.caseWork.revisions[0].revision;
  const copilotClaims: Json = inCase
    ? {
        narrative_revision: `r${revision}`,
        claims: buildNarrative(state).flatMap((section) => section.claims.map((c) => ({ section: section.heading, text: c.text, status: c.status, cites: c.cites }))),
      }
    : {
        narrative_revision: null,
        claims: [],
        explanation: [...whyFlagged(alert, fixed), ...whyBenign(alert)].map((p) => ({ text: p.text, cites: p.cites })),
      };

  const humanDecisions: Json = inCase
    ? {
        case: "CASE-0412",
        status: state.caseWork.status,
        disposition: state.caseWork.disposition,
        rationale: state.caseWork.rationale.trim() || null,
        escalated_by: state.caseWork.escalatedBy ? ROLES[state.caseWork.escalatedBy].name : null,
        revisions: state.caseWork.revisions.map((r) => ({ revision: `r${r.revision}`, by: r.by, note: r.note })),
        notes: state.caseWork.notes.map((n) => ({ by: n.by, at: n.at, text: n.text })),
      }
    : { case: null, status: "Not in a case", disposition: null, rationale: null, escalated_by: null, revisions: [], notes: [] };

  return {
    source_references: sourceReferences,
    data_quality: dataQuality,
    model,
    feature_contributions: contributions,
    graph_paths: graphPaths,
    policy_passages: policyPassages,
    copilot_claims: copilotClaims,
    human_decisions: humanDecisions,
  };
}

/** Returns a copy with one amount changed, for demonstrating tamper detection. */
export function tamperedCopy(doc: EvidencePassport): EvidencePassport {
  const copy = JSON.parse(JSON.stringify(doc)) as EvidencePassport;
  const refs = copy.sections.source_references;
  if (Array.isArray(refs) && refs.length > 0 && isRecord(refs[0])) {
    (refs[0] as Record<string, Json>).amount = "158200.00";
  } else {
    copy.sections.human_decisions = { status: "Closed, no further action (edited)" };
  }
  return copy;
}
