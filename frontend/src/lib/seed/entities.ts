import type { AccountKey, EntityProfile } from "@/lib/types";

export const ENTITIES: Record<AccountKey, EntityProfile> = {
  A: {
    key: "A", id: "ACC-1104", kind: "Business operating account", role: "Source of funds", opened: "Aug 2024 (26 months)", customer: "BE-220 (tokenized business entity)", geo: "GEO-IL-17", segment: "Small business, wholesale supply",
    baseline: ["$18,400", "6", "22"], observed: ["$58,200 out in one payment", "1", "1 new"], zScore: "+3.1", flowRatio: "1.62 out / in (7 d)", retained: "$41,300 average balance", interArrival: "1 d 2 h median", concentration: "Low: 22 regular payees",
    expected: "Weekly supplier payments of $8,000 to $25,000 to known payees.", observedActivity: "One $58,200 ACH to a personal account opened 21 days earlier.", mismatch: true, geoNote: "No geography change observed.",
    counterparties: [
      { id: "ACC-2208", direction: "Receiver", firstSeen: "Oct 6", status: "New", amount: "$58,200.00" },
      { id: "EXT-CB-77", direction: "Sender", firstSeen: "Oct 9", status: "New", amount: "$12,000.00" },
      { id: "ACC-6031", direction: "Receiver", firstSeen: "Oct 2", status: "Known", amount: "$14,900.00" },
    ],
    links: [], related: ["AL-1027", "CASE-0412"], spikeDay: 26, spikeIn: 12000, spikeOut: 58200, baseIn: 16000, baseOut: 17000,
  },
  B: {
    key: "B", id: "ACC-2208", kind: "Personal checking", role: "Seed (alerted account)", opened: "Sep 15, 2026 (21 days before first transfer)", customer: "CU-7731 (tokenized)", geo: "GEO-CA-06", segment: "Retail, digital onboarding",
    baseline: ["$1,240", "9", "4"], observed: ["$115,900 in 2 days", "3", "3 new"], zScore: "+6.4", flowRatio: "0.99 out / in (24 h)", retained: "$500 at day end", interArrival: "1 h 48 m median", concentration: "High: 1 sender, 2 receivers",
    expected: "Salary deposits of about $2,600 twice a month (onboarding form).", observedActivity: "One $58,200 ACH from a business, then two outbound P2P transfers within 3 hours.", mismatch: true, geoNote: "Device login region GEO-TX-48 differs from address region GEO-CA-06 on Oct 6.",
    counterparties: [
      { id: "ACC-1104", direction: "Sender", firstSeen: "Oct 6", status: "New", amount: "$58,200.00" },
      { id: "ACC-3315", direction: "Receiver", firstSeen: "Oct 6", status: "New", amount: "$48,100.00" },
      { id: "ACC-3391", direction: "Receiver", firstSeen: "Oct 6", status: "New", amount: "$9,600.00" },
      { id: "EMP-PAY-12", direction: "Sender", firstSeen: "Sep 30", status: "Known", amount: "$2,600.00" },
    ],
    links: [
      { key: "dv", type: "SHARES_DEVICE", withId: "ACC-3391", token: "DV-77", how: "Exact token match", factors: "Same device token on 4 logins, Oct 5 to 6", candidate: false },
      { key: "ad", type: "SHARES_ADDRESS_TOKEN", withId: "ACC-3391", token: "AD-19", how: "Fuzzy match, confidence 0.83", factors: "Street and ZIP tokens match; unit number differs", candidate: true },
    ],
    related: ["AL-1027", "AL-1029", "CASE-0412"], screening: true, spikeDay: 26, spikeIn: 58200, spikeOut: 57700, baseIn: 600, baseOut: 450,
  },
  C: {
    key: "C", id: "ACC-3315", kind: "Personal checking", role: "Intermediary", opened: "Sep 28, 2026 (8 days before)", customer: "CU-8114 (tokenized)", geo: "GEO-TX-48", segment: "Retail",
    baseline: ["$310", "3", "2"], observed: ["$48,100 in, $47,650 out", "2", "2 new"], zScore: "+7.9", flowRatio: "0.99 out / in (24 h)", retained: "$450 at day end", interArrival: "4 h 29 m", concentration: "High: 1 sender, 1 receiver",
    expected: "No expected activity provided at onboarding.", observedActivity: "Received $48,100 by P2P and sent $47,650 by ACH 4 hours later.", mismatch: true, geoNote: "Address and device regions match.",
    counterparties: [
      { id: "ACC-2208", direction: "Sender", firstSeen: "Oct 6", status: "New", amount: "$48,100.00" },
      { id: "ACC-4471", direction: "Receiver", firstSeen: "Oct 6", status: "New", amount: "$47,650.00" },
    ],
    links: [], related: ["AL-1027", "CASE-0412"], dataHeld: true, spikeDay: 26, spikeIn: 48100, spikeOut: 47650, baseIn: 150, baseOut: 140,
  },
  D: {
    key: "D", id: "ACC-4471", kind: "Prepaid wallet", role: "Intermediary", opened: "Jul 2026", customer: "CU-5120 (tokenized)", geo: "GEO-NV-32", segment: "Prepaid program",
    baseline: ["$2,050", "11", "7"], observed: ["$47,650 in, $47,100 out", "2", "2 new"], zScore: "+5.2", flowRatio: "0.99 out / in (24 h)", retained: "$550", interArrival: "16 h 34 m", concentration: "Medium",
    expected: "Card loads and small merchant spend.", observedActivity: "Large ACH load followed by a domestic wire the next morning.", mismatch: true, geoNote: "No geography change observed.",
    counterparties: [
      { id: "ACC-3315", direction: "Sender", firstSeen: "Oct 6", status: "New", amount: "$47,650.00" },
      { id: "ACC-5590", direction: "Receiver", firstSeen: "Oct 7", status: "New", amount: "$47,100.00" },
    ],
    links: [], related: ["AL-1027", "CASE-0412"], dataHeld: true, spikeDay: 27, spikeIn: 47650, spikeOut: 47100, baseIn: 900, baseOut: 880,
  },
  E: {
    key: "E", id: "ACC-5590", kind: "Business account", role: "Intermediary, new beneficiary", opened: "Aug 2026 (2 months)", customer: "BE-311 (tokenized business entity)", geo: "GEO-FL-12", segment: "Import and export (self-declared)",
    baseline: ["$4,100", "5", "3"], observed: ["$47,100 in, $46,800 out", "2", "2 new"], zScore: "+4.8", flowRatio: "0.99 out / in (24 h)", retained: "$300", interArrival: "2 h 15 m", concentration: "High",
    expected: "Trade receipts and supplier payments.", observedActivity: "One domestic wire in, one cross-border wire out within hours.", mismatch: false, geoNote: "Cross-border destination (synthetic metadata).",
    counterparties: [
      { id: "ACC-4471", direction: "Sender", firstSeen: "Oct 7", status: "New", amount: "$47,100.00" },
      { id: "EXT-CB-77", direction: "Receiver", firstSeen: "Oct 7", status: "New", amount: "$46,800.00" },
    ],
    links: [], related: ["AL-1027", "CASE-0412"], spikeDay: 27, spikeIn: 47100, spikeOut: 46800, baseIn: 2100, baseOut: 2000,
  },
  F: {
    key: "F", id: "EXT-CB-77", kind: "Correspondent bank (synthetic)", role: "Exit, cross-border", opened: "Not a customer", customer: "External counterparty", geo: "GEO-XB-04 (synthetic)", segment: "Correspondent",
    baseline: ["n/a", "n/a", "n/a"], observed: ["$46,800 received", "1", "1"], zScore: "n/a", flowRatio: "n/a", retained: "n/a", interArrival: "n/a", concentration: "n/a",
    expected: "External party. Only payment metadata is available.", observedActivity: "Received a cross-border wire; returned $12,000 to ACC-1104 two days later.", mismatch: false, geoNote: "Beneficiary details are synthetic metadata only.",
    counterparties: [
      { id: "ACC-5590", direction: "Sender", firstSeen: "Oct 7", status: "New", amount: "$46,800.00" },
      { id: "ACC-1104", direction: "Receiver", firstSeen: "Oct 9", status: "New", amount: "$12,000.00" },
    ],
    links: [], related: ["AL-1027"], spikeDay: 27, spikeIn: 46800, spikeOut: 12000, baseIn: 0, baseOut: 0,
  },
  G: {
    key: "G", id: "ACC-3391", kind: "Personal checking", role: "Shares a device with ACC-2208", opened: "Sep 14, 2026", customer: "CU-7740 (tokenized)", geo: "GEO-CA-06", segment: "Retail, digital onboarding",
    baseline: ["$880", "6", "3"], observed: ["$9,600 in, $9,400 cash out", "2", "1 new"], zScore: "+4.1", flowRatio: "0.98 out / in (24 h)", retained: "$200", interArrival: "3 h 05 m", concentration: "High",
    expected: "Small card spend.", observedActivity: "$9,600 P2P from ACC-2208, then ATM withdrawals.", mismatch: true, geoNote: "Same device token as ACC-2208.",
    counterparties: [{ id: "ACC-2208", direction: "Sender", firstSeen: "Oct 6", status: "New", amount: "$9,600.00" }],
    links: [{ key: "dv2", type: "SHARES_DEVICE", withId: "ACC-2208", token: "DV-77", how: "Exact token match", factors: "Same device token on 4 logins", candidate: false }],
    related: ["AL-1029", "CASE-0412"], spikeDay: 26, spikeIn: 9600, spikeOut: 9400, baseIn: 300, baseOut: 280,
  },
};

