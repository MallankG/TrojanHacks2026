import asyncio
from collections.abc import Mapping
from typing import Any

from tracelayer_agents.schemas import IngestOutput
from tracelayer_agents.wrappers import NodeContext, traced_node


@traced_node("ingest", IngestOutput)
async def ingest(state: Mapping[str, Any], ctx: NodeContext) -> IngestOutput:
    result = await asyncio.to_thread(
        ctx.services.ingestion.ingest, state["batch_ref"], ctx.config.source_asset_id
    )
    return IngestOutput(
        ingestion_run_id=result.ingestion_run_id,
        transaction_batch_id=result.transaction_batch_id,
    )
