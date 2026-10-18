/**
 * Domain types for the TraceLayer investigation UI.
 * All data in this prototype is synthetic and tokenized.
 */

export type Tone = "ok" | "warn" | "info" | "bad" | "dark" | "blue" | "mute";

export type RoleId = "analyst" | "steward" | "officer" | "auditor";

export interface Role {
  id: RoleId;
  name: string;
  label: string;
  short: string;
  canUpload: boolean;
  canFixData: boolean;
  canAccessCases: boolean;
  canDecide: boolean;
  canApproveEscalation: boolean;
  canEditPolicy: boolean;
  canApproveRepair: boolean;
  readOnly: boolean;
}

export type Rail = "ACH" | "P2P" | "Wire";

export type NodeKey = "A" | "B" | "C" | "D" | "E" | "F" | "G" | "T";
export type AccountKey = Exclude<NodeKey, "T">;
export type NodeShape = "square" | "circle" | "diamond" | "open" | "hex";

export interface GraphNode {
  key: NodeKey;
  id: string;
  kind: string;
  shape: NodeShape;
  x: number;
  y: number;
  role: string;
}

export type EdgeKey = "e1" | "e2" | "e3" | "e4" | "e5" | "e6" | "e7";

export interface GraphEdge {
  key: EdgeKey;
  tx: string;
  from: AccountKey;
  to: AccountKey;
  rail: Rail;
  amount: number;
  initiated: string;
  settled: string;
  /** Position on the timeline scrubber (0-based). */
  order: number;
  /** Hops from the seed account; -1 means one hop backward. */
  hop: number;
  batch: string;
  row: number;
  gap: string;
  /** In the quarantined batch until the mapping fix is applied. */
  held?: boolean;
  crossBorder?: boolean;
  cycle?: boolean;
  /** Side branch revealed by expanding the seed node. */
  side?: boolean;
}

export type AlertId = "AL-1027" | "AL-1029" | "AL-1031" | "AL-1033" | "AL-1035" | "AL-1038" | "AL-1040";

export interface AlertSeed {
  id: AlertId;
  typology: string;
  amount: number;
  accounts: number;
  age: string;
  sla: string;
  cluster?: string;
  caseId?: CaseId;
  xgb: number;
  anomaly: number;
  typologyScore: number;
  rulesFired: number;
  graphScore: number;
  ageScore: number;
  graphText: string;
  dataQuality: string;
  benign?: boolean;
}

export type PriorityBand = 1 | 2 | 3;

export interface PriorityPart {
  label: string;
  value: number;
  weight: number;
  points: number;
}

export interface ScoredAlert extends AlertSeed {
  score: number;
  band: PriorityBand;
  bandLabel: string;
  bandTone: Tone;
  dataQualityTone: Tone;
  parts: PriorityPart[];
  status: string;
}

export interface PriorityWeights {
  xgb: number;
  typology: number;
  graph: number;
  anomaly: number;
  age: number;
}

export type CaseId = "CASE-0412" | "CASE-0398" | "CASE-0405" | "CASE-0409";

export type CaseStatus =
  | "In review"
  | "Pending approval"
  | "Escalated for SAR-support review"
  | "Closed, no further action"
  | "Awaiting information";

export interface CaseSummary {
  id: CaseId;
  title: string;
  owner: string;
  alerts: string;
  amount: string;
  status?: CaseStatus;
  summary?: string;
}

export type DispositionChoice = "escalate" | "close" | "more";

export interface Note {
  by: string;
  at: string;
  text: string;
}

export interface Revision {
  revision: number;
  by: string;
  note: string;
}

export type AuditType = "Data" | "Model" | "AI" | "Case" | "Alert" | "Graph" | "Access" | "Export" | "Policy" | "Entity" | "Screening";

export interface AuditEvent {
  at: string;
  actor: string;
  type: AuditType;
  action: string;
  object: string;
  requestId: string;
  hash: string;
}

export interface ExportRecord {
  id: string;
  caseId: CaseId;
  by: string;
  at: string;
  revision: string;
  hash: string;
}

export type BadRowMode = "mapping" | "repair" | "auto" | "owner" | "entity";
export type BadRowGroup = "rejected" | "flagged" | "resolved";

export interface BadRowSeed {
  row: number;
  type: string;
  group: Exclude<BadRowGroup, "resolved">;
  field: string;
  original: string;
  mode: BadRowMode;
  features: string[];
  alerts: string[];
  links: string[];
  cases: string[];
  result?: string;
  normalized?: string;
  before?: string;
  after?: string;
  fixNote?: string;
}

export type RepairStatus = "proposed" | "applied" | "rolledback" | "requested" | "sent";

export interface Repair {
  status: RepairStatus;
  proposedBy: RoleId;
  approvedBy?: RoleId;
}

export interface Batch {
  name: string;
  rail: string;
  rows: string;
  accepted: string;
  rejected: string;
  status: string;
  tone: Tone;
  owner: string;
  schema: string;
  hash: string;
  checks?: ContractCheck[];
}

export interface ContractCheck {
  label: string;
  count: string;
  result: "Pass" | "Fail" | "Flagged" | "Late" | "Pending";
  tone: Tone;
  note: string;
}

export interface EntityProfile {
  key: AccountKey;
  id: string;
  kind: string;
  role: string;
  opened: string;
  customer: string;
  geo: string;
  segment: string;
  baseline: [string, string, string];
  observed: [string, string, string];
  zScore: string;
  flowRatio: string;
  retained: string;
  interArrival: string;
  concentration: string;
  expected: string;
  observedActivity: string;
  mismatch: boolean;
  geoNote: string;
  counterparties: Array<{ id: string; direction: "Sender" | "Receiver"; firstSeen: string; status: "New" | "Known"; amount: string }>;
  links: Array<{ key: string; type: string; withId: string; token: string; how: string; factors: string; candidate: boolean }>;
  related: string[];
  screening?: boolean;
  dataHeld?: boolean;
  spikeDay: number;
  spikeIn: number;
  spikeOut: number;
  baseIn: number;
  baseOut: number;
}

export interface ModelEntry {
  key: "xgb" | "rules" | "iforest" | "gnn";
  name: string;
  version: string;
  status: string;
  tone: Tone;
  data: string;
  commit: string;
  schema: string;
  threshold: string;
  checksum: string;
  use: string;
  limits: string;
}

export interface ChatMessage {
  id: number;
  who: "user" | "ai";
  text?: string;
  answerKey?: AnswerKey;
  showTrace?: boolean;
  feedback?: string;
}

export type AnswerKey =
  | "explain"
  | "back"
  | "fwd"
  | "connected"
  | "evidence"
  | "benign"
  | "policy"
  | "missing"
  | "similar"
  | "next"
  | "inject"
  | "deny"
  | "clarify";

export type EvidenceKind =
  | "Transaction"
  | "Account"
  | "Model run"
  | "Detector findings"
  | "Data-quality issue"
  | "Known data gap"
  | "Shared token"
  | "Policy passage"
  | "Graph snapshot"
  | "Alert"
  | "Similar synthetic case"
  | "Evidence";

export interface EvidenceItem {
  id: string;
  kind: EvidenceKind;
  title: string;
  fields: Array<[string, string]>;
  original?: Array<[string, string]>;
  lineage?: string[];
  quote?: string;
  note?: string;
  edgeKey?: EdgeKey;
  accountKey?: AccountKey;
  alertId?: AlertId;
}
