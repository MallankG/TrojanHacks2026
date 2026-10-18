"""chat_workflow (one turn of a case-scoped investigation chat):

    START → chat_guard → chat_classify ─┬─ plan ──→ chat_tool_query → chat_draft_answer
                                        │            → chat_verify ─┐
                                        └─ denied / needs clarification ┴→ chat_respond → END

`chat_guard` denies a question naming another case's records (classify then skips) and notes how
an unbounded one is narrowed.

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
from tracelayer_agents.chat import guard
from tracelayer_agents.chat.session import ChatEntry, as_of
from tracelayer_agents.config import AgentConfig
from tracelayer_agents.interfaces import Services
from tracelayer_agents.nodes.chat import (
    SUGGESTED_CHIPS,
    chat_classify,
    chat_draft_answer,
    chat_guard,
    chat_respond,
    chat_tool_query,
    chat_verify,
)
from tracelayer_agents.schemas import (
    AnswerMetadata,
    ChatClaim,
    ChatGap,
    ChatTurn,
    ChatTurnResult,
    Coverage,
    SourceRef,
    ToolCallRecord,
    ToolResult,
)
from tracelayer_agents.state import ChatState
from tracelayer_agents.wrappers import NodeContext

NODES = (chat_guard, chat_classify, chat_tool_query, chat_draft_answer, chat_verify,
         chat_respond)


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
    builder.add_edge(START, "chat_guard")
    builder.add_edge("chat_guard", "chat_classify")
    builder.add_conditional_edges(
        "chat_classify", route_after_classify,
        {"plan": "chat_tool_query", "clarify": "chat_respond"},
    )
    builder.add_edge("chat_tool_query", "chat_draft_answer")
    builder.add_edge("chat_draft_answer", "chat_verify")
    builder.add_edge("chat_verify", "chat_respond")
    builder.add_edge("chat_respond", END)
    return builder.compile()


_COMPILED: dict[tuple[int, int], tuple[Services, AgentConfig | None, CompiledStateGraph]] = {}


def _compiled(services: Services, config: AgentConfig | None) -> CompiledStateGraph:
    """Compile once per (services, config); the entry keeps both alive so ids are not reused."""
    key = (id(services), id(config))
    if key not in _COMPILED:
        _COMPILED[key] = (services, config, build_chat_workflow(services, config))
    return _COMPILED[key][2]


def coverage(claims: list[ChatClaim], gaps: list[ChatGap],
             results: list[ToolResult]) -> Coverage:
    """Counts, not a confidence score (see `schemas.Coverage`)."""
    partial_tools = {r.tool for r in results if r.truncated or r.warnings}
    verified = partial = unsupported = 0
    for c in claims:
        if c.label == "missing_evidence":
            unsupported += 1
        elif c.label == "hypothesis" or partial_tools & set(c.source_tools):
            partial += 1
        else:
            verified += 1
    return Coverage(verified=verified, partial=partial, unsupported=unsupported + len(gaps))


def answer_metadata(entry: ChatEntry, claims: list[ChatClaim], gaps: list[ChatGap],
                    results: list[ToolResult], records: list[ToolCallRecord],
                    context_version: int) -> AnswerMetadata:
    sources = {}
    for r in results:
        if r.status == "ok" and r.tool == "policy_retrieval":
            for row in r.rows:
                sources.setdefault(row["chunk_id"], SourceRef(
                    chunk_id=row["chunk_id"], doc_title=row.get("doc_title") or "",
                    section=row.get("section"), source_uri=row.get("source_uri")))
    return AnswerMetadata(
        as_of=as_of(entry.scope), graph_snapshot_id=entry.scope.graph_snapshot_id,
        context_version=context_version,  # the version the turn ran against, not the live one
        retrieved_sources=list(sources.values()), tool_actions=records,
        coverage=coverage(claims, gaps, results),
    )


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
    context = session.context
    trace_id = f"tr_{uuid.uuid4().hex}"
    turn_id = f"turn_{uuid.uuid4().hex[:12]}"
    initial: ChatState = {
        "trace_id": trace_id, "chat_id": session.chat_id, "turn_id": turn_id,
        "case_id": session.case_id, "actor_id": session.actor_id,
        "actor_role": session.actor_role, "tenant_id": session.tenant_id, "scope": entry.scope,
        "pinned": list(context.pinned_evidence_ids), "selected": list(context.selected_ids),
        "context_version": context.context_version, "chip": chip, "question": question,
    }
    state: dict[str, Any] = await _compiled(services, config).ainvoke(initial)

    denied = bool(state.get("denied"))
    clarified = bool(state.get("clarification"))
    results = [] if denied else state.get("tool_results") or []
    claims = [] if denied else state.get("claims") or []
    gaps = [] if denied else state.get("gaps") or []
    records = [ToolCallRecord(tool=r.tool, args=r.args, status=r.status, row_count=r.row_count,
                              truncated=r.truncated, duration_ms=r.duration_ms, error=r.error)
               for r in results]
    status = "denied" if denied else "needs_clarification" if clarified else "ok"
    turn = ChatTurn(
        turn_id=turn_id, trace_id=trace_id, created_at=datetime.now(UTC), chip=chip,
        # A denied question may name another case's records: keep only its hash.
        question=None if denied else question,
        question_hash=sha256_json(question or f"chip:{chip}"),
        status=status,
        intent=state.get("intent") or "unclear",
        answer=state["clarification"] if clarified else state["answer"],
        claims=claims,
        gaps=gaps,
        failures=[f"{r.tool}: {r.status} ({r.error})" for r in results if r.status != "ok"],
        next_steps=[] if clarified else state.get("next_steps") or [],
        suggested_chips=SUGGESTED_CHIPS if clarified else [],
        tool_calls=records,
        verification_status=("unverified" if clarified
                             else state.get("verification_status") or "unverified"),
        llm_meta=state.get("llm_meta") or [],
        precomputed=bool(state.get("precomputed")),
        bounds_applied=[] if denied else guard.bounds_text(state.get("narrowed") or []),
        metadata=answer_metadata(entry, claims, gaps, results, records, context.context_version),
    )
    with entry.lock:
        session.turns.append(turn)
    return ChatTurnResult(
        status=turn.status, chat_id=session.chat_id, turn=turn,
        context=session.context.model_copy(deep=True),
        trace=state.get("trace_events") or [],
    )
