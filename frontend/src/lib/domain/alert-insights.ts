import type { ScoredAlert, Tone } from "@/lib/types";

export interface CitedPoint {
  text: string;
  cites: string[];
}

export interface TypologySignal {
  name: string;
  result: "Fired" | "Lead only" | "Not evaluated" | "Not fired";
  parameters: string;
  cites: string[];
}

export function typologyTone(result: TypologySignal["result"]): Tone {
  switch (result) {
    case "Fired":
      return "dark";
    case "Lead only":
      return "info";
    case "Not evaluated":
      return "warn";
    default:
      return "mute";
  }
}

const p = (text: string, cites: string[]): CitedPoint => ({ text, cites });

export function whyFlagged(alert: ScoredAlert, fixed: boolean): CitedPoint[] {
  if (alert.id === "AL-1027") {
    return [
      p("Funds left ACC-2208 within 1 h 48 m of arriving.", ["TX-88201", "TX-88214"]),
      p("All 3 counterparties were new to ACC-2208, opened 21 days earlier.", ["ACC-2208"]),
      fixed
        ? p("The path continues 4 hops to a cross-border wire in 25 h 6 m.", ["TX-88237", "TX-88266"])
        : p("The path breaks at ACC-3315: the next hop is in a quarantined batch.", ["DQ-1042"]),
      p("ACC-2208 shares a device with ACC-3391, which received $9,600.", ["DV-77", "TX-88222"]),
    ];
  }
  if (alert.id === "AL-1033") {
    return [p("213 outbound transfers within 1 hour.", ["DF-1027"]), p("Total of $212,400 in 24 hours, well above the 30-day average.", ["MR-2291"])];
  }
  return [p(`${alert.graphText}.`, ["DF-1027"]), p(`XGBoost score ${alert.xgb.toFixed(2)} against threshold 0.62.`, ["MR-2291"])];
}

export function whyBenign(alert: ScoredAlert): CitedPoint[] {
  if (alert.id === "AL-1027") {
    return [
      p("ACC-1104 pays suppliers weekly, so a supplier payment is possible.", ["ACC-1104"]),
      p("Shared devices can mean a shared household.", ["DV-77"]),
      p("No invoices are available to confirm or rule out a business purpose.", ["DQ-GAP-INV"]),
    ];
  }
  if (alert.id === "AL-1033") {
    return [
      p("The same 214 recipients are paid every 2 weeks for 18 months.", ["DF-1027"]),
      p("Amounts match the business payroll file pattern; periodicity pulls the model score down.", ["MR-2291"]),
      p("A similar case, CASE-0301, was closed as benign after review.", ["CASE-0301"]),
    ];
  }
  return [p("No counter-evidence has been reviewed yet. Check expected activity on the entity profile.", ["DQ-GAP-INV"])];
}

export function typologySignals(alert: ScoredAlert, fixed: boolean, bandMin: string, bandMax: string): TypologySignal[] {
  const band = `Band $${bandMin} to $${bandMax}`;
  if (alert.id === "AL-1027") {
    return [
      { name: "Rapid pass-through", result: "Fired", parameters: "Out/in at least 0.9 within 24 h", cites: ["TX-88201", "TX-88214"] },
      { name: "Layering, multi-hop", result: fixed ? "Fired" : "Not evaluated", parameters: fixed ? "4 or more hops within 72 h" : "Path incomplete: hop on hold", cites: fixed ? ["TX-88237", "TX-88259"] : ["DQ-1042"] },
      { name: "New beneficiary", result: "Fired", parameters: "All counterparties first seen in 7 d", cites: ["ACC-2208"] },
      { name: "Round trip", result: "Lead only", parameters: "Possible 6-hop cycle, bounded search", cites: ["TX-88290"] },
      { name: "Structuring-like", result: "Not fired", parameters: band, cites: [] },
    ];
  }
  return [
    { name: alert.typology, result: "Fired", parameters: "Rule parameters v3.2", cites: ["DF-1027"] },
    { name: "Structuring-like", result: "Not fired", parameters: band, cites: [] },
  ];
}

export function graphContext(alert: ScoredAlert, fixed: boolean): Array<[string, string]> {
  const main = alert.id === "AL-1027";
  return [
    ["Connected component", main ? (fixed ? "8 accounts" : "4 accounts visible") : "[__] accounts"],
    ["Two-hop exposure", main ? "7 accounts" : "[__]"],
    ["Cycle membership", main ? "Possible round trip, lead only" : "None found (bounded search)"],
    ["PageRank percentile", main ? "96th" : "[__]"],
  ];
}
