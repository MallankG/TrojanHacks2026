"use client";

import { demoTimestamp, simulatedHash } from "@/lib/format";
import { routeIntent } from "@/lib/domain/copilot";
import { bundleBlockers, isCaseVerified, MIN_RATIONALE, verifyClaimEdit } from "@/lib/domain/case";
import { bandError } from "@/lib/domain/governance";
import { parseWeights, scoringInputs } from "@/lib/domain/priority";
import { ROLES, deniedMessage } from "@/lib/seed/roles";
import type { FeatureFlagKey } from "@/lib/seed/governance";
import type { AlertId, AnswerKey, AuditType, Batch, CaseId, CaseStatus, DispositionChoice, NodeKey, Rail, RoleId } from "@/lib/types";
import {
  createInitialState,
  type ConfirmAction,
  type DemoState,
  type GraphView,
  type ModalState,
  type RecomputeKind,
  type RiskQueueFilters,
  type ScreenKey,
  type WeightKey,
} from "./state";
import { getState, setState } from "./store";

type EventSpec = { type: AuditType; action: string; object?: string; system?: boolean };

/** Appends audit events, newest first, advancing the demo clock. */
function withEvents(state: DemoState, specs: EventSpec[]): DemoState {
  const role = ROLES[state.role];
  let seq = state.seq;
  const created = specs.map((spec) => {
    seq += 1;
    return {
      at: demoTimestamp(seq),
      actor: spec.system ? "System" : `${role.name} (${role.short})`,
      type: spec.type,
      action: spec.action,
      object: spec.object ?? "",
      requestId: `REQ-${8100 + seq}`,
      hash: simulatedHash(`${spec.type}${spec.action}${seq}`, 12),
    };
  });
  return { ...state, seq, events: [...created.reverse(), ...state.events] };
}

/** Records the scoring inputs before and after a change so the alert diff can recompute both sides. */
function withRecompute(prev: DemoState, next: DemoState, kind: RecomputeKind, reason: string): DemoState {
  const n = next.recomputes.length + 1;
  const record = { id: `RC-${String(n).padStart(3, "0")}`, at: demoTimestamp(next.seq), by: ROLES[next.role].name, kind, reason, before: scoringInputs(prev), after: scoringInputs(next) };
  return { ...next, recomputes: [record, ...next.recomputes] };
}

let toastId = 0;

export function toast(message: string): void {
  toastId += 1;
  const id = toastId;
  setState((s) => ({ ...s, toast: { id, message } }));
}

export function dismissToast(id: number): void {
  setState((s) => (s.toast?.id === id ? { ...s, toast: null } : s));
}

function role() {
  return ROLES[getState().role];
}

/** Returns false (and explains why) when the current role cannot do something. */
function guard(allowed: boolean, message?: string): boolean {
  if (!allowed) toast(message ?? deniedMessage(role()));
  return allowed;
}

// ---------- session ----------

export function signIn(): void {
  setState((s) => ({ ...s, authed: true }));
  toast("Signed in. Sessions end after 15 minutes of inactivity.");
}

export function signOut(): void {
  setState((s) => ({ ...s, authed: false, modal: null }));
}

export function setRole(next: RoleId): void {
  setState((s) => ({ ...s, role: next }));
  toast(`Signed in as ${ROLES[next].name}, ${ROLES[next].label}. Permissions updated.`);
}

export function resetDemo(): void {
  setState(() => ({ ...createInitialState(), authed: true }));
  toast("Demo reset to the seeded scenario.");
}

export function toggleSimulation(kind: "llm" | "graph"): void {
  setState((s) => ({ ...s, sim: { ...s.sim, [kind]: !s.sim[kind] } }));
}

export function markVisited(screen: ScreenKey): void {
  setState((s) => (s.visited[screen] ? s : { ...s, visited: { ...s.visited, [screen]: true } }));
}

export function openModal(modal: ModalState): void {
  setState((s) => ({ ...s, modal }));
}

export function closeModal(): void {
  setState((s) => (s.modal ? { ...s, modal: null } : s));
}

export function openEvidence(id: string): void {
  setState((s) => ({ ...s, modal: { kind: "evidence", id }, caseWork: id.startsWith("TX-") ? { ...s.caseWork, seenSource: true } : s.caseWork }));
}

