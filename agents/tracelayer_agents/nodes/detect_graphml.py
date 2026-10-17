import asyncio
from collections.abc import Mapping
from typing import Any

from tracelayer_agents.schemas import DetectorScoresOutput
from tracelayer_agents.wrappers import NodeContext, traced_node


@traced_node("detect_graphml", DetectorScoresOutput)
async def detect_graphml(state: Mapping[str, Any], ctx: NodeContext) -> DetectorScoresOutput:
    out = await asyncio.to_thread(ctx.services.graph_risk.score, state["graph_snapshot_id"])
    return DetectorScoresOutput(model_scores={out.detector_name: out})
