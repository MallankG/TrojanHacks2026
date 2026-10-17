import asyncio
from collections.abc import Mapping
from typing import Any

from tracelayer_agents.schemas import BuildGraphOutput
from tracelayer_agents.wrappers import NodeContext, traced_node


@traced_node("build_graph", BuildGraphOutput)
async def build_graph(state: Mapping[str, Any], ctx: NodeContext) -> BuildGraphOutput:
    snapshot = await asyncio.to_thread(
        ctx.services.graph.build_snapshot, state["transaction_batch_id"]
    )
    return BuildGraphOutput(
        graph_snapshot_id=snapshot.graph_snapshot_id,
        feature_schema_version=snapshot.feature_schema_version,
    )