// ---------- data operations ----------

export function applyMappingFix(reason: string): string | null {
  if (!guard(role().canFixData)) return null;
  if (reason.trim().length < 10) return "Write a reason of at least 10 characters. It is saved with the mapping version.";
  setState((s) =>
    withRecompute(
      s,
      withEvents(
      { ...s, modal: null, data: { ...s.data, driftFixed: true } },
      [
        { type: "Data", action: `Mapping fix v4.1 approved: bene_acct_no to target_account. Reason: ${reason.trim()}`, object: "ach_2026_10_06_b" },
        { type: "Data", action: "ach_2026_10_06_b reloaded: 18,212 accepted, 28 rejected", object: "ach_2026_10_06_b", system: true },
        { type: "Model", action: "Re-score SR-2292: 3 alerts updated; AL-1027 now review first", object: "SR-2292", system: true },
        { type: "Graph", action: "Snapshot GS-0412-b created (graph v184)", object: "CASE-0412", system: true },
      ],
      ),
      "Data",
      `Mapping fix v4.1 reloaded ach_2026_10_06_b. Reason: ${reason.trim()}`,
    ),
  );
  toast("Batch reloaded. 3 alerts re-scored. AL-1027 path is now complete.");
  return null;
}

export function rollbackMapping(): void {
  if (!guard(role().canFixData)) return;
  setState((s) =>
    withRecompute(
      s,
      withEvents(
      {
        ...s,
        data: { ...s.data, driftFixed: false },
        caseWork: {
          ...s.caseWork,
          // An escalation choice made on verified data is no longer valid.
          disposition: s.caseWork.disposition === "escalate" ? null : s.caseWork.disposition,
          report: { ...s.caseWork.report, done: false },
          verifyResult: "",
        },
      },
      [{ type: "Data", action: "Mapping rolled back to v4.0; ach_2026_10_06_b quarantined again; downstream marked stale", object: "ach_2026_10_06_b" }],
      ),
      "Data",
      "Mapping rolled back to v4.0; the held hop is quarantined again",
    ),
  );
  toast("Rolled back to mapping v4.0. Downstream outputs marked stale.");
}

export function addCheckFeedToScope(): void {
  if (!guard(role().canUpload)) return;
  setState((s) => withEvents({ ...s, data: { ...s.data, checkScope: true } }, [{ type: "Data", action: "Check feed added to monitoring scope; contract draft created", object: "check feed" }]));
  toast("Check feed added to scope. Onboard a file to start monitoring.");
}

export function proposeRepair(row: number, fixNote: string, reason: string): boolean {
  if (!guard(role().canFixData)) return false;
  if (reason.trim().length < 10) {
    toast("Add a reason of at least 10 characters before proposing.");
    return false;
  }
  setState((s) =>
    withEvents({ ...s, data: { ...s.data, repairs: { ...s.data.repairs, [row]: { status: "proposed", proposedBy: s.role } } } }, [{ type: "Data", action: `Repair proposed for row ${row}: ${fixNote}`, object: "ach_2026_10_06_b" }]),
  );
  toast("Repair proposed. A different reviewer must approve it.");
  return true;
}

export function approveRepair(row: number): void {
  const s0 = getState();
  const repair = s0.data.repairs[row];
  if (!repair) return;
  if (!guard(role().canApproveRepair, "Only a data steward or BSA officer can approve repairs.")) return;
  if (!guard(repair.proposedBy !== s0.role, "You proposed this repair. A different reviewer must approve it.")) return;
  setState((s) =>
    withEvents(
      { ...s, data: { ...s.data, repairs: { ...s.data.repairs, [row]: { ...repair, status: "applied", approvedBy: s.role } } } },
      [{ type: "Data", action: `Repair approved for row ${row}; derived version v2 created; downstream re-scored`, object: "ach_2026_10_06_b" }],
    ),
  );
  toast("Repair applied to a derived version. Source row unchanged. Downstream outputs re-scored.");
}