export const ENTITY_ORDER: AccountKey[] = ["A", "B", "C", "D", "E", "F", "G"];

export function entityByAccountId(accountId: string): EntityProfile | undefined {
  return ENTITY_ORDER.map((k) => ENTITIES[k]).find((e) => e.id === accountId);
}

export const DEFAULT_ENTITY = "ACC-2208";

export interface DailyFlow {
  label: string;
  inflow: number;
  outflow: number;
}

/** Deterministic 30-day series (Sep 10 to Oct 9) used by the flow chart. */
export function dailyFlows(entity: EntityProfile): DailyFlow[] {
  const days: DailyFlow[] = [];
  for (let i = 0; i < 30; i++) {
    const date = new Date(Date.UTC(2026, 8, 10 + i));
    const label = `${date.getUTCMonth() === 8 ? "Sep" : "Oct"} ${date.getUTCDate()}`;
    const jitterIn = ((i * 37 + entity.key.charCodeAt(0) * 11) % 10) / 10;
    const jitterOut = ((i * 53) % 10) / 10;
    let inflow = entity.baseIn * (0.4 + jitterIn);
    let outflow = entity.baseOut * (0.3 + jitterOut);
    if (entity.key === "B" && i < 5) {
      inflow = 0;
      outflow = 0;
    }
    if (i === entity.spikeDay) {
      inflow = entity.spikeIn;
      outflow = entity.spikeOut;
    }
    if (entity.key === "A" && i === 29) {
      inflow = 12000;
      outflow = entity.baseOut;
    }
    if (entity.key === "F" && i === 29) {
      inflow = 0;
      outflow = 12000;
    }
    days.push({ label, inflow: Math.round(inflow), outflow: Math.round(outflow) });
  }
  return days;
}
