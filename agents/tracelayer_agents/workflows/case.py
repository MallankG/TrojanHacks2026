"""case_workflow (part 1: drafting):

    START → load_case → retrieve_policy → draft_narrative → verify_evidence ─┬ errors & attempts < 2
                                                ↑                             │   → draft_narrative
                                                └─────────────────────────────┘
                                                                              └ else → END

Ends with `case_status="drafted"` (no verifier errors) or `"needs_revision"` (errors remain; the
draft is kept and `flagged_claims` lists the claims to show as unverified).
"""

from __future__ import annotations

import uuid
from dataclasses import fields
from typing import Any, Literal

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from tracelayer_agents.config import AgentConfig
from tracelayer_agents.interfaces import Services
from tracelayer_agents.nodes.draft_narrative import draft_narrative
from tracelayer_agents.nodes.load_case import load_case
from tracelayer_agents.nodes.retrieve_policy import retrieve_policy
from tracelayer_agents.nodes.verify import verify_evidence_node
from tracelayer_agents.registry import get_services
from tracelayer_agents.schemas import CaseDraftResult
from tracelayer_agents.state import CaseState
from tracelayer_agents.wrappers import NodeContext, NodeError

NODES = (load_case, retrieve_policy, draft_narrative, verify_evidence_node)
MAX_DRAFT_ATTEMPTS = 2


def route_after_verify(state: CaseState) -> Literal["redraft", "done"]:
    if state.get("verifier_errors") and (state.get("draft_attempts") or 0) < MAX_DRAFT_ATTEMPTS:
        return "redraft"
    return "done"


def build_case_workflow(
    services: Services, config: AgentConfig | None = None
) -> CompiledStateGraph:
    """`services` must be fully resolved (see `registry.get_services`)."""
    ctx = NodeContext(services=services, config=config or AgentConfig.from_env())
    builder = StateGraph(CaseState)
    for node in NODES:
        builder.add_node(node.name, node.bind(ctx))

    builder.add_edge(START, "load_case")
    builder.add_edge("load_case", "retrieve_policy")
    builder.add_edge("retrieve_policy", "draft_narrative")
    builder.add_edge("draft_narrative", "verify_evidence")
    # TODO(next prompt): "done" routes to human_decision (interrupt) → record_decision → END
    # instead of END, resumed by resume_case with Command(resume=decision) on thread_id=case_id.
    builder.add_conditional_edges(
        "verify_evidence", route_after_verify, {"redraft": "draft_narrative", "done": END}
    )
    # thread_id = case_id, so the human_decision step can pause and resume this run.
    return builder.compile(checkpointer=MemorySaver())


async def start_case(
    alert_id: str,
    services: Services | None = None,
    config: AgentConfig | None = None,
) -> CaseDraftResult:
    """Draft and verify the narrative for one alert whose evidence bundle `investigate_flows`
    saved.

    Raises `NodeError` if a node fails (e.g. `load_case` for an unknown alert); its `.state` holds
    the last committed state with the node's error `TraceEvent` appended.
    """
    # TODO(next prompt): create the CaseRecord, save each draft with CaseRepository.save_draft and
    # write audit events for the draft.
    complete = services is not None and all(getattr(services, f.name) for f in fields(Services))
    resolved = services if complete else get_services(services)
    workflow = build_case_workflow(resolved, config)
    trace_id = f"tr_{uuid.uuid4().hex}"
    case_id = f"case_{uuid.uuid4().hex[:12]}"
    run_cfg: dict[str, Any] = {"configurable": {"thread_id": case_id}}

    initial: CaseState = {
        "trace_id": trace_id, "case_id": case_id, "alert_id": alert_id, "draft_attempts": 0,
    }
    try:
        state: CaseState = await workflow.ainvoke(initial, run_cfg)
    except NodeError as err:
        snapshot = await workflow.aget_state(run_cfg)
        partial: dict[str, Any] = dict(snapshot.values)
        partial["trace_events"] = [*partial.get("trace_events", []), err.trace_event]
        err.state = partial
        raise

    return CaseDraftResult(
        trace_id=trace_id,
        case_id=case_id,
        alert_id=alert_id,
        draft=state["draft_narrative"],
        verifier_errors=state.get("verifier_errors", []),
        flagged_claims=state.get("flagged_claims", []),
        llm_meta=state["llm_meta"],
        case_status=state["case_status"],
        trace=state.get("trace_events", []),
    )