export function rollbackRepair(row: number): void {
  const repair = getState().data.repairs[row];
  if (!repair) return;
  if (!guard(role().canApproveRepair, "Only a data steward or BSA officer can roll back repairs.")) return;
  setState((s) =>
    withEvents({ ...s, data: { ...s.data, repairs: { ...s.data.repairs, [row]: { status: "rolledback", proposedBy: repair.proposedBy } } } }, [{ type: "Data", action: `Repair rolled back for row ${row}; prior normalized version restored`, object: "ach_2026_10_06_b" }]),
  );
  toast("Rolled back to the prior normalized version.");
}

export function routeRow(row: number, kind: "requested" | "sent"): void {
  if (!guard(!role().readOnly)) return;
  const action = kind === "requested" ? `Corrected export requested from Payments Ops for row ${row}` : `Row ${row} sent to entity review; no automatic merge`;
  setState((s) => withEvents({ ...s, data: { ...s.data, repairs: { ...s.data.repairs, [row]: { status: kind, proposedBy: s.role } } } }, [{ type: "Data", action, object: kind === "sent" ? "ACC-9X12" : "ach_2026_10_06_b" }]));
  toast(kind === "requested" ? "Request sent to the source owner." : "Sent to entity review. Nothing is merged without approval.");
}

export function commitBatch(batch: Batch, quarantined: boolean, owner: string): boolean {
  if (!guard(role().canUpload)) return false;
  setState((s) =>
    withEvents(
      { ...s, modal: null, data: { ...s.data, selectedBatch: batch.name, extraBatches: [batch, ...s.data.extraBatches.filter((b) => b.name !== batch.name)] } },
      [{ type: "Data", action: quarantined ? `${batch.name} quarantined; owner ${owner} notified` : `${batch.name} committed: ${batch.accepted} accepted, ${batch.rejected} rejected`, object: batch.name }],
    ),
  );
  toast(quarantined ? `${batch.name} quarantined. ${owner} was notified.` : `${batch.name} committed. Scoring will re-run on the new rows.`);
  return true;
}

export function selectBatch(name: string): void {
  setState((s) => ({ ...s, data: { ...s.data, selectedBatch: name } }));
}

// ---------- risk queue ----------

export function updateRiskQueue(patch: Partial<RiskQueueFilters>): void {
  setState((s) => ({ ...s, riskQueue: { ...s.riskQueue, ...patch } }));
}

// ---------- graph ----------

export function updateGraph(patch: Partial<GraphView>): void {
  setState((s) => ({ ...s, graph: { ...s.graph, ...patch } }));
}

export function toggleRail(rail: Rail): void {
  setState((s) => ({ ...s, graph: { ...s.graph, rails: { ...s.graph.rails, [rail]: !s.graph.rails[rail] } } }));
}

export function toggleOverlay(overlay: "cycle" | "token"): void {
  setState((s) => ({ ...s, graph: { ...s.graph, overlays: { ...s.graph.overlays, [overlay]: !s.graph.overlays[overlay] } } }));
}

export function selectGraphNode(key: NodeKey): void {
  updateGraph({ selection: { kind: "node", key } });
}

export function togglePin(tx: string): void {
  if (!guard(!role().readOnly, "Auditors can view evidence but not change it.")) return;
  setState((s) => ({ ...s, graph: { ...s.graph, pinned: s.graph.pinned.includes(tx) ? s.graph.pinned.filter((x) => x !== tx) : [...s.graph.pinned, tx] } }));
}

export function pinMany(ids: string[]): void {
  if (!guard(!role().readOnly, "Auditors can view evidence but not change it.")) return;
  setState((s) => ({ ...s, graph: { ...s.graph, pinned: Array.from(new Set([...s.graph.pinned, ...ids])) } }));
  toast(`${ids.length} path transactions pinned.`);
}

export function saveEvidenceSubgraph(): void {
  const s0 = getState();
  if (!guard(!role().readOnly, "Auditors can view evidence but not change it.")) return;
  if (!guard(!s0.sim.graph, "Saving a new snapshot needs the graph service.")) return;
  const snap = s0.data.driftFixed ? "GS-0412-b" : "GS-0412-a";
  setState((s) => withEvents({ ...s, graph: { ...s.graph, pinSaved: true } }, [{ type: "Graph", action: `Evidence subgraph saved with ${s.graph.pinned.length} pinned edges as ${snap}`, object: "CASE-0412" }]));
  toast("Evidence subgraph saved to CASE-0412.");
}

