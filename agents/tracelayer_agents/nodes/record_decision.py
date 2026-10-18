"""Apply the reviewer's decision to the case and audit it. Escalation is an internal status
change only: nothing is filed, frozen or sent to a customer.

A decision with `edited_summary` first saves the reviewer's text as a new draft version
(version + 1, parent = the drafted version's `draft_hash`) and audits `case.narrative_edited`
with the parent hash, the new hash and the hash of the summary diff (never the text itself). The
edited version is re-verified for information; the reviewer owns it, so errors don't block."""

import asyncio
import difflib
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from tracelayer_agents import audit
from tracelayer_agents.audit import sha256_json
from tracelayer_agents.nodes.verify import verify_evidence
from tracelayer_agents.schemas import (
    DECISION_TO_STATUS,
    AuditEvent,
    NarrativeDraft,
    RecordDecisionOutput,
    VerifierError,
)
from tracelayer_agents.wrappers import NodeContext, traced_node


def summary_diff(before: str, after: str) -> str:
    return "\n".join(difflib.unified_diff(before.splitlines(), after.splitlines(),
                                          "drafted", "edited", lineterm=""))


@traced_node("record_decision", RecordDecisionOutput)
async def record_decision(state: Mapping[str, Any], ctx: NodeContext) -> RecordDecisionOutput:
    cases, repo = ctx.services.cases, ctx.services.audit
    case_id = state["case_id"]
    decision = state["decision"]
    alert = state["alert"]
    draft = state.get("draft_narrative")
    status = DECISION_TO_STATUS[decision.action]
    edited_text = (decision.edited_summary or "").strip()

    def persist() -> tuple[list[AuditEvent], NarrativeDraft | None, list[VerifierError]]:
        before = cases.get_case(case_id)
        if before is None:
            raise KeyError(f"unknown case_id {case_id!r}")
        events: list[AuditEvent] = []
        edited, edit_errors = None, []
        current = draft
        if draft is not None and edited_text and edited_text != draft.summary:
            parent_hash = sha256_json(draft)
            edited = draft.model_copy(update={
                "summary": edited_text, "version": draft.version + 1,
                "parent_draft_hash": parent_hash, "edited_by": decision.actor_id,
            })
            edit_errors = verify_evidence(edited, state["evidence_bundle"],
                                          state.get("retrieved_sources") or [], alert)
            cases.save_draft(case_id, edited)
            events.append(audit.record(
                repo, action="case.narrative_edited", object_type="case", object_id=case_id,
                actor_id=decision.actor_id, actor_role=decision.actor_role,
                before_hash=parent_hash, after_hash=sha256_json(edited),
                prompt_version=draft.prompt_version,
                metadata={
                    "trace_id": state.get("trace_id"),
                    "parent_draft_hash": parent_hash,
                    "draft_hash": sha256_json(edited),
                    "diff_hash": sha256_json(summary_diff(draft.summary, edited_text)),
                    "draft_version": edited.version,
                    "edited_chars": len(edited_text),
                    "verifier_error_count": len(edit_errors),
                    "verifier_codes": ",".join(sorted({e.code for e in edit_errors})),
                },
            ))
            current = edited
        after = before.model_copy(update={
            "status": status, "disposition": decision.action, "updated_at": datetime.now(UTC),
        })
        apply_decision = getattr(cases, "apply_decision", None)
        receipt = None
        if apply_decision:
            receipt = apply_decision(case_id, decision)
        else:
            cases.update_case(after)
        events.append(audit.record(
            repo, action="case.decision", object_type="case", object_id=case_id,
            actor_id=decision.actor_id, actor_role=decision.actor_role, reason=decision.reason,
            before_hash=receipt["before_hash"] if receipt else sha256_json(before),
            after_hash=receipt["after_hash"] if receipt else sha256_json(after),
            model_versions=alert.model_versions,
            prompt_version=draft.prompt_version if draft else None,
            metadata={
                "trace_id": state.get("trace_id"),
                "alert_id": alert.alert_id,
                "action": decision.action,
                "previous_status": str(before.status),
                "case_status": str(status),
                "bundle_hash": state["evidence_bundle"].bundle_hash,
                "graph_snapshot_id": alert.graph_snapshot_id,
                "draft_hash": sha256_json(current) if current is not None else None,
                "draft_version": current.version if current is not None else None,
            },
        ))
        return events, edited, edit_errors

    events, edited, edit_errors = await asyncio.to_thread(persist)
    return RecordDecisionOutput(case_status=status, audit_events=events, edited_draft=edited,
                                edited_verifier_errors=edit_errors)
