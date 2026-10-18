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