export function toggleExpand(): void {
  const expanded = !getState().graph.expanded;
  updateGraph({ expanded });
  toast(expanded ? "Expanded ACC-2208: found ACC-3391 through device DV-77." : "Collapsed ACC-2208 neighbors.");
}

// ---------- entities ----------

export function decideEntityLink(linkKey: string, accountId: string, linkType: string, withId: string, token: string, decision: "Approved" | "Rejected"): void {
  if (!guard(!role().readOnly && role().canAccessCases, "Your role cannot change entity links.")) return;
  setState((s) =>
    withEvents({ ...s, erDecisions: { ...s.erDecisions, [linkKey]: decision } }, [
      { type: "Entity", action: decision === "Approved" ? `Link approved: ${accountId} ${linkType} ${withId} (human decision)` : `Link candidate rejected: ${accountId} ${linkType} ${withId}`, object: token },
    ]),
  );
  toast(decision === "Approved" ? "Link approved and logged. No accounts were merged." : "Candidate rejected and logged.");
}

export function decideScreening(decision: "False positive" | "Needs review"): void {
  if (!guard(!role().readOnly, "Auditors cannot record dispositions.")) return;
  setState((s) =>
    withEvents({ ...s, screeningDecision: decision }, [{ type: "Screening", action: decision === "False positive" ? "Mock screening match dispositioned as false positive" : "Mock screening match sent for second review", object: "ACC-2208" }]),
  );
  toast(decision === "False positive" ? "Recorded as a false positive." : "Sent for second review.");
}

// ---------- copilot ----------

export function copilotBlockedReason(state: DemoState): string | null {
  if (state.sim.llm) return "The LLM is unavailable. Use the evidence panel and graph while it recovers.";
  if (ROLES[state.role].readOnly) return "Auditors can read transcripts but cannot ask questions.";
  if (state.copilot.contextCase !== "CASE-0412") return "Switch back to CASE-0412 to ask questions.";
  return null;
}

export function askCopilot(text: string, key?: AnswerKey): void {
  const s0 = getState();
  const blocked = copilotBlockedReason(s0);
  if (blocked) {
    toast(blocked);
    return;
  }
  const answerKey = key ?? routeIntent(text);
  setState((s) => {
    const id = s.copilot.nextId;
    const next: DemoState = {
      ...s,
      copilot: { ...s.copilot, nextId: id + 2, messages: [...s.copilot.messages, { id, who: "user", text }, { id: id + 1, who: "ai", answerKey, showTrace: false, feedback: "" }] },
      caseWork: answerKey === "benign" ? { ...s.caseWork, counterReviewed: true } : s.caseWork,
    };
    if (answerKey === "deny") return withEvents(next, [{ type: "Access", action: "Copilot request denied: outside case scope or unbounded query", object: "CASE-0412" }]);
    if (answerKey === "inject") return withEvents(next, [{ type: "AI", action: "Verifier blocked 2 injected claims before display", object: "CASE-0412" }]);
    return next;
  });
}

export function recordCopilotFeedback(id: number, helpful: boolean): void {
  if (!guard(!role().readOnly, "Auditors can read transcripts but cannot leave feedback.")) return;
  setState((s) =>
    withEvents(
      { ...s, copilot: { ...s.copilot, messages: s.copilot.messages.map((m) => (m.id === id ? { ...m, feedback: helpful ? "Marked helpful. Saved to the audit trail." : "Marked not helpful. Saved to the audit trail." } : m)) } },
      [{ type: "AI", action: `Investigator feedback on copilot answer ${id}: ${helpful ? "helpful" : "not helpful"}`, object: "CASE-0412" }],
    ),
  );
}

/** Adds a copilot answer to the case as a note; it is re-verified before it can enter the narrative. */
export function addAnswerToCase(title: string): void {
  if (!guard(!role().readOnly && role().canDecide, "Your role cannot add to the case.")) return;
  setState((s) =>
    withEvents(
      { ...s, caseWork: { ...s.caseWork, notes: [...s.caseWork.notes, { by: role().name, at: "Now", text: `Copilot answer added for narrative review: ${title}` }] } },
      [{ type: "Case", action: `Copilot answer added to case notes for narrative review: ${title}`, object: "CASE-0412" }],
    ),
  );
  toast("Added to the case notes. It is re-verified before it can enter the narrative.");
}

