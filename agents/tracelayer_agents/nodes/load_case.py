"""First case-workflow node: the alert and the evidence bundle `investigate_flows` saved for it,
plus the derived facts the narrative may quote."""

import asyncio
from collections.abc import Mapping
from typing import Any

from tracelayer_agents.nodes.derive import compute_derived_facts
from tracelayer_agents.schemas import LoadCaseOutput
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
    return LoadCaseOutput(
        alert=alert, evidence_bundle=bundle, derived_facts=compute_derived_facts(bundle)
    )
