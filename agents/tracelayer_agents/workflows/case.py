"""case_workflow:

    START → load_case → retrieve_policy → draft_narrative → verify_evidence → save_draft
                                                ↑                                  │
                                                └──── errors & attempts < 2 ───────┤
                                                                                   └ else
                    END ← record_decision ← human_decision (interrupt) ←───────────┘

`start_case` runs until `human_decision` pauses, with `case_status="awaiting_review"` (no verifier
errors) or `"needs_revision"` (errors remain; the draft is kept and `flagged_claims` lists the
claims to show as unverified). `resume_case` resumes the paused run (thread_id = case_id) with the
reviewer's `Decision`; `record_decision` sets the final status and writes the `case.decision`
audit event. Nothing is filed, frozen or sent to a customer.
"""

from __future__ import annotations

import uuid
from dataclasses import fields
from typing import Any, Literal

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from tracelayer_agents import schemas
from tracelayer_agents.config import AgentConfig
from tracelayer_agents.interfaces import Services
from tracelayer_agents.nodes.draft_narrative import draft_narrative
from tracelayer_agents.nodes.human_decision import (
    DecisionRejected,
    human_decision,
    validate_decision,
)
from tracelayer_agents.nodes.load_case import load_case
from tracelayer_agents.nodes.record_decision import record_decision
from tracelayer_agents.nodes.retrieve_policy import retrieve_policy
from tracelayer_agents.nodes.save_draft import save_draft
from tracelayer_agents.nodes.verify import verify_evidence_node
from tracelayer_agents.registry import get_services
from tracelayer_agents.schemas import CaseDecisionResult, CaseDraftResult, Decision
from tracelayer_agents.state import CaseState
from tracelayer_agents.wrappers import NodeContext, NodeError

NODES = (
    load_case, retrieve_policy, draft_narrative, verify_evidence_node, save_draft,
    human_decision, record_decision,
)
MAX_DRAFT_ATTEMPTS = 2

# One checkpointer for every compiled case workflow, so `resume_case` finds the run `start_case`
# paused. In-memory: paused cases don't survive a restart (TEAM_QUESTIONS backend Q7). The
# checkpoint may only deserialize our own schema types (plus LangGraph's built-in safe types).
_SCHEMA_TYPES = [
    obj for obj in vars(schemas).values()
    if isinstance(obj, type) and obj.__module__ == schemas.__name__
]
CHECKPOINTER = MemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=_SCHEMA_TYPES))


def route_after_save(state: CaseState) -> Literal["redraft", "done"]:
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
    builder.add_edge("verify_evidence", "save_draft")
    builder.add_conditional_edges(
        "save_draft", route_after_save, {"redraft": "draft_narrative", "done": "human_decision"}
    )
    builder.add_edge("human_decision", "record_decision")
    builder.add_edge("record_decision", END)
    # thread_id = case_id, so the human_decision step can pause and resume this run.
    return builder.compile(checkpointer=CHECKPOINTER)


def _resolve(services: Services | None) -> Services:
    # A complete bundle was already resolved by the registry; resolving it again would swap in
    # fresh mocks the caller can't see.
    complete = services is not None and all(getattr(services, f.name) for f in fields(Services))
    return services if complete else get_services(services)


async def start_case(
    alert_id: str,
    services: Services | None = None,
    config: AgentConfig | None = None,
) -> CaseDraftResult:
    """Open a case for one alert whose evidence bundle `investigate_flows` saved, draft and verify
    its narrative, and pause at `human_decision` for the reviewer.

    Raises `NodeError` if a node fails (e.g. `load_case` for an unknown alert); its `.state` holds
    the last committed state with the node's error `TraceEvent` appended.
    """
    workflow = build_case_workflow(_resolve(services), config)
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
        precomputed=bool(state.get("precomputed")),
    )


async def resume_case(
    case_id: str,
    decision: Decision | dict[str, Any],
    services: Services | None = None,
    config: AgentConfig | None = None,
) -> CaseDecisionResult:
    """Resume the case `start_case` paused, with the reviewer's decision. `services` must be the
    ones `start_case` used (the case and audit repositories hold the case).

    Raises `KeyError` for an unknown case_id, `ValueError` for a case that was already decided,
    `DecisionRejected` for an invalid or unauthorized decision (the case stays paused, so a valid
    decision can be submitted), and `NodeError` if `record_decision` fails (the decision is then
    in the checkpoint; that case needs operator attention).
    """
    workflow = build_case_workflow(_resolve(services), config)
    run_cfg: dict[str, Any] = {"configurable": {"thread_id": case_id}}
    snapshot = await workflow.aget_state(run_cfg)
    if not snapshot.values:
        raise KeyError(f"unknown case_id {case_id!r}")
    if "human_decision" not in snapshot.next:
        status = snapshot.values.get("case_status")
        raise ValueError(f"case {case_id!r} is not awaiting a decision (case_status={status})")

    # Check before resuming: once a resume value reaches the checkpoint, LangGraph replays it on
    # every later resume, so a rejected decision must never get there. `human_decision` runs the
    # same check again on the value it receives.
    checked = validate_decision(decision, snapshot.values.get("case_status"))
    raw = checked.model_dump(mode="json")
    try:
        state: CaseState = await workflow.ainvoke(Command(resume=raw), run_cfg)
    except NodeError as err:
        if isinstance(err.__cause__, DecisionRejected):
            raise err.__cause__ from None
        snapshot = await workflow.aget_state(run_cfg)
        partial: dict[str, Any] = dict(snapshot.values)
        partial["trace_events"] = [*partial.get("trace_events", []), err.trace_event]
        err.state = partial
        raise

    event = next(e for e in reversed(state["audit_events"]) if e.action == "case.decision")
    return CaseDecisionResult(
        case_id=case_id,
        case_status=state["case_status"],
        audit_event=event,
        trace=state.get("trace_events", []),
    )
