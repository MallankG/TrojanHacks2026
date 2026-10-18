"""Pure mappings from facade results to the backend's request bodies and tables
(`backend/app/schemas.py` AgentRunCreate/AgentRunUpdate/NarrativeCreate/NarrativeVerification/
EvidenceIn/DecisionIn, and the `audit_events` table). No DB or HTTP calls; every function returns
plain JSON-ready dicts.

Their id columns are uuid, ours are strings (`TX-…`, `ACC-…`). `to_uuid` maps ours with uuid5
under one namespace per id kind, so the same id always maps to the same uuid; the original id is
kept in a text/jsonb field wherever their schema has one (`UNHOLDABLE_ORIGINAL_IDS` lists where it
has none).
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID, uuid5

from tracelayer_agents.audit import sha256_json
from tracelayer_agents.schemas import (
    AuditEvent,
    CaseDecisionResult,
    CaseDraftResult,
    CaseStatus,
    Decision,
    EvidenceBundle,
    NarrativeDraft,
    TraceEvent,
)

TRACELAYER_NS = UUID("6f1d0c1e-7a43-5b8e-9a51-3c2f4e8d7b10")
ID_KINDS = ("transaction", "account", "alert", "case", "chunk", "agent_run", "narrative",
            "evidence", "actor", "audit_event")
NAMESPACES = {kind: uuid5(TRACELAYER_NS, kind) for kind in ID_KINDS}

# Where their schema has no field for our original string id.
UNHOLDABLE_ORIGINAL_IDS = (
    "CitationIn.policy_chunk_id: no text field (only quoted_text); FK to policy_chunks",
    "NarrativeCreate.agent_run_id / parent_narrative_id: no text field",
    "NarrativeCreate draft_hash / version / sections: no column; kept in each claim's "
    "referenced_values and rendered into content",
    "DecisionIn.narrative_id: no text field",
    "EvidenceIn.alert_id / account_id / policy_chunk_id: kept in payload, but the uuid columns "
    "are FKs to alerts / accounts / policy_chunks",
    "Route params case_id / run_id / narrative_id: uuid path params, no text field",
)

STEP_STATUS = {"ok": "completed", "error": "failed", "skipped": "skipped"}
CLAIM_KIND = {"fact": "fact", "hypothesis": "hypothesis", "policy": "fact"}
DECISION = {  # our action -> (review_decision, case_disposition)
    "escalate": ("escalate", "escalate_for_sar_consideration"),
    "dismiss": ("reject", "no_further_action"),
    "request_more_data": ("edit", "needs_more_information"),
}
_AWAITING = {CaseStatus.OPEN, CaseStatus.DRAFTED, CaseStatus.AWAITING_REVIEW,
             CaseStatus.NEEDS_REVISION, CaseStatus.HELD_FOR_DATA_REVIEW}
_ACTION_RE = re.compile(r"^[a-z_]+\.[a-z_]+$")


def to_uuid(kind: str, our_id: str) -> str:
    """Deterministic uuid5 for one of our ids (as a string, ready for JSON)."""
    return str(uuid5(NAMESPACES[kind], our_id))


def _uuids(kind: str, ids: list[str]) -> list[str]:
    return [to_uuid(kind, i) for i in ids]


# --------------------------------------------------------------------------- agent runs


def agent_run_create(result: CaseDraftResult, graph_name: str = "case",
                     graph_version: str | None = None) -> dict[str, Any]:
    """`POST /cases/{case_id}/agent-runs` body (AgentRunCreate)."""
    meta = result.llm_meta
    return {
        "graph_name": graph_name,
        "graph_version": graph_version,
        "alert_id": to_uuid("alert", result.alert_id),
        "llm_provider": meta.provider if meta else None,
        "llm_model": meta.model if meta else None,
        "prompt_version": meta.prompt_version if meta else None,
        "config": {
            "trace_id": result.trace_id,
            "original_alert_id": result.alert_id,
            "original_case_id": result.case_id,
            "input_hash": meta.input_hash if meta else None,
            "fallback_used": meta.fallback_used if meta else None,
            "precomputed": result.precomputed,
        },
    }


def agent_step(event: TraceEvent) -> dict[str, Any]:
    return {
        "name": event.node,
        "status": STEP_STATUS[event.status],
        "detail": {"trace_id": event.trace_id, "duration_ms": event.duration_ms,
                   "input_hash": event.input_hash, "output_hash": event.output_hash,
                   "error": event.error},
        "at": event.ended_at.isoformat(),
    }


def run_status(trace: list[TraceEvent], case_status: CaseStatus | None) -> str:
    if any(e.status == "error" for e in trace) or case_status is None:
        return "failed"
    return "awaiting_review" if case_status in _AWAITING else "completed"


def agent_run_update(trace: list[TraceEvent], case_status: CaseStatus | None) -> dict[str, Any]:
    """`PATCH /agent-runs/{run_id}` body (AgentRunUpdate); steps go through `append_agent_steps`."""
    errors = [e.error for e in trace if e.status == "error" and e.error]
    return {
        "status": run_status(trace, case_status),
        "current_step": trace[-1].node if trace else None,
        "append_steps": [agent_step(e) for e in trace],
        "error_message": errors[0] if errors else None,
    }


# --------------------------------------------------------------------------- narrative


SECTION_TITLES = {
    "observed_facts": "Observed facts",
    "model_rule_inferences": "Model and rule inferences",
    "analyst_hypotheses": "Analyst hypotheses",
    "contradictory_evidence": "Contradictory evidence",
    "data_gaps": "Data gaps",
    "relevant_policy": "Relevant policy",
    "recommended_next_steps": "Recommended next steps",
}


def _claim_sections(draft: NarrativeDraft) -> dict[str, str]:
    """claim_type -> the v4 section its claims appear in."""
    return {"fact": "observed_facts", "hypothesis": "analyst_hypotheses",
            "policy": "relevant_policy"} if draft.sections is not None else {}


def narrative_content(draft: NarrativeDraft) -> str:
    """The narrative as text: the summary, then each v4 section with its evidence ids (or, for a
    pre-v4 draft, the claims)."""
    if draft.sections is None:
        return "\n\n".join([draft.summary, *(c.text for c in draft.observed_facts)])
    parts = [draft.summary]
    for name, title in SECTION_TITLES.items():
        items = getattr(draft.sections, name)
        if items:
            lines = [f"- {i.text}" + (f" [{', '.join(i.evidence_ids)}]" if i.evidence_ids else "")
                     for i in items]
            parts.append(f"## {title}\n" + "\n".join(lines))
    return "\n\n".join(parts)


def _narrative_body(draft: NarrativeDraft, draft_hash: str, agent_run_uuid: str | None,
                    parent_narrative_uuid: str | None) -> dict[str, Any]:
    sections = _claim_sections(draft)
    claims = [{
        "claim_text": c.text,
        "claim_kind": CLAIM_KIND[c.claim_type],
        "referenced_transaction_ids": _uuids("transaction", c.transaction_ids),
        "referenced_account_ids": _uuids("account", c.account_ids),
        "referenced_values": {
            "claim_type": c.claim_type,
            "original_transaction_ids": c.transaction_ids,
            "original_account_ids": c.account_ids,
            "original_chunk_ids": c.citation_chunk_ids,
            "amounts": [str(a) for a in c.amounts],
            "dates": [d.isoformat() for d in c.dates],
            "section": sections.get(c.claim_type),
            "draft_hash": draft_hash,
            "draft_version": draft.version,
        },
        "cited_chunk_ids": _uuids("chunk", c.citation_chunk_ids),
        "verification": "pending",
        "verifier_notes": None,
    } for c in draft.observed_facts]
    claims += [{"claim_text": u, "claim_kind": "unknown", "verification": "pending"}
               for u in draft.uncertainties if u.strip()]
    return {
        "content": narrative_content(draft),
        "parent_narrative_id": parent_narrative_uuid,
        "agent_run_id": agent_run_uuid,
        "llm_provider": draft.provider,
        "llm_model": draft.model,
        "prompt_version": draft.prompt_version,
        "claims": claims,
        "citations": [{"policy_chunk_id": to_uuid("chunk", c), "quoted_text": None}
                      for c in dict.fromkeys(draft.citations)],
    }


def narrative_create(result: CaseDraftResult,
                     agent_run_uuid: str | None = None) -> dict[str, Any]:
    """`POST /cases/{case_id}/narratives` body (NarrativeCreate). Claim i is
    `draft.observed_facts[i]` (so `claim_index` matches our `flagged_claims`), then one `unknown`
    claim per uncertainty. The v4 sections are rendered into `content`; `draft_hash` and the
    section of each claim go in `referenced_values` (no column for them)."""
    draft = result.draft
    if draft is None:
        raise ValueError("result has no draft")
    return _narrative_body(draft, result.draft_hash or sha256_json(draft), agent_run_uuid, None)


def narrative_uuid(draft_hash: str) -> str:
    """A stable narrative uuid for a draft version, from its `draft_hash`."""
    return to_uuid("narrative", draft_hash)


def narrative_edit_create(result: CaseDecisionResult, parent_narrative_uuid: str,
                          agent_run_uuid: str | None = None) -> dict[str, Any]:
    """NarrativeCreate body for the reviewer's edited version (`result.edited_draft`), as a child
    of the drafted narrative. The edit itself is audited as `case.narrative_edited`."""
    draft = result.edited_draft
    if draft is None:
        raise ValueError("result has no edited draft")
    return _narrative_body(draft, result.edited_draft_hash or sha256_json(draft), agent_run_uuid,
                           parent_narrative_uuid)


def narrative_verification(result: CaseDraftResult) -> dict[str, Any]:
    """`PUT /narratives/{narrative_id}/verification` body (NarrativeVerification), indexed like
    `narrative_create`'s claims."""
    if result.status != "ok" or result.draft is None:
        return {"verification_status": "failed",
                "verification_report": {"error": result.error}, "claims": []}
    by_index: dict[int, list[str]] = {}
    for e in result.verifier_errors:
        if e.claim_index is not None and e.claim_index >= 0:
            by_index.setdefault(e.claim_index, []).append(e.code)
    flagged = set(result.flagged_claims) | set(by_index)
    claims = []
    for i, c in enumerate(result.draft.observed_facts):
        if i in flagged:
            claims.append({"claim_index": i, "verification": "unsupported",
                           "verifier_notes": ",".join(by_index.get(i, ["flagged"]))})
        else:
            claims.append({"claim_index": i, "verification": (
                "not_applicable" if c.claim_type == "hypothesis" else "supported"),
                "verifier_notes": None})
    n = len(claims)
    claims += [{"claim_index": n + j, "verification": "not_applicable", "verifier_notes": None}
               for j, u in enumerate(u for u in result.draft.uncertainties if u.strip())]
    status = ("flagged" if result.verifier_errors or result.flagged_claims
              or result.case_status == CaseStatus.NEEDS_REVISION else "passed")
    return {
        "verification_status": status,
        "verification_report": {
            "errors": [e.model_dump(mode="json") for e in result.verifier_errors],
            "free_text_errors": [e.code for e in result.verifier_errors
                                 if e.claim_index is None or e.claim_index < 0],
            "flagged_claims": sorted(flagged),
            "case_status": result.case_status.value if result.case_status else None,
        },
        "claims": claims,
    }


