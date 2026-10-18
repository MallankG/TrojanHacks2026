"""chat_workflow (one turn of a case-scoped investigation chat):

    START → chat_classify ─┬─ plan ──→ chat_tool_query → chat_draft_answer → chat_verify ─┐
                           └─ needs clarification ───────────────────────────────────────┴→
                              chat_respond → END

Bound to one case through the `CaseScope` the session holds. Runs without a checkpointer (the
scope is a plain object and a turn is never resumed); the session keeps the turn.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from tracelayer_agents.audit import sha256_json
from tracelayer_agents.chat.session import ChatEntry, build_context
from tracelayer_agents.config import AgentConfig
from tracelayer_agents.interfaces import Services
from tracelayer_agents.nodes.chat import (
    SUGGESTED_CHIPS,
    chat_classify,
    chat_draft_answer,
    chat_respond,
    chat_tool_query,
    chat_verify,
)
from tracelayer_agents.schemas import ChatTurn, ChatTurnResult, ToolCallRecord
from tracelayer_agents.state import ChatState
from tracelayer_agents.wrappers import NodeContext

NODES = (chat_classify, chat_tool_query, chat_draft_answer, chat_verify, chat_respond)


def route_after_classify(state: ChatState) -> Literal["plan", "clarify"]:
    return "clarify" if state.get("clarification") else "plan"


def build_chat_workflow(
    services: Services, config: AgentConfig | None = None
) -> CompiledStateGraph:
    """`services` must be fully resolved (see `registry.get_services`)."""
    ctx = NodeContext(services=services, config=config or AgentConfig.from_env())
    builder = StateGraph(ChatState)
    for node in NODES:
        builder.add_node(node.name, node.bind(ctx))
    builder.add_edge(START, "chat_classify")
    builder.add_conditional_edges(
        "chat_classify", route_after_classify,
        {"plan": "chat_tool_query", "clarify": "chat_respond"},
    )
    builder.add_edge("chat_tool_query", "chat_draft_answer")
    builder.add_edge("chat_draft_answer", "chat_verify")
    builder.add_edge("chat_verify", "chat_respond")
    builder.add_edge("chat_respond", END)
    return builder.compile()


async def run_chat_turn(
    entry: ChatEntry,
    services: Services,
    config: AgentConfig | None = None,
    *,
    chip: str | None = None,
    question: str | None = None,
) -> ChatTurnResult:
    """Answer one chip or question on `entry`'s case and append the turn to its session.
    Raises `NodeError` if a node fails (tool and LLM failures don't: they become part of the
    answer)."""
    session = entry.session
    trace_id = f"tr_{uuid.uuid4().hex}"
    turn_id = f"turn_{uuid.uuid4().hex[:12]}"
    initial: ChatState = {
        "trace_id": trace_id, "chat_id": session.chat_id, "turn_id": turn_id,
        "case_id": session.case_id, "actor_id": session.actor_id,
        "actor_role": session.actor_role, "scope": entry.scope,
        "pinned": list(session.context.pinned_evidence_ids), "chip": chip, "question": question,
    }
    state: dict[str, Any] = await build_chat_workflow(services, config).ainvoke(initial)

    clarified = bool(state.get("clarification"))
    results = state.get("tool_results") or []
    turn = ChatTurn(
        turn_id=turn_id, trace_id=trace_id, created_at=datetime.now(UTC), chip=chip,
        question=question, question_hash=sha256_json(question or f"chip:{chip}"),
        status="needs_clarification" if clarified else "ok",
        intent=state.get("intent") or "unclear",
        answer=state["clarification"] if clarified else state["answer"],
        claims=state.get("claims") or [],
        gaps=state.get("gaps") or [],
        failures=[f"{r.tool}: {r.status} ({r.error})" for r in results if r.status != "ok"],
        next_steps=state.get("next_steps") or [],
        suggested_chips=SUGGESTED_CHIPS if clarified else [],
        tool_calls=[ToolCallRecord(tool=r.tool, args=r.args, status=r.status,
                                   row_count=r.row_count, truncated=r.truncated,
                                   duration_ms=r.duration_ms, error=r.error) for r in results],
        verification_status=("unverified" if clarified
                             else state.get("verification_status") or "unverified"),
        llm_meta=state.get("llm_meta") or [],
    )
    with entry.lock:
        session.turns.append(turn)
    return ChatTurnResult(
        status=turn.status, chat_id=session.chat_id, turn=turn,
        context=build_context(entry.scope, session.context.pinned_evidence_ids),
        trace=state.get("trace_events") or [],
    )