export function updateMessage(id: number, patch: { showTrace?: boolean; feedback?: string }): void {
  setState((s) => ({ ...s, copilot: { ...s.copilot, messages: s.copilot.messages.map((m) => (m.id === id ? { ...m, ...patch } : m)) } }));
}

export function setCopilotFocus(id: string): void {
  setState((s) => ({ ...s, copilot: { ...s.copilot, focus: id } }));
}

export function clearChat(): void {
  setState((s) => ({ ...s, copilot: { ...s.copilot, messages: [] } }));
}

export function switchCopilotCase(to: CaseId): void {
  if (!guard(role().canAccessCases)) return;
  setState((s) =>
    withEvents({ ...s, modal: null, copilot: { ...s.copilot, contextCase: to, messages: [] } }, [{ type: "Access", action: `Copilot context switched from ${s.copilot.contextCase} to ${to}; chat cleared`, object: to }]),
  );
  toast(`Context switched to ${to}. Chat cleared to avoid cross-case leakage.`);
}


// ---------- case ----------

export function addNote(text: string): boolean {
  if (!guard(!role().readOnly, "Auditors cannot add notes.")) return false;
  if (text.trim().length < 3) return false;
  setState((s) => withEvents({ ...s, caseWork: { ...s.caseWork, notes: [...s.caseWork.notes, { by: role().name, at: "Now", text: text.trim() }] } }, [{ type: "Case", action: "Note added", object: "CASE-0412" }]));
  toast("Note added to the case and audit trail.");
  return true;
}

export function setCounterReviewed(value: boolean): void {
  if (role().readOnly) return;
  setState((s) => ({ ...s, caseWork: { ...s.caseWork, counterReviewed: value } }));
}

function canEditNarrative(): boolean {
  return guard(!role().readOnly && role().canDecide, "Your role cannot edit the narrative.");
}

function nextRevision(s: DemoState, by: string, note: string): DemoState["caseWork"]["revisions"] {
  return [{ revision: s.caseWork.revisions[0].revision + 1, by, note }, ...s.caseWork.revisions];
}

export function applyVerifiedValue(): void {
  if (!canEditNarrative()) return;
  setState((s) => {
    const revisions = nextRevision(s, role().name, "Used verified value; re-verified");
    return withEvents({ ...s, caseWork: { ...s.caseWork, claimFixed: true, showDiff: true, revisions } }, [{ type: "AI", action: `Blocked claim replaced with verified value; narrative r${revisions[0].revision}`, object: "CASE-0412" }]);
  });
  toast(`Claim replaced from evidence. Revision r${getState().caseWork.revisions[0].revision} saved.`);
}

export function openClaimEditor(): void {
  if (!canEditNarrative()) return;
  openModal({ kind: "editClaim" });
}

/** Returns an error message, or null when the edit passed verification and was saved. */
export function saveClaimEdit(text: string): string | null {
  if (!canEditNarrative()) return "Your role cannot edit the narrative.";
  const error = verifyClaimEdit(text);
  if (error) return error;
  setState((s) => {
    const revisions = nextRevision(s, role().name, "Analyst edit; re-verified");
    return withEvents({ ...s, modal: null, caseWork: { ...s.caseWork, claimFixed: true, showDiff: true, revisions } }, [{ type: "AI", action: `Analyst edit re-verified against evidence; narrative r${revisions[0].revision}`, object: "CASE-0412" }]);
  });
  toast(`Edit re-verified. Revision r${getState().caseWork.revisions[0].revision} saved.`);
  return null;
}

export function regenerateBlocked(state: DemoState): string | null {
  if (state.sim.llm) return "Drafting is paused while the LLM is unavailable. Edit manually instead.";
  if (!state.policy.aiDrafting) return "AI drafting is turned off in Governance. Edit manually instead.";
  if (ROLES[state.role].readOnly || !ROLES[state.role].canDecide) return "Your role cannot redraft.";
  return null;
}

