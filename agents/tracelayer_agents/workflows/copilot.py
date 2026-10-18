"""copilot_workflow:

    START → retrieve_policy → copilot_answer → verify_citations → END

Answers a policy question from the curated corpus only. Every claim in the returned answer cites a
chunk that `retrieve_policy` actually retrieved; anything else is dropped by `verify_citations`.
"""

from __future__ import annotations

import uuid
from dataclasses import fields
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from tracelayer_agents.config import AgentConfig
from tracelayer_agents.interfaces import Services
from tracelayer_agents.nodes.copilot_answer import copilot_answer
from tracelayer_agents.nodes.retrieve_policy import retrieve_policy
from tracelayer_agents.nodes.verify import verify_citations
from tracelayer_agents.registry import get_services
from tracelayer_agents.schemas import CopilotResult
from tracelayer_agents.state import CopilotState
from tracelayer_agents.wrappers import NodeContext, NodeError

NODES = (retrieve_policy, copilot_answer, verify_citations)


def build_copilot_workflow(
    services: Services, config: AgentConfig | None = None
) -> CompiledStateGraph:
    """`services` must be fully resolved (see `registry.get_services`)."""
    ctx = NodeContext(services=services, config=config or AgentConfig.from_env())
    builder = StateGraph(CopilotState)
    for node in NODES:
        builder.add_node(node.name, node.bind(ctx))

    builder.add_edge(START, "retrieve_policy")
    builder.add_edge("retrieve_policy", "copilot_answer")
    builder.add_edge("copilot_answer", "verify_citations")
    builder.add_edge("verify_citations", END)
    # The checkpointer lets run_copilot read back the last committed state when a node fails.
    return builder.compile(checkpointer=MemorySaver())


async def run_copilot(
    question: str,
    alert_id: str | None = None,
    services: Services | None = None,
    config: AgentConfig | None = None,
) -> CopilotResult:
    """Answer one policy question. `alert_id` is carried in state for the trace; retrieval uses
    the question only.

    Raises `ValueError` for a blank question, and `NodeError` if a node fails (its `.state` holds
    the last committed state with the node's error `TraceEvent` appended).
    """
    if not question.strip():
        raise ValueError("question must not be empty")
    complete = services is not None and all(getattr(services, f.name) for f in fields(Services))
    resolved = services if complete else get_services(services)
    workflow = build_copilot_workflow(resolved, config)
    trace_id = f"tr_{uuid.uuid4().hex}"
    run_cfg: dict[str, Any] = {"configurable": {"thread_id": trace_id}}

    initial: CopilotState = {"trace_id": trace_id, "question": question, "alert_id": alert_id}
    try:
        state: CopilotState = await workflow.ainvoke(initial, run_cfg)
    except NodeError as err:
        snapshot = await workflow.aget_state(run_cfg)
        partial: dict[str, Any] = dict(snapshot.values)
        partial["trace_events"] = [*partial.get("trace_events", []), err.trace_event]
        err.state = partial
        raise

    return CopilotResult(
        trace_id=trace_id,
        answer=state["answer"],
        sources=state.get("retrieved_sources", []),
        verifier_errors=state.get("verifier_errors", []),
        trace=state.get("trace_events", []),
    )
