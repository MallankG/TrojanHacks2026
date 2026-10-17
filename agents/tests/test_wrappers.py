import asyncio
import time

import pytest

from tracelayer_agents.config import AgentConfig
from tracelayer_agents.interfaces import Services
from tracelayer_agents.schemas import IngestOutput
from tracelayer_agents.wrappers import NodeContext, NodeError, SkipNode, traced_node

CTX = NodeContext(services=Services(), config=AgentConfig(node_timeout_s=0.2))
STATE = {"trace_id": "tr_test", "batch_ref": "x"}
OUT = IngestOutput(ingestion_run_id="run_1", transaction_batch_id="batch_1")


async def test_sync_node_runs_and_traces() -> None:
    @traced_node("ingest", IngestOutput)
    def node(state, ctx):
        return OUT

    update = await node.bind(CTX)(STATE)
    assert update["ingestion_run_id"] == "run_1"
    (event,) = update["trace_events"]
    assert (event.node, event.status, event.trace_id) == ("ingest", "ok", "tr_test")
    assert event.output_hash and event.duration_ms >= 0 and event.ended_at >= event.started_at


async def test_async_node_and_dict_result_validate() -> None:
    @traced_node("ingest", IngestOutput)
    async def node(state, ctx):
        return OUT.model_dump()

    update = await node.bind(CTX)(STATE)
    assert update["transaction_batch_id"] == "batch_1"


async def test_input_hash_ignores_trace_lists() -> None:
    @traced_node("ingest", IngestOutput)
    def node(state, ctx):
        return OUT

    a = (await node.bind(CTX)(STATE))["trace_events"][0]
    b = (await node.bind(CTX)({**STATE, "trace_events": [a]}))["trace_events"][0]
    assert a.input_hash == b.input_hash


async def test_wrong_return_type_is_node_error() -> None:
    @traced_node("ingest", IngestOutput)
    def node(state, ctx):
        return ["not", "a", "model"]

    with pytest.raises(NodeError) as info:
        await node.bind(CTX)(STATE)
    assert info.value.node == "ingest"
    assert info.value.trace_event.status == "error"
    assert "TypeError" in info.value.trace_event.error


async def test_exception_becomes_error_event() -> None:
    @traced_node("ingest", IngestOutput)
    async def node(state, ctx):
        raise KeyError("batch_ref")

    with pytest.raises(NodeError) as info:
        await node.bind(CTX)(STATE)
    assert isinstance(info.value.__cause__, KeyError)
    assert info.value.trace_event.output_hash is None


@pytest.mark.parametrize("is_async", [True, False])
async def test_timeout_from_config(is_async: bool) -> None:
    if is_async:
        async def slow(state, ctx):
            await asyncio.sleep(5)
    else:
        def slow(state, ctx):
            time.sleep(0.5)  # abandoned worker thread; kept short so the test exits quickly

    node = traced_node("ingest", IngestOutput)(slow)
    with pytest.raises(NodeError) as info:
        await node.bind(CTX)(STATE)
    assert "TimeoutError" in info.value.trace_event.error


async def test_skip_node() -> None:
    @traced_node("investigate_flows", IngestOutput)
    def node(state, ctx):
        raise SkipNode("nothing to do")

    update = await node.bind(CTX)(STATE)
    assert list(update) == ["trace_events"]
    assert update["trace_events"][0].status == "skipped"