# --------------------------------------------------------------------------- evidence


def case_evidence_rows(bundle: EvidenceBundle) -> list[dict[str, Any]]:
    """`POST /cases/{case_id}/evidence` bodies (EvidenceIn), one per evidence item."""
    rows: list[dict[str, Any]] = [{
        "evidence_type": "alert",
        "explanation": f"Alert {bundle.alert_id} evidence bundle",
        "source_ref": bundle.alert_id,
        "alert_id": to_uuid("alert", bundle.alert_id),
        "payload": {"original_alert_id": bundle.alert_id, "bundle_hash": bundle.bundle_hash,
                    "lineage_refs": bundle.lineage_refs},
    }]
    for t in bundle.transactions:
        rows.append({
            "evidence_type": "transaction",
            "explanation": f"Transaction {t.transaction_id} in the alert's evidence",
            "source_ref": t.source_row_ref,
            "transaction_ids": [to_uuid("transaction", t.transaction_id)],
            "payload": {"original_transaction_id": t.transaction_id, **t.model_dump(mode="json")},
        })
    findings = bundle.flow_findings
    if findings is not None:
        for kind in ("paths", "cycles", "fan_in", "fan_out", "pass_through"):
            for p in getattr(findings, kind):
                rows.append({
                    "evidence_type": "transaction_path",
                    "explanation": f"{kind} pattern over {len(p.transaction_ids)} transactions",
                    "source_ref": findings.graph_snapshot_id,
                    "transaction_ids": _uuids("transaction", p.transaction_ids),
                    "payload": {"pattern": kind, "original_transaction_ids": p.transaction_ids,
                                "original_account_ids": p.account_ids,
                                **p.model_dump(mode="json", exclude={"transaction_ids",
                                                                     "account_ids"})},
                })
    for q in bundle.quality_caveats:
        rows.append({
            "evidence_type": "data_quality_issue",
            "explanation": f"Data-quality caveat {q.rule_id} on {q.asset_id}",
            "source_ref": q.check_id,
            "payload": q.model_dump(mode="json"),
        })
    return rows


