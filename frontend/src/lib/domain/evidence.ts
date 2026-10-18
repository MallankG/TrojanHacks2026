import { ENTITIES } from "@/lib/seed/entities";
import { SIMILAR_CASES } from "@/lib/seed/governance";
import { EDGES, NODES } from "@/lib/seed/graph";
import { formatMoney } from "@/lib/format";
import type { AccountKey, EvidenceItem } from "@/lib/types";

/** Builds the case-scoped evidence registry used by citations everywhere. */
export function buildEvidence(fixed: boolean): Record<string, EvidenceItem> {
  const registry: Record<string, EvidenceItem> = {};

  for (const e of EDGES) {
    const held = Boolean(e.held) && !fixed;
    registry[e.tx] = {
      id: e.tx,
      kind: "Transaction",
      title: `${e.tx} · ${NODES[e.from].id} to ${NODES[e.to].id}`,
      fields: [
        ["Amount", `${formatMoney(e.amount)} USD`],
        ["Rail", `${e.rail}${e.crossBorder ? ", cross-border (synthetic metadata)" : ""}`],
        ["Initiated", `${e.initiated} UTC`],
        ["Settled", `${e.settled} UTC`],
        ["Status", held ? "On hold: batch quarantined" : "Settled"],
        ["Time gap", e.gap],
      ],
      original: [
        ["source_file", `${e.batch}.csv`],
        ["row", String(e.row)],
        ["txn_id", e.tx],
        ["amt", e.amount.toFixed(2)],
        ["ccy", "USD"],
        [e.held ? "bene_acct_no" : "acct_to", NODES[e.to].id],
      ],
      lineage: [`${e.batch} row ${e.row}`, held ? "Canonical record not created" : `Canonical ${e.tx}`, "Feature set v7", fixed ? "SR-2292" : "SR-2291", "AL-1027"],
      note: held ? "This record is in a quarantined batch. Values come from the raw row and are not normalized yet." : undefined,
      edgeKey: e.key,
    };
  }

  (Object.keys(ENTITIES) as AccountKey[]).forEach((key) => {
    const ent = ENTITIES[key];
    registry[ent.id] = {
      id: ent.id,
      kind: "Account",
      title: `${ent.id} · ${NODES[key].kind}`,
      fields: [
        ["Role in case", NODES[key].role],
        ["Opened", ent.opened],
        ["Customer", ent.customer],
        ["Expected activity", ent.expected],
      ],
      note: "Identifiers are synthetic and tokenized.",
      accountKey: key,
    };
  });

  registry["MR-2291"] = {
    id: "MR-2291",
    kind: "Model run",
    title: `${fixed ? "SR-2292" : "SR-2291"} · xgb-aml v1.4.2`,
    fields: [
      ["Score for AL-1027", fixed ? "0.91" : "0.71"],
      ["Threshold", "0.62"],
      ["Feature set", "v7, as-of Oct 9 13:40 UTC"],
      ["Artifact checksum", "sha256:5b1e…9a40 (simulated)"],
      ["Top contributions", "out_in_ratio_24h, median_interarrival_min, new_counterparty_share_7d"],
    ],
    note: "A score ranks leads for review. It is not a probability of crime.",
  };
  registry["DF-1027"] = {
    id: "DF-1027",
    kind: "Detector findings",
    title: "DF-1027 · typology-rules v3.2",
    fields: [
      ["Rapid pass-through", "Fired: out/in 0.99 within 24 h"],
      ["Layering", fixed ? "Fired: 4 hops in 25 h 6 m" : "Not evaluated: path incomplete"],
      ["New beneficiary", "Fired: 3 new counterparties"],
      ["Round trip", "Lead: possible 6-hop cycle"],
    ],
  };
  registry["DQ-1042"] = {
    id: "DQ-1042",
    kind: "Data-quality issue",
    title: "DQ-1042 · ach_2026_10_06_b, row 1042",
    fields: [
      ["Check", "Required field target_account"],
      ["Cause", "Schema drift: column renamed to bene_acct_no"],
      ["Status", fixed ? "Resolved by mapping fix v4.1" : "Open: batch quarantined"],
      ["Owner", "Payments Ops, P. Nair"],
      ["Impact", "1,206 rows, 3 alerts, 14 features"],
    ],
    note: "Missing data is not treated as low risk.",
  };
  registry["DQ-GAP-INV"] = {
    id: "DQ-GAP-INV",
    kind: "Known data gap",
    title: "Invoices for ACC-1104 payments",
    fields: [
      ["Status", "Not available in this tenant"],
      ["Effect", "A supplier-payment explanation can be neither confirmed nor ruled out"],
    ],
  };
  registry["DV-77"] = {
    id: "DV-77",
    kind: "Shared token",
    title: "DV-77 · device token",
    fields: [
      ["Linked accounts", "ACC-2208, ACC-3391"],
      ["Evidence", "4 logins on Oct 5 to 6 from the same device token"],
      ["Match", "Exact token match"],
      ["Caution", "Households and shared phones also share devices"],
    ],
    note: "Proximity is a review lead, never proof.",
  };
  const policyNote = "Policy context explains review steps. It does not prove wrongdoing.";
  registry["POL-FFIEC-SAR"] = {
    id: "POL-FFIEC-SAR",
    kind: "Policy passage",
    title: "FFIEC BSA/AML Examination Manual · Suspicious Activity Reporting",
    fields: [
      ["Source", "bsaaml.ffiec.gov/manual"],
      ["Section", "[section from index]"],
      ["Indexed", "[index date]"],
      ["Chunk", "POL-FFIEC-SAR-03"],
    ],
    quote: "[Exact passage text, word for word, from the indexed corpus]",
    note: policyNote,
  };
  registry["POL-FINCEN-ADV"] = {
    id: "POL-FINCEN-ADV",
    kind: "Policy passage",
    title: "FinCEN public guidance · [document title from index]",
    fields: [
      ["Source", "fincen.gov/resources"],
      ["Section", "[section from index]"],
      ["Indexed", "[index date]"],
      ["Chunk", "POL-FINCEN-11"],
    ],
    quote: "[Exact passage text, word for word, from the indexed corpus]",
    note: policyNote,
  };
  registry["GS-0412"] = {
    id: "GS-0412",
    kind: "Graph snapshot",
    title: `${fixed ? "GS-0412-b" : "GS-0412-a"} · CASE-0412`,
    fields: [
      ["Query", "trace_funds(seed ACC-2208, both, max_hops 5, Oct 6 to 9)"],
      ["Nodes and edges", fixed ? "8 nodes, 8 edges" : "4 nodes, 3 edges"],
      ["Graph version", fixed ? "v184" : "v183"],
      ["Hash", fixed ? "sha256:71c0…8e2d (simulated)" : "sha256:0b9e…41c7 (simulated)"],
    ],
  };
  registry["AL-1029"] = {
    id: "AL-1029",
    kind: "Alert",
    title: "AL-1029 · Shared-device mule cluster",
    fields: [
      ["Cluster", "CL-17 with AL-1027"],
      ["Amount", "$9,600.00"],
      ["Signal", "Shares device DV-77 with ACC-2208"],
    ],
    alertId: "AL-1029",
  };
  for (const c of SIMILAR_CASES) {
    registry[c.id] = {
      id: c.id,
      kind: "Similar synthetic case",
      title: `${c.id} · ${c.title}`,
      fields: [
        ["Similarity", c.similarity],
        ["Prior decision", c.decision],
        ["Matched", "Flow shape, rail mix, new counterparties"],
      ],
      note: "Similarity is not proof. Prior decisions can be wrong.",
    };
  }
  return registry;
}

export function evidenceOrFallback(registry: Record<string, EvidenceItem>, id: string): EvidenceItem {
  return registry[id] ?? { id, kind: "Evidence", title: id, fields: [], note: "This reference is not in the current case scope." };
}

export const CASE_EVIDENCE_IDS = [
  "TX-88201",
  "TX-88214",
  "TX-88222",
  "TX-88237",
  "TX-88259",
  "TX-88266",
  "TX-88290",
  "ACC-2208",
  "ACC-3391",
  "MR-2291",
  "DF-1027",
  "DQ-1042",
  "DV-77",
  "POL-FFIEC-SAR",
  "POL-FINCEN-ADV",
] as const;
