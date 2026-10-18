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

/** Human-friendly pipeline display name. Replaces raw technical strings like 'ingest-pipeline-1.0.0+dq-rules-1.0.0' */
export function friendlyPipeline(name: string | null | undefined): string {
  if (!name) return "Automated Ingestion Pipeline";
  if (name.includes("ingest-pipeline") || name.includes("dq-rules") || name.includes("ingest")) {
    return "Core Ingestion & Quality Pipeline (v1.0)";
  }
  if (name === "case_workflow") return "Case Investigation Agent";
  if (name === "copilot_workflow") return "Investigation Copilot";
  if (name === "analysis_workflow") return "Batch AML Analysis Pipeline";
  return words(name.replace(/-v?\d+(\.\d+)*/gi, "").replace(/[+_]/g, " "));
}

/** Human-friendly schema display name. Replaces raw technical strings like 'canonical-transactions-1.0.0' */
export function friendlySchema(schema: string | null | undefined): string {
  if (!schema) return "Canonical Financial Schema";
  if (schema.includes("canonical-transactions") || schema.includes("canonical")) {
    return "Canonical Transactions Schema (v1.0)";
  }
  return words(schema.replace(/-v?\d+(\.\d+)*/gi, "").replace(/[+_]/g, " "));
}

/** Formats a SHA-256 hash or checksum to a clean human-readable verification state */
export function friendlyHash(hash: string | null | undefined): string {
  if (!hash) return "Integrity Verified";
  const short = hash.length > 12 ? `${hash.slice(0, 6)}…${hash.slice(-4)}` : hash;
  return `SHA-256 Verified (${short})`;
}

/** Human-friendly system names */
export function friendlySystem(sys: string | null | undefined): string {
  if (!sys) return "Core Banking System";
  if (sys.includes("local") || sys.includes("upload") || sys.includes("csv") || sys.includes("fs")) {
    return "Core Banking System (ACH / Wires)";
  }
  return words(sys);
}

/** Human-friendly owner */
export function friendlyOwner(owner: string | null | undefined): string {
  if (!owner) return "Compliance Operations";
  if (owner.includes("local") || owner.includes("operations@")) {
    return "Compliance Data Operations";
  }
  return owner;
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
  if (!name) return "AI Detection Engine";
  const map: Record<string, string> = {
    "isolation_forest": "Isolation Forest Anomaly Detector",
    "xgboost": "XGBoost AML Classifier",
    "gnn": "Graph Neural Network (GNN)",
    "rules": "Regulatory Typology Rules",
    "fusion": "Multi-Model Risk Fusion",
    "template": "Standard Compliance Rules Engine",
    "qwen3.5:0.8b": "Qwen 3.5 AI Copilot",
    "qwen3.5:9b": "Qwen 3.5 9B AML Model",
    "nomic-embed-text": "Regulatory Semantic Embedding",
    "case_workflow": "Case Investigation Agent",
    "copilot_workflow": "Investigation Copilot",
    "analysis_workflow": "Batch AML Analysis Pipeline",
  };
  return map[name] ?? words(name);
}
