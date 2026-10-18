import asyncio
from collections.abc import Mapping
from typing import Any

from tracelayer_agents.schemas import DetectorScoresOutput
from tracelayer_agents.wrappers import NodeContext, traced_node


@traced_node("detect_behavior", DetectorScoresOutput)
async def detect_behavior(state: Mapping[str, Any], ctx: NodeContext) -> DetectorScoresOutput:
    outputs = await asyncio.to_thread(ctx.services.behavior.score, state["graph_snapshot_id"])
    names = [o.detector_name for o in outputs]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate detector_name in BehaviorScorer output: {names}")
    return DetectorScoresOutput(model_scores={o.detector_name: o for o in outputs})
