"""The human-in-the-loop step (CLAUDE.md rule 6). Pauses the case workflow with `interrupt()` and a
case packet for the reviewer; `workflows.case.resume_case` resumes it with the reviewer's decision.

Role rules are enforced here, not in the UI. A rejected decision raises `DecisionRejected`, the
node fails, and the run stays paused at this step until a valid decision comes in.
"""

from collections.abc import Mapping
from typing import Any

from langgraph.types import interrupt
from pydantic import ValidationError

from tracelayer_agents.schemas import CaseStatus, Decision, HumanDecisionOutput
from tracelayer_agents.wrappers import NodeContext, traced_node


class DecisionRejected(ValueError):
    """The decision is malformed or the actor's role may not take this action."""


def validate_decision(raw: Any, case_status: CaseStatus | str | None) -> Decision:
    try:
        decision = Decision.model_validate(
            raw.model_dump() if isinstance(raw, Decision) else raw
        )
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'decision'}: {e['msg']}" for e in exc.errors()
        )
        raise DecisionRejected(f"invalid decision: {problems}") from None
    # TODO(review): starter RBAC; confirm the role matrix with compliance.
    if (case_status == CaseStatus.NEEDS_REVISION and decision.action == "dismiss"
            and decision.actor_role != "supervisor"):
        raise DecisionRejected(
            "only a supervisor may dismiss a case whose draft still needs revision "
            f"(actor_role={decision.actor_role!r})"
        )
    return decision


def case_packet(state: Mapping[str, Any]) -> dict[str, Any]:
    """What the reviewer sees while the case is paused."""
    alert = state["alert"]
    draft = state.get("draft_narrative")
    errors = state.get("verifier_errors") or []
    return {
        "case_id": state.get("case_id"),
        "alert_id": alert.alert_id,
        "tier": alert.tier,
        "composite_score": alert.composite_score,
        "bundle_hash": state["evidence_bundle"].bundle_hash,
        "draft_summary": draft.summary if draft else None,
        "case_status": str(state.get("case_status")),
        "verifier_error_count": len(errors),
        "flagged_claims": list(state.get("flagged_claims") or []),
        "allowed_actions": ["escalate", "dismiss", "request_more_data"],
    }


@traced_node("human_decision", HumanDecisionOutput)
async def human_decision(state: Mapping[str, Any], ctx: NodeContext) -> HumanDecisionOutput:
    raw = interrupt(case_packet(state))
    return HumanDecisionOutput(decision=validate_decision(raw, state.get("case_status")))
