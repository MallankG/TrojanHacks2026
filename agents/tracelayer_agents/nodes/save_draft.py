"""After each `verify_evidence`: store the draft on the case, set the case status the reviewer sees
(`awaiting_review`, or `needs_revision` while verifier errors remain) and audit both steps."""

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from tracelayer_agents import audit
from tracelayer_agents.schemas import AuditEvent, CaseStatus, SaveDraftOutput
from tracelayer_agents.wrappers import NodeContext, traced_node


@traced_node("save_draft", SaveDraftOutput)
async def save_draft(state: Mapping[str, Any], ctx: NodeContext) -> SaveDraftOutput:
    cases, repo = ctx.services.cases, ctx.services.audit
    case_id = state["case_id"]
    draft = state["draft_narrative"]
    meta = state["llm_meta"]
    errors = state.get("verifier_errors") or []
    flagged = state.get("flagged_claims") or []
    status = CaseStatus.NEEDS_REVISION if errors else CaseStatus.AWAITING_REVIEW

    def persist() -> list[AuditEvent]:
        case = cases.get_case(case_id)
        if case is None:
            raise KeyError(f"case {case_id!r} was not opened by load_case")
        receipt_writer = getattr(cases, "save_draft_receipt", None)
        if receipt_writer:
            receipt_writer(case_id, draft, state)
        else:
            cases.save_draft(case_id, draft)
            cases.update_case(case.model_copy(update={"status": status,
                                                      "updated_at": datetime.now(UTC)}))
        # metadata must be flat JSON scalars, so lists are comma-joined
        drafted = audit.record(
            repo, action="case.draft_generated", object_type="case", object_id=case_id,
            prompt_version=draft.prompt_version,
            metadata={
                "trace_id": state.get("trace_id"),
                "alert_id": state.get("alert_id"),
                "provider": draft.provider,
                "model": draft.model,
                "prompt_version": draft.prompt_version,
                "bundle_hash": state["evidence_bundle"].bundle_hash,
                "attempts": state.get("draft_attempts"),
                "llm_calls": meta.attempts,
                "fallback_used": draft.fallback_used,
            },
        )
        verified = audit.record(
            repo, action="case.verified", object_type="case", object_id=case_id,
            metadata={
                "trace_id": state.get("trace_id"),
                "error_count": len(errors),
                "codes": ",".join(sorted({e.code for e in errors})),
                "flagged_claims": ",".join(str(i) for i in flagged),
                "case_status": str(status),
            },
        )
        return [drafted, verified]

    events = await asyncio.to_thread(persist)
    return SaveDraftOutput(case_status=status, audit_events=events)