export function commitRegeneratedDraft(): void {
  const blocked = regenerateBlocked(getState());
  if (blocked) {
    toast(blocked);
    return;
  }
  setState((s) => {
    const revisions = nextRevision(s, "AI redraft", s.caseWork.claimFixed ? "Verifier: 0 blocked" : "Verifier: 1 blocked again");
    return withEvents({ ...s, caseWork: { ...s.caseWork, revisions } }, [{ type: "AI", action: "Narrative redrafted; verifier re-ran", object: "CASE-0412" }]);
  });
}

export function toggleDiff(): void {
  setState((s) => ({ ...s, caseWork: { ...s.caseWork, showDiff: !s.caseWork.showDiff } }));
}

/** Why the current role cannot record a disposition right now, or null when it can. */
export function dispositionBlocked(state: DemoState): string | null {
  const r = ROLES[state.role];
  if (r.readOnly) return "Auditors are read-only.";
  if (!r.canDecide) return "Your role cannot decide cases.";
  if (state.caseWork.status !== "In review") return `A disposition is already recorded: ${state.caseWork.status}.`;
  return null;
}

const ESCALATION_LOCKED = "Locked until the narrative is verified and the data hold is resolved.";

export function setDisposition(choice: DispositionChoice): void {
  if (!guard(dispositionBlocked(getState()) === null, dispositionBlocked(getState()) ?? undefined)) return;
  if (choice === "escalate" && !isCaseVerified(getState())) {
    toast(ESCALATION_LOCKED);
    return;
  }
  setState((s) => ({ ...s, caseWork: { ...s.caseWork, disposition: choice } }));
}

export function setRationale(text: string): void {
  if (dispositionBlocked(getState())) return;
  setState((s) => ({ ...s, caseWork: { ...s.caseWork, rationale: text } }));
}

/** Validates and opens the confirmation dialog. Returns an inline error if invalid. */
export function submitDisposition(): string | null {
  const s = getState();
  const blocked = dispositionBlocked(s);
  if (blocked) return blocked;
  if (!s.caseWork.disposition) return "Choose a disposition.";
  if (s.caseWork.disposition === "escalate" && !isCaseVerified(s)) return ESCALATION_LOCKED;
  if (s.caseWork.rationale.trim().length < MIN_RATIONALE) return `Write a rationale of at least ${MIN_RATIONALE} characters. It is saved in the audit trail.`;
  openModal({ kind: "confirm", action: s.caseWork.disposition });
  return null;
}

const NEXT_STATUS: Record<ConfirmAction, CaseStatus> = {
  escalate: "Pending approval",
  close: "Closed, no further action",
  more: "Awaiting information",
  approve: "Escalated for SAR-support review",
  return: "In review",
};

/** Why the current role cannot act as the second reviewer, or null when it can. */
export function approvalBlocked(state: DemoState): string | null {
  if (state.caseWork.status !== "Pending approval") return "There is no escalation waiting for approval.";
  if (!ROLES[state.role].canApproveEscalation) return "Only a BSA officer can act as the second reviewer.";
  if (state.caseWork.escalatedBy === state.role) return "You requested this escalation. A different reviewer must approve it.";
  return null;
}

export function confirmCaseAction(action: ConfirmAction): void {
  const s0 = getState();
  if (action === "approve" || action === "return") {
    const blocked = approvalBlocked(s0);
    if (!guard(blocked === null, blocked ?? undefined)) {
      closeModal();
      return;
    }
  } else {
    const blocked = dispositionBlocked(s0) ?? (action === "escalate" && !isCaseVerified(s0) ? ESCALATION_LOCKED : null);
    if (!guard(blocked === null, blocked ?? undefined)) {
      closeModal();
      return;
    }
  }
  const status = NEXT_STATUS[action];
  const text = action === "approve" ? "Escalation approved by second reviewer" : action === "return" ? "Escalation returned to analyst" : `Disposition recorded: ${status}. Rationale saved`;
  setState((s) =>
    withEvents(
      {
        ...s,
        modal: null,
        caseWork: {
          ...s.caseWork,
          status,
          disposition: action === "return" ? null : s.caseWork.disposition,
          escalatedBy: action === "escalate" ? s.role : action === "return" ? null : s.caseWork.escalatedBy,
        },
      },
      [{ type: "Case", action: text, object: "CASE-0412" }],
    ),
  );
  toast(action === "escalate" ? "Sent for BSA officer approval. Nothing is filed automatically." : "Recorded in the audit trail.");
}

