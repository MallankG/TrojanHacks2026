import asyncio
import time

import pytest
from pydantic import ValidationError

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


async def test_llm_node_uses_llm_node_timeout() -> None:
    ctx = NodeContext(services=Services(),
                      config=AgentConfig(node_timeout_s=0.05, llm_node_timeout_s=200.0))

    async def slowish(state, ctx):
        await asyncio.sleep(0.2)  # longer than node_timeout_s, well within llm_node_timeout_s
        return OUT

    assert (await traced_node("copilot_answer", IngestOutput, llm=True)(slowish).bind(ctx)(STATE))
    with pytest.raises(NodeError) as info:
        await traced_node("ingest", IngestOutput)(slowish).bind(ctx)(STATE)
    assert "exceeded 0.05s" in info.value.trace_event.error


def test_llm_node_timeout_must_exceed_llm_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_TIMEOUT_S", raising=False)
    monkeypatch.delenv("TRACELAYER_LLM_NODE_TIMEOUT_S", raising=False)
    assert AgentConfig().llm_node_timeout_s == 150.0  # default LLM_TIMEOUT_S (120) + 30
    assert AgentConfig().node_timeout_s == 30.0
    for bad in (120.0, 60.0):
        with pytest.raises(ValidationError, match="LLM_TIMEOUT_S"):
            AgentConfig(llm_node_timeout_s=bad)
    assert AgentConfig(llm_node_timeout_s=120.5).llm_node_timeout_s == 120.5

    monkeypatch.setenv("LLM_TIMEOUT_S", "200")
    assert AgentConfig().llm_node_timeout_s == 230.0
    monkeypatch.setenv("TRACELAYER_LLM_NODE_TIMEOUT_S", "150")
    with pytest.raises(ValidationError):
        AgentConfig.from_env()  # 150 <= LLM_TIMEOUT_S=200
    monkeypatch.setenv("TRACELAYER_LLM_NODE_TIMEOUT_S", "240")
    assert AgentConfig.from_env().llm_node_timeout_s == 240.0


async def test_skip_node() -> None:
    @traced_node("investigate_flows", IngestOutput)
    def node(state, ctx):
        raise SkipNode("nothing to do")

    update = await node.bind(CTX)(STATE)
    assert list(update) == ["trace_events"]
    assert update["trace_events"][0].status == "skipped"
