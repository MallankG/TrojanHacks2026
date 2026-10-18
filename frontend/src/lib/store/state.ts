import { SEED_EVENTS, SEED_EXPORTS, type FeatureFlagKey } from "@/lib/seed/governance";
import type {
  AuditEvent,
  Batch,
  CaseId,
  CaseStatus,
  ChatMessage,
  DispositionChoice,
  EdgeKey,
  ExportRecord,
  NodeKey,
  Note,
  PriorityWeights,
  Rail,
  Repair,
  Revision,
  RoleId,
} from "@/lib/types";

export type ScreenKey = "command" | "data" | "risk" | "graph" | "entity" | "copilot" | "cases" | "governance";

export type GraphSelection = { kind: "edge"; key: EdgeKey } | { kind: "node"; key: NodeKey };

export interface GraphView {
  depth: 2 | 3 | 4 | 5;
  direction: "back" | "both" | "fwd";
  rails: Record<Rail, boolean>;
  minAmount: 0 | 10000 | 40000;
  overlays: { cycle: boolean; token: boolean };
  time: number;
  selection: GraphSelection;
  expanded: boolean;
  pinned: string[];
  pinSaved: boolean;
  view: "graph" | "table";
  compare: boolean;
}

export interface CopilotState {
  messages: ChatMessage[];
  focus: string;
  contextCase: CaseId;
  nextId: number;
}

export interface ReportState {
  formats: { json: boolean; csv: boolean; pdf: boolean };
  done: boolean;
  exportId: string;
  revision: number;
  hash: string;
  at: string;
  by: string;
}

export interface CaseWork {
  status: CaseStatus;
  notes: Note[];
  counterReviewed: boolean;
  claimFixed: boolean;
  revisions: Revision[];
  showDiff: boolean;
  disposition: DispositionChoice | null;
  rationale: string;
  /** Role that requested escalation; a different role must approve it (four-eyes). */
  escalatedBy: RoleId | null;
  seenSource: boolean;
  report: ReportState;
  verifyResult: string;
}

export type WeightKey = keyof PriorityWeights;

export interface PolicyState {
  /** Version of the saved priority policy shown wherever priority is explained. */
  version: string;
  bandMin: string;
  bandMax: string;
  weights: Record<WeightKey, string>;
  aiDrafting: boolean;
  flags: Record<FeatureFlagKey, boolean>;
}

export type ConfirmAction = "escalate" | "close" | "more" | "approve" | "return";

export type ModalState =
  | { kind: "onboard" }
  | { kind: "mappingFix" }
  | { kind: "evidence"; id: string }
  | { kind: "confirm"; action: ConfirmAction }
  | { kind: "killSwitch" }
  | { kind: "editClaim" }
  | { kind: "switchCase" }
  | { kind: "help" }
  | { kind: "sessionTimeout" };

export type QueueBandFilter = "all" | 1 | 2 | 3;
export type QueueStatusFilter = "all" | "new" | "case";

export interface RiskQueueFilters {
  text: string;
  band: QueueBandFilter;
  status: QueueStatusFilter;
  grouped: boolean;
}

export interface DemoState {
  version: number;
  authed: boolean;
  role: RoleId;
  sim: { llm: boolean; graph: boolean };
  data: {
    driftFixed: boolean;
    checkScope: boolean;
    selectedBatch: string;
    extraBatches: Batch[];
    repairs: Record<number, Repair>;
  };
  graph: GraphView;
  riskQueue: RiskQueueFilters;
  copilot: CopilotState;
  caseWork: CaseWork;
  policy: PolicyState;
  erDecisions: Record<string, "Approved" | "Rejected">;
  screeningDecision: string;
  events: AuditEvent[];
  seq: number;
  exports: ExportRecord[];
  visited: Partial<Record<ScreenKey, boolean>>;
  modal: ModalState | null;
  toast: { id: number; message: string } | null;
}

export const STATE_VERSION = 5;

export const DEFAULT_WEIGHTS: Record<WeightKey, string> = { xgb: "35", typology: "25", graph: "20", anomaly: "10", age: "10" };

export function createInitialState(): DemoState {
  return {
    version: STATE_VERSION,
    authed: false,
    role: "analyst",
    sim: { llm: false, graph: false },
    data: { driftFixed: false, checkScope: false, selectedBatch: "ach_2026_10_06_b", extraBatches: [], repairs: {} },
    graph: {
      depth: 5,
      direction: "both",
      rails: { ACH: true, P2P: true, Wire: true },
      minAmount: 0,
      overlays: { cycle: false, token: false },
      time: 6,
      selection: { kind: "edge", key: "e1" },
      expanded: false,
      pinned: ["TX-88201", "TX-88214"],
      pinSaved: false,
      view: "graph",
      compare: false,
    },
    riskQueue: { text: "", band: "all", status: "all", grouped: true },
    copilot: { messages: [], focus: "TX-88201", contextCase: "CASE-0412", nextId: 1 },
    caseWork: {
      status: "In review",
      notes: [
        { by: "M. Rivera", at: "Oct 9 13:46 UTC", text: "Opened from AL-1027. AL-1029 clustered by alert fusion (CL-17)." },
        { by: "System", at: "Oct 9 13:52 UTC", text: "Narrative r1 drafted. Verifier blocked 1 claim before display." },
      ],
      counterReviewed: false,
      claimFixed: false,
      revisions: [{ revision: 1, by: "AI draft", note: "Verifier blocked 1 claim" }],
      showDiff: false,
      disposition: null,
      rationale: "",
      escalatedBy: null,
      seenSource: false,
      report: { formats: { json: true, csv: true, pdf: true }, done: false, exportId: "", revision: 0, hash: "", at: "", by: "" },
      verifyResult: "",
    },
    policy: {
      version: "v3.1",
      bandMin: "8000",
      bandMax: "9999",
      weights: { ...DEFAULT_WEIGHTS },
      aiDrafting: true,
      flags: { propagation: false, passport: true, snapshotDiff: true, gnn: false, sandbox: false },
    },
    erDecisions: {},
    screeningDecision: "",
    events: SEED_EVENTS,
    seq: 0,
    exports: SEED_EXPORTS,
    visited: {},
    modal: null,
    toast: null,
  };
}
