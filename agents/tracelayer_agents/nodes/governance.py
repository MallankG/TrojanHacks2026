import asyncio
from collections.abc import Mapping
from typing import Any

from tracelayer_agents.schemas import (
    CaseStatus,
    GovernanceOutput,
    GovernanceStatus,
    QualityIssue,
)
from tracelayer_agents.wrappers import NodeContext, traced_node

_ORDER: dict[GovernanceStatus, int] = {"pass": 0, "qualified": 1, "fail": 2}


def status_from_issues(issues: list[QualityIssue]) -> GovernanceStatus:
    """pass if no issues, qualified if only warning/info, fail if any critical."""
    if any(i.severity == "critical" for i in issues):
        return "fail"
    return "qualified" if issues else "pass"


@traced_node("governance", GovernanceOutput)
async def governance(state: Mapping[str, Any], ctx: NodeContext) -> GovernanceOutput:
    report = await asyncio.to_thread(
        ctx.services.quality.run_contract, state["transaction_batch_id"]
    )
    derived = status_from_issues(report.issues)
    # TODO(review): if the service's verdict and the issue-derived one disagree, keep the stricter.
    status = max(derived, report.status, key=_ORDER.__getitem__)
    halt = state.get("mode", "strict") == "strict" and status == "fail"
    return GovernanceOutput(
        governance_status=status,
        quality_issues=report.issues,
        case_status=CaseStatus.HELD_FOR_DATA_REVIEW if halt else None,
    )
