"""analysis_graph:

    START → ingest → governance → [gate]
              ├─ halt (strict + fail) ───────────────────────────→ END
              └─ continue → build_graph → ┬ detect_graphml  ┐
                                           └ detect_behavior ┴→ aggregate → investigate_flows → END

The two detectors run in the same superstep; `aggregate` waits for both, and the `merge_dicts`
reducer on `model_scores` combines their outputs.
"""

from __future__ import annotations

import uuid
from dataclasses import fields
from typing import Any, Literal

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from tracelayer_agents import audit
from tracelayer_agents.config import AgentConfig
from tracelayer_agents.interfaces import Services
from tracelayer_agents.nodes.aggregate import aggregate
from tracelayer_agents.nodes.build_graph import build_graph
from tracelayer_agents.nodes.detect_behavior import detect_behavior
from tracelayer_agents.nodes.detect_graphml import detect_graphml
from tracelayer_agents.nodes.governance import governance
from tracelayer_agents.nodes.ingest import ingest
from tracelayer_agents.nodes.investigate_flows import investigate_flows
from tracelayer_agents.registry import get_services
from tracelayer_agents.schemas import CaseStatus, Mode
from tracelayer_agents.state import AnalysisState
from tracelayer_agents.wrappers import NodeContext, NodeError

NODES = (
    ingest,
    governance,
    build_graph,
    detect_graphml,
    detect_behavior,
    aggregate,
    investigate_flows,
)


def gate(state: AnalysisState) -> Literal["halt", "continue"]:
    if state.get("mode") == "strict" and state.get("governance_status") == "fail":
        return "halt"
    return "continue"


def build_analysis_graph(
    services: Services, config: AgentConfig | None = None
) -> CompiledStateGraph:
    """`services` must be fully resolved (see `registry.get_services`)."""
    ctx = NodeContext(services=services, config=config or AgentConfig.from_env())
    builder = StateGraph(AnalysisState)
    for node in NODES:
        builder.add_node(node.name, node.bind(ctx))

    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "governance")
    builder.add_conditional_edges("governance", gate, {"halt": END, "continue": "build_graph"})
    builder.add_edge("build_graph", "detect_graphml")
    builder.add_edge("build_graph", "detect_behavior")
    builder.add_edge(["detect_graphml", "detect_behavior"], "aggregate")
    builder.add_edge("aggregate", "investigate_flows")
    builder.add_edge("investigate_flows", END)
    # The checkpointer lets run_analysis read back the last committed state when a node fails.
    return builder.compile(checkpointer=MemorySaver())


async def run_analysis(
    batch_ref: str,
    mode: Mode,
    services: Services | None = None,
    config: AgentConfig | None = None,
) -> AnalysisState:
    """Run the analysis graph on one batch and return the final state.

    `services`: a complete bundle is used as-is; None or a partial one is filled in by
    `registry.get_services`.

    Raises `NodeError` if a node fails; its `.state` holds the last committed state with the
    node's error `TraceEvent` appended (the facade turns that into `status="error"`).
    """
    # A complete bundle was already resolved by the registry (which applies TRACELAYER_USE_MOCKS);
    # resolving it again would swap in fresh mocks the caller can't see.
    complete = services is not None and all(getattr(services, f.name) for f in fields(Services))
    resolved = services if complete else get_services(services)
    graph = build_analysis_graph(resolved, config)
    trace_id = f"tr_{uuid.uuid4().hex}"
    run_cfg: dict[str, Any] = {"configurable": {"thread_id": trace_id}}

    def record(action: str, **metadata: Any):
        return audit.record(
            resolved.audit,
            action=action,
            object_type="analysis_run",
            object_id=trace_id,
            metadata=metadata,
        )

    started = record("analysis.run.started", batch_ref=batch_ref, mode=mode)
    initial: AnalysisState = {
        "trace_id": trace_id,
        "batch_ref": batch_ref,
        "mode": mode,
        "audit_events": [started],
    }
    try:
        state: AnalysisState = await graph.ainvoke(initial, run_cfg)
    except NodeError as err:
        snapshot = await graph.aget_state(run_cfg)
        partial: AnalysisState = dict(snapshot.values)  # type: ignore[assignment]
        failed = record("analysis.run.failed", node=err.node, error=err.trace_event.error)
        partial["trace_events"] = [*partial.get("trace_events", []), err.trace_event]
        partial["audit_events"] = [*partial.get("audit_events", []), failed]
        err.state = dict(partial)
        raise

    halted = state.get("case_status") == CaseStatus.HELD_FOR_DATA_REVIEW
    finished = record(
        "analysis.run.halted" if halted else "analysis.run.completed",
        governance_status=state.get("governance_status"),
        alert_count=len(state.get("alerts", [])),
        case_status=str(state["case_status"]) if halted else None,
    )
    state["audit_events"] = [*state.get("audit_events", []), finished]
    return state
