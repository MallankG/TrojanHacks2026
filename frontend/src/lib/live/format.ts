import type { Tone } from "@/lib/types";

/** Formatting for live backend data. Amounts arrive as decimal strings and stay exact until display. */

export function money(amount: string | number | null | undefined, currency = "USD"): string {
  if (amount === null || amount === undefined || amount === "") return "Not recorded";
  const value = typeof amount === "number" ? amount : Number(amount);
  if (!Number.isFinite(value)) return String(amount);
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency, minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);
  } catch {
    return `${value.toFixed(2)} ${currency}`;
  }
}

export function utc(iso: string | null | undefined): string {
  if (!iso) return "Not recorded";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return `${date.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

export function shortId(id: string | null | undefined): string {
  return id ? id.slice(0, 8) : "None";
}

export function words(value: string | null | undefined): string {
  if (!value) return "None";
  const text = value.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function alertLabel(alertNumber: number): string {
  return `AL-${alertNumber}`;
}

export function caseLabel(caseNumber: number): string {
  return `CASE-${String(caseNumber).padStart(4, "0")}`;
}

export const BAND_TONE: Record<string, Tone> = { critical: "bad", high: "warn", medium: "info", low: "mute" };
export const BAND_LABEL: Record<string, string> = { critical: "Review first", high: "Review first", medium: "Review next", low: "Review when able" };

export const SEVERITY_TONE: Record<string, Tone> = { critical: "bad", error: "bad", warning: "warn", info: "info" };

export const RUN_TONE: Record<string, Tone> = { completed: "ok", completed_with_errors: "warn", failed: "bad", running: "info", pending: "mute" };

export const CASE_TONE: Record<string, Tone> = { open: "info", in_review: "info", pending_review: "warn", escalated: "bad", closed: "ok" };

// Display helpers keep the recorded value visible: they only make it readable, never fill in or upgrade it
// (a governance screen must not show a version, owner, source or verification that was not recorded).

const WORKFLOWS: Record<string, string> = {
  case_workflow: "Case Investigation Agent",
  copilot_workflow: "Investigation Copilot",
  analysis_workflow: "Batch AML Analysis Pipeline",
};

/** 'ingest-pipeline-1.0.0+dq-rules-1.0.0' -> 'Ingestion pipeline 1.0.0 · DQ rules 1.0.0'; other names unchanged. */
export function friendlyPipeline(name: string | null | undefined): string {
  if (!name) return "Not recorded";
  if (WORKFLOWS[name]) return WORKFLOWS[name];
  const parts = name.split("+").map((p) => {
    const m = /^(ingest-pipeline|dq-rules)-(.+)$/.exec(p);
    return m ? `${m[1] === "dq-rules" ? "DQ rules" : "Ingestion pipeline"} ${m[2]}` : p;
  });
  return parts.join(" · ");
}

/** 'canonical-txn-v1' -> 'Canonical transactions (canonical-txn-v1)'; the recorded version stays visible. */
export function friendlySchema(schema: string | null | undefined): string {
  if (!schema) return "Not recorded";
  return schema.startsWith("canonical") ? `Canonical transactions (${schema})` : schema;
}

/** A stored checksum, shortened. It is what was recorded at upload, not a fresh verification. */
export function friendlyHash(hash: string | null | undefined): string {
  if (!hash) return "Not recorded";
  return `SHA-256 ${hash.length > 12 ? `${hash.slice(0, 6)}…${hash.slice(-4)}` : hash}`;
}

export function friendlySystem(sys: string | null | undefined): string {
  return sys ? words(sys) : "Not recorded";
}

export function friendlyOwner(owner: string | null | undefined): string {
  return owner || "Not recorded";
}

/** Human-friendly file size */
export function friendlyFileSize(bytes: number | null | undefined): string {
  if (bytes == null) return "Not recorded";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

/** Human-friendly model name */
export function friendlyModel(name: string | null | undefined): string {
  if (!name) return "Not recorded";
  return MODEL_NAMES[name] ?? WORKFLOWS[name] ?? words(name);
}

const MODEL_NAMES: Record<string, string> = {
  "isolation_forest": "Isolation Forest Anomaly Detector",
  "xgboost": "XGBoost AML Classifier",
  "gnn": "Graph Neural Network (GNN)",
  "rules": "Regulatory Typology Rules",
  "fusion": "Multi-Model Risk Fusion",
  "template": "Deterministic template (no model)",
  "qwen3.5:0.8b": "Qwen 3.5 AI Copilot",
  "qwen3.5:9b": "Qwen 3.5 9B AML Model",
  "nomic-embed-text": "Regulatory Semantic Embedding",
  "openai/gpt-oss-20b": "GPT-OSS 20B",
  "openai/gpt-oss-120b": "GPT-OSS 120B",
};
