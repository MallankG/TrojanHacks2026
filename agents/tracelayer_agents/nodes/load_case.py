"""First case-workflow node: the alert and the evidence bundle `investigate_flows` saved for it,
plus the derived facts the narrative may quote. Opens the `CaseRecord` (status `open`) if this
case_id has none yet."""

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from tracelayer_agents.nodes.derive import compute_derived_facts
from tracelayer_agents.schemas import CaseRecord, CaseStatus, LoadCaseOutput
from tracelayer_agents.wrappers import NodeContext, traced_node


@traced_node("load_case", LoadCaseOutput)
async def load_case(state: Mapping[str, Any], ctx: NodeContext) -> LoadCaseOutput:
    alert_id = state["alert_id"]
    alert = await asyncio.to_thread(ctx.services.cases.get_alert, alert_id)
    if alert is None:
        raise KeyError(f"unknown alert_id {alert_id!r}")
    bundle = await asyncio.to_thread(ctx.services.cases.get_evidence, alert_id)
    if bundle is None:
        raise KeyError(f"no evidence bundle saved for alert {alert_id!r}")

    def open_case() -> None:
        case_id = state["case_id"]
        if ctx.services.cases.get_case(case_id) is None:
            now = datetime.now(UTC)
            ctx.services.cases.create_case(CaseRecord(
                case_id=case_id, alert_id=alert_id, title=f"Case for alert {alert_id}",
                status=CaseStatus.OPEN, created_at=now, updated_at=now,
            ))

    await asyncio.to_thread(open_case)
    return LoadCaseOutput(
        alert=alert, evidence_bundle=bundle, derived_facts=compute_derived_facts(bundle)
    )