# --------------------------------------------------------------------------- decision & audit


def case_decision_row(decision: Decision, result: CaseDecisionResult | None = None,
                      narrative_uuid: str | None = None) -> dict[str, Any]:
    """`POST /cases/{case_id}/decisions` body (DecisionIn). Their schema has no field for
    `edited_summary`, so it is appended to `notes`."""
    review, disposition = DECISION[decision.action]
    notes = decision.reason
    if decision.edited_summary:
        notes += f"\n\nEdited summary: {decision.edited_summary}"
    return {"decision": review, "disposition": disposition, "narrative_id": narrative_uuid,
            "notes": notes[:4000]}


def audit_action(action: str) -> str:
    """Their `action` must match `^[a-z_]+\\.[a-z_]+$`."""
    out = re.sub(r"[^a-z_.]", "_", action.lower())
    head, _, tail = out.partition(".")
    out = f"{head or 'agent'}.{tail.replace('.', '_') or 'event'}"
    return out if _ACTION_RE.match(out) else "agent.event"


def audit_event_row(event: AuditEvent, tenant_id: str | None = None) -> dict[str, Any]:
    """One `audit_events` row. `object_id` is text there, so ours is kept as is."""
    actor_type = ("user" if event.actor_id and event.actor_role not in ("system", "agent")
                  else "agent" if event.actor_role == "agent" else "system")
    return {
        "event_id": to_uuid("audit_event", event.event_id),
        "tenant_id": tenant_id,
        "actor_id": to_uuid("actor", event.actor_id) if event.actor_id else None,
        "actor_type": actor_type,
        "action": audit_action(event.action),
        "object_type": event.object_type,
        "object_id": event.object_id,
        "occurred_at": event.timestamp.isoformat(),
        "request_id": event.metadata.get("trace_id"),
        "metadata": {
            **event.metadata,
            "original_event_id": event.event_id,
            "original_actor_id": event.actor_id,
            "original_action": event.action,
            "actor_role": event.actor_role,
            "before_hash": event.before_hash,
            "after_hash": event.after_hash,
            "prev_hash": event.prev_hash,
            "hash": event.hash,
            "model_versions": event.model_versions,
            "prompt_version": event.prompt_version,
            "reason": event.reason,
        },
    }