export function toggleReportFormat(format: "json" | "csv" | "pdf"): void {
  if (!guard(!role().readOnly, "Auditors can download existing bundles but not configure new ones.")) return;
  setState((s) => ({ ...s, caseWork: { ...s.caseWork, report: { ...s.caseWork.report, formats: { ...s.caseWork.report.formats, [format]: !s.caseWork.report.formats[format] } } } }));
}

/** Builds the bundle. Re-checks blockers so a stale UI can never export an unverified case. */
export function completeBundle(): void {
  const blockers = bundleBlockers(getState(), role().readOnly);
  if (blockers.length > 0) {
    toast(blockers[0]);
    return;
  }
  setState((s) => {
    const revision = s.caseWork.revisions[0].revision;
    const at = demoTimestamp(s.seq + 1);
    const hash = simulatedHash(`CASE-0412|r${revision}|${at}`, 16);
    const n = s.exports.filter((x) => x.caseId === "CASE-0412").length + 1;
    const id = `EXP-0412-0${n}`;
    const by = ROLES[s.role].name;
    return withEvents(
      {
        ...s,
        caseWork: { ...s.caseWork, verifyResult: "", report: { ...s.caseWork.report, done: true, exportId: id, revision, hash, at, by } },
        exports: [{ id, caseId: "CASE-0412", by, at, revision: `r${revision}`, hash }, ...s.exports],
      },
      [{ type: "Export", action: `Evidence bundle ${id} created (narrative r${revision})`, object: "CASE-0412" }],
    );
  });
  toast("Bundle ready. Manifest and file hashes recorded.");
}

export function verifyBundleHash(): void {
  setState((s) => {
    const ok = s.caseWork.revisions[0].revision === s.caseWork.report.revision;
    const verifyResult = ok
      ? "Hash matches the manifest. Bundle unchanged since export."
      : `Mismatch: the narrative changed after export (r${s.caseWork.report.revision} to r${s.caseWork.revisions[0].revision}). Generate a new bundle.`;
    return withEvents({ ...s, caseWork: { ...s.caseWork, verifyResult } }, [{ type: "Export", action: `Bundle hash verified: ${ok ? "match" : "mismatch"}`, object: "CASE-0412" }]);
  });
}

// ---------- governance ----------

export function exportAuditTrail(): void {
  setState((s) => withEvents(s, [{ type: "Access", action: "Audit trail exported (CSV)", object: "audit_events" }]));
  toast("Audit CSV prepared (simulated). The export itself was logged.");
}

/** Saves a new structuring-like band. Drafts stay in the form until this succeeds. */
export function saveBand(min: string, max: string): boolean {
  if (!guard(role().canEditPolicy)) return false;
  if (bandError(min, max)) return false;
  const bandMin = min.trim();
  const bandMax = max.trim();
  setState((s) =>
    withRecompute(
      s,
      withEvents({ ...s, policy: { ...s.policy, bandMin, bandMax } }, [{ type: "Policy", action: `Structuring-like review band set to $${bandMin} to $${bandMax} (new rule version)`, object: "typology-rules" }]),
      "Threshold",
      `Structuring-like review band changed from $${s.policy.bandMin}-$${s.policy.bandMax} to $${bandMin}-$${bandMax}`,
    ),
  );
  toast("Band saved as a new rule version. Alerts will re-evaluate.");
  return true;
}

function nextPolicyVersion(version: string): string {
  const match = /^v(\d+)\.(\d+)$/.exec(version);
  return match ? `v${match[1]}.${Number(match[2]) + 1}` : version;
}

/** Saves priority weights as a new policy version; the queue only re-ranks after this. */
export function saveWeights(weights: Record<WeightKey, string>): boolean {
  if (!guard(role().canEditPolicy)) return false;
  if (!parseWeights(weights)) return false;
  const next = nextPolicyVersion(getState().policy.version);
  setState((s) =>
    withRecompute(
      s,
      withEvents({ ...s, policy: { ...s.policy, weights: { ...weights }, version: next } }, [
        { type: "Policy", action: `Priority policy weights saved as ${next}: ${Object.values(weights).join("/")}`, object: "priority-policy" },
      ]),
      "Policy",
      `Priority weights saved as ${next} (was ${s.policy.version})`,
    ),
  );
  toast(`Priority policy saved as ${next}. The queue re-ranked.`);
  return true;
}

