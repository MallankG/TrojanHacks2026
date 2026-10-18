"""Apply the reviewer's decision to the case and audit it. Escalation is an internal status
change only: nothing is filed, frozen or sent to a customer."""

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from tracelayer_agents import audit
from tracelayer_agents.audit import sha256_json
from tracelayer_agents.schemas import DECISION_TO_STATUS, AuditEvent, RecordDecisionOutput
from tracelayer_agents.wrappers import NodeContext, traced_node


@traced_node("record_decision", RecordDecisionOutput)
async def record_decision(state: Mapping[str, Any], ctx: NodeContext) -> RecordDecisionOutput:
    cases, repo = ctx.services.cases, ctx.services.audit
    case_id = state["case_id"]
    decision = state["decision"]
    alert = state["alert"]
    draft = state.get("draft_narrative")
    status = DECISION_TO_STATUS[decision.action]

    def persist() -> AuditEvent:
        before = cases.get_case(case_id)
        if before is None:
            raise KeyError(f"unknown case_id {case_id!r}")
        after = before.model_copy(update={
            "status": status, "disposition": decision.action, "updated_at": datetime.now(UTC),
        })
        cases.update_case(after)
        return audit.record(
            repo, action="case.decision", object_type="case", object_id=case_id,
            actor_id=decision.actor_id, actor_role=decision.actor_role, reason=decision.reason,
            before_hash=sha256_json(before), after_hash=sha256_json(after),
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
            },
        )

    event = await asyncio.to_thread(persist)
    return RecordDecisionOutput(case_status=status, audit_events=[event])