export function requestAiToggle(): void {
  const s = getState();
  if (!guard(role().canEditPolicy, "Only a BSA officer can change AI scope.")) return;
  if (s.policy.aiDrafting) {
    openModal({ kind: "killSwitch" });
    return;
  }
  setState((st) => withEvents({ ...st, policy: { ...st.policy, aiDrafting: true } }, [{ type: "Policy", action: "AI drafting turned on", object: "ai-scope" }]));
  toast("AI drafting is on.");
}

export function confirmKillSwitch(): void {
  if (!guard(role().canEditPolicy, "Only a BSA officer can change AI scope.")) {
    closeModal();
    return;
  }
  setState((s) => withEvents({ ...s, modal: null, policy: { ...s.policy, aiDrafting: false } }, [{ type: "Policy", action: "AI drafting turned off (kill switch)", object: "ai-scope" }]));
  toast("AI drafting is off. Cases continue from verified facts.");
}

export function toggleFlag(flag: FeatureFlagKey): void {
  if (!guard(role().canEditPolicy, "Only a BSA officer can change feature flags.")) return;
  setState((s) => {
    const on = !s.policy.flags[flag];
    return withEvents({ ...s, policy: { ...s.policy, flags: { ...s.policy.flags, [flag]: on } } }, [{ type: "Policy", action: `Feature flag ${flag} turned ${on ? "on" : "off"}`, object: "feature-flags" }]);
  });
}

// ---------- section 20.1 stretch features ----------

export function setGraphLens(lens: GraphView["lens"]): void {
  updateGraph({ lens });
}

export function recordPassport(alertId: AlertId, packageHash: string, document: unknown): void {
  setState((s) => {
    const n = s.passports.filter((p) => p.alertId === alertId).length + 1;
    const id = `EP-${alertId.slice(3)}-${String(n).padStart(2, "0")}`;
    const next = withEvents(s, [{ type: "Export", action: `Evidence passport ${id} issued for ${alertId} (sha256 ${packageHash.slice(0, 12)}…)`, object: alertId }]);
    return { ...next, passports: [{ id, alertId, at: demoTimestamp(next.seq), by: ROLES[s.role].name, packageHash, document }, ...next.passports] };
  });
  toast("Evidence passport issued. Section hashes and package hash recorded.");
}

export function recordPassportCheck(alertId: AlertId, valid: boolean, detail: string): void {
  setState((s) => withEvents(s, [{ type: "Export", action: `Evidence passport verified: ${valid ? "intact" : `mismatch (${detail})`}`, object: alertId }]));
}

export function recordReplay(pin: string, reproduced: number, nonDeterministic: number, unavailable: number): void {
  setState((s) => {
    const id = `RP-${String(s.replays.length + 1).padStart(3, "0")}`;
    const next = withEvents(s, [
      { type: "Model", action: `Investigation replay ${id} against ${pin}: ${reproduced} reproduced, ${nonDeterministic} non-deterministic, ${unavailable} unavailable`, object: "CASE-0412" },
    ]);
    return { ...next, replays: [{ id, pin, at: demoTimestamp(next.seq), by: ROLES[s.role].name, reproduced, nonDeterministic, unavailable }, ...next.replays] };
  });
}

/** Records a sandbox result as a proposal. Production policy is never changed from the sandbox. */
export function saveSandboxProposal(summary: string): void {
  if (!guard(role().canEditPolicy, "Only a BSA officer can record sandbox proposals.")) return;
  setState((s) => {
    const id = `SB-${String(s.sandboxProposals.length + 1).padStart(3, "0")}`;
    const next = withEvents(s, [{ type: "Policy", action: `Sandbox proposal ${id} recorded, not applied to production: ${summary}`, object: "priority-policy" }]);
    return { ...next, sandboxProposals: [{ id, at: demoTimestamp(next.seq), by: ROLES[s.role].name, summary }, ...next.sandboxProposals] };
  });
  toast("Proposal recorded. Production policy is unchanged.");
}
