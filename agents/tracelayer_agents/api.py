"""The public facade: the only module the backend imports.

    from tracelayer_agents import api
    api.configure(Services(cases=..., audit=...))     # once, at FastAPI startup
    result = await api.run_analysis("batch_ref")      # -> AnalysisResult

`configure` resolves the services once (missing ones fall back to mocks) and keeps them
module-level, so `start_case` and `resume_case` always see the same repositories and the case
checkpointer (`workflows.case.CHECKPOINTER`). Every call configures lazily if the backend didn't.

No function here raises: failures come back as the result model with `status="error"` (or
`"rejected"` for a refused decision), a short `error` message and the trace so far. Messages never
carry stack traces or prompt text; unexpected exceptions are logged server-side. Every result is a
Pydantic model that works as a FastAPI `response_model` (money as decimal strings).
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any, get_args

from tracelayer_agents import audit
from tracelayer_agents.chat.session import MAX_PINS, ChatStore, build_context, build_scope
from tracelayer_agents.config import AgentConfig
from tracelayer_agents.interfaces import Services
from tracelayer_agents.nodes.human_decision import DecisionRejected
from tracelayer_agents.registry import get_services
from tracelayer_agents.schemas import (
    CHAT_CHIPS,
    ActorRole,
    AnalysisResult,
    AuditVerification,
    CaseDecisionResult,
    CaseDraftResult,
    CaseStatus,
    ChatFeedback,
    ChatResult,
    ChatTurnResult,
    CopilotResult,
    CytoscapeEdge,
    CytoscapeEdgeData,
    CytoscapeGraph,
    CytoscapeNode,
    CytoscapeNodeData,
    Decision,
    FeedbackResult,
    Mode,
    PinResult,
    TraceEvent,
)
from tracelayer_agents.workflows import analysis as analysis_workflow
from tracelayer_agents.workflows import case as case_workflow
from tracelayer_agents.workflows import chat as chat_workflow
from tracelayer_agents.workflows import copilot as copilot_workflow
from tracelayer_agents.wrappers import NodeError

logger = logging.getLogger(__name__)

# How many runs' traces `get_trace` keeps in memory (oldest evicted first).
TRACE_HISTORY = 50
MAX_ERROR_CHARS = 200
MAX_GRAPH_HOPS = 3
MAX_CHAT_MESSAGE_CHARS = 2000
MAX_FEEDBACK_NOTE_CHARS = 2000

_services: Services | None = None
_config: AgentConfig | None = None
_traces: OrderedDict[str, list[TraceEvent]] = OrderedDict()
_chats = ChatStore()


def configure(services: Services | None = None, config: AgentConfig | None = None) -> None:
    """Resolve the services once (each missing one falls back to its mock, with one warning) and
    keep them with the config for every later call. Safe to call again: it replaces both."""
    global _services, _config, _chats
    _services = get_services(services)
    _config = config or AgentConfig.from_env()
    _chats = ChatStore()  # chats are bound to cases in these repositories


def _ensure() -> tuple[Services, AgentConfig]:
    if _services is None or _config is None:
        configure()
    assert _services is not None and _config is not None
    return _services, _config


# --------------------------------------------------------------------------- helpers


def _short(message: str) -> str:
    line = (message or "").strip().splitlines()[0] if (message or "").strip() else "failed"
    return line if len(line) <= MAX_ERROR_CHARS else line[: MAX_ERROR_CHARS - 1] + "…"


def _node_error(err: NodeError) -> str:
    return _short(f"{err.node} failed: {err.trace_event.error or 'error'}")


def _internal(exc: Exception, what: str) -> str:
    logger.exception("%s failed", what)
    return f"internal error ({type(exc).__name__})"


def _failed_trace(err: NodeError) -> list[TraceEvent]:
    return list((err.state or {}).get("trace_events") or [err.trace_event])


def _remember(trace: list[TraceEvent]) -> None:
    if not trace:
        return
    trace_id = trace[0].trace_id
    _traces[trace_id] = list(trace)
    _traces.move_to_end(trace_id)
    while len(_traces) > TRACE_HISTORY:
        _traces.popitem(last=False)


# --------------------------------------------------------------------------- workflows


async def run_analysis(batch_ref: str, mode: Mode = "strict") -> AnalysisResult:
    """Ingest, gate, score and investigate one batch."""
    services, config = _ensure()
    try:
        state = await analysis_workflow.run_analysis(batch_ref, mode, services, config)
    except NodeError as err:
        partial = err.state or {}
        trace = _failed_trace(err)
        _remember(trace)
        return AnalysisResult(
            status="error", error=_node_error(err), trace_id=partial.get("trace_id"),
            governance_status=partial.get("governance_status"), trace=trace,
        )
    except Exception as exc:
        return AnalysisResult(status="error", error=_internal(exc, "run_analysis"))

    trace = state.get("trace_events", [])
    _remember(trace)
    halted = state.get("case_status") == CaseStatus.HELD_FOR_DATA_REVIEW
    return AnalysisResult(
        status="halted" if halted else "ok",
        trace_id=state["trace_id"],
        governance_status=state.get("governance_status"),
        halted=halted,
        alerts=state.get("alerts", []),
        trace=trace,
    )


async def start_case(alert_id: str) -> CaseDraftResult:
    """Open a case for an alert, draft and verify its narrative, and pause for the reviewer."""
    services, config = _ensure()
    try:
        if services.cases.get_alert(alert_id) is None:
            return CaseDraftResult(
                status="error", error=_short(f"unknown alert_id {alert_id!r}"), alert_id=alert_id
            )
        result = await case_workflow.start_case(alert_id, services, config)
    except NodeError as err:
        partial = err.state or {}
        trace = _failed_trace(err)
        _remember(trace)
        return CaseDraftResult(
            status="error", error=_node_error(err), alert_id=alert_id,
            trace_id=partial.get("trace_id"), case_id=partial.get("case_id"), trace=trace,
        )
    except Exception as exc:
        return CaseDraftResult(
            status="error", error=_internal(exc, "start_case"), alert_id=alert_id
        )
    _remember(result.trace)
    return result


async def resume_case(case_id: str, decision: Decision | dict[str, Any]) -> CaseDecisionResult:
    """Record the reviewer's decision on a paused case. `decision.actor_role` must come from the
    backend's auth, never from the request body; role rules are enforced here."""
    services, config = _ensure()
    try:
        result = await case_workflow.resume_case(case_id, decision, services, config)
    except DecisionRejected as err:
        return CaseDecisionResult(status="rejected", error=_short(str(err)), case_id=case_id)
    except KeyError:
        return CaseDecisionResult(
            status="error", error=_short(f"unknown case_id {case_id!r}"), case_id=case_id
        )
    except ValueError as err:  # already decided
        return CaseDecisionResult(status="error", error=_short(str(err)), case_id=case_id)
    except NodeError as err:
        trace = _failed_trace(err)
        _remember(trace)
        return CaseDecisionResult(
            status="error", error=_node_error(err), case_id=case_id, trace=trace
        )
    except Exception as exc:
        return CaseDecisionResult(
            status="error", error=_internal(exc, "resume_case"), case_id=case_id
        )
    _remember(result.trace)
    return result


async def copilot_query(question: str, alert_id: str | None = None) -> CopilotResult:
    """Answer a policy question from the curated corpus, with verified citations."""
    services, config = _ensure()
    if not (question or "").strip():
        return CopilotResult(status="error", error="question must not be empty")
    try:
        result = await copilot_workflow.run_copilot(question, alert_id, services, config)
    except NodeError as err:
        partial = err.state or {}
        trace = _failed_trace(err)
        _remember(trace)
        return CopilotResult(
            status="error", error=_node_error(err), trace_id=partial.get("trace_id"),
            trace=trace,
        )
    except Exception as exc:
        return CopilotResult(status="error", error=_internal(exc, "copilot_query"))
    _remember(result.trace)
    return result


# --------------------------------------------------------------------------- reads


async def get_alert_graph(alert_id: str, hops: int = 1) -> CytoscapeGraph:
    """The transaction neighbourhood around an alert's evidence, as Cytoscape.js elements.
    Evidence edges have `highlighted=true`."""
    services, _ = _ensure()
    if not 1 <= hops <= MAX_GRAPH_HOPS:
        return CytoscapeGraph(status="error", error=f"hops must be 1 to {MAX_GRAPH_HOPS}")
    try:
        alert = services.cases.get_alert(alert_id)
        if alert is None:
            return CytoscapeGraph(status="error", error=_short(f"unknown alert_id {alert_id!r}"))
        bundle = services.cases.get_evidence(alert_id)
        evidence = (
            [t.transaction_id for t in bundle.transactions] if bundle is not None
            else list(alert.tx_ids)
        )
        sub = services.graph.subgraph(evidence, hops)
    except Exception as exc:
        return CytoscapeGraph(status="error", error=_internal(exc, "get_alert_graph"))

    evidence_ids = set(evidence)
    on_evidence = {
        acc for e in sub.edges if e.transaction_id in evidence_ids
        for acc in (e.origin_account_id, e.destination_account_id)
    }
    nodes = [
        CytoscapeNode(data=CytoscapeNodeData(
            id=n.account_id, label=n.account_id,
            risk=alert.composite_score if n.account_id in on_evidence else None,
            quality=alert.quality_status,
        ))
        for n in sub.nodes
    ]
    edges = [
        CytoscapeEdge(data=CytoscapeEdgeData(
            id=e.transaction_id, source=e.origin_account_id, target=e.destination_account_id,
            amount=str(e.amount), timestamp=e.timestamp, tx_id=e.transaction_id,
            highlighted=e.transaction_id in evidence_ids,
        ))
        for e in sub.edges
    ]
    return CytoscapeGraph(nodes=nodes, edges=edges)


def get_trace(trace_id: str) -> list[TraceEvent]:
    """Node trace of one of the last `TRACE_HISTORY` runs; `[]` if unknown or evicted."""
    return list(_traces.get(trace_id, []))


def verify_audit_chain() -> AuditVerification:
    """Re-verify the whole audit hash chain."""
    services, _ = _ensure()
    try:
        return audit.verify_chain(services.audit.list_events())
    except Exception as exc:
        return AuditVerification(
            ok=False, checked=0, reason=_internal(exc, "verify_audit_chain")
        )


# --------------------------------------------------------------------------- investigation chat


async def start_chat(case_id: str, actor_id: str, actor_role: ActorRole | str) -> ChatResult:
    """Open an investigation chat bound to one case (opened by `start_case`). `actor_id` and
    `actor_role` must come from the backend's auth. A different case needs a new chat."""
    services, _ = _ensure()
    if actor_role not in get_args(ActorRole):
        return ChatResult(status="error", error=f"actor_role must be one of {get_args(ActorRole)}")
    if not (actor_id or "").strip():
        return ChatResult(status="error", error="actor_id must not be empty")
    try:
        scope = await asyncio.to_thread(build_scope, case_id, services)
    except KeyError as err:
        return ChatResult(status="error", error=_short(str(err.args[0] if err.args else err)))
    except Exception as exc:
        return ChatResult(status="error", error=_internal(exc, "start_chat"))
    try:
        entry = _chats.create(scope, actor_id, actor_role)  # type: ignore[arg-type]
        audit.record(
            services.audit, action="chat.started", object_type="case", object_id=case_id,
            actor_id=actor_id, actor_role=actor_role,
            metadata={"chat_id": entry.session.chat_id, "alert_id": scope.alert.alert_id,
                      "bundle_hash": scope.bundle.bundle_hash,
                      "graph_snapshot_id": scope.graph_snapshot_id,
                      "scope_accounts": len(scope.account_ids),
                      "scope_transactions": len(scope.edges)},
        )
    except Exception as exc:
        return ChatResult(status="error", error=_internal(exc, "start_chat"))
    return ChatResult(chat=entry.session.model_copy(deep=True))


async def send_message(
    chat_id: str, text: str | None = None, chip: str | None = None
) -> ChatTurnResult:
    """Ask one question (`text`) or press one prompt chip (`chip`) in a chat; exactly one."""
    services, config = _ensure()
    if (text is None or not text.strip()) == (chip is None):
        return ChatTurnResult(status="error", chat_id=chat_id,
                              error="send exactly one of text or chip")
    if chip is not None and chip not in CHAT_CHIPS:
        return ChatTurnResult(status="error", chat_id=chat_id, error=f"unknown chip {chip!r}")
    if text is not None and len(text) > MAX_CHAT_MESSAGE_CHARS:
        return ChatTurnResult(status="error", chat_id=chat_id,
                              error=f"text must be at most {MAX_CHAT_MESSAGE_CHARS} characters")
    try:
        entry = _chats.get(chat_id)
        result = await chat_workflow.run_chat_turn(
            entry, services, config, chip=chip, question=text.strip() if text else None
        )
    except KeyError:
        return ChatTurnResult(status="error", chat_id=chat_id,
                              error=_short(f"unknown chat_id {chat_id!r}"))
    except NodeError as err:
        trace = _failed_trace(err)
        _remember(trace)
        return ChatTurnResult(status="error", chat_id=chat_id, error=_node_error(err),
                              trace=trace)
    except Exception as exc:
        return ChatTurnResult(status="error", chat_id=chat_id,
                              error=_internal(exc, "send_message"))
    _remember(result.trace)
    return result


def _set_pins(chat_id: str, ids: list[str], pin: bool) -> PinResult:
    services, _ = _ensure()
    try:
        entry = _chats.get(chat_id)
    except KeyError:
        return PinResult(status="error", chat_id=chat_id,
                         error=_short(f"unknown chat_id {chat_id!r}"))
    try:
        ids = list(dict.fromkeys(i for i in ids or [] if isinstance(i, str)))
        session = entry.session
        current = list(session.context.pinned_evidence_ids)
        allowed = entry.scope.pinnable_ids if pin else set(current)
        rejected = [i for i in ids if i not in allowed]
        if rejected or not ids:
            return PinResult(
                status="rejected", chat_id=chat_id, pinned_evidence_ids=current,
                rejected_ids=rejected,
                error="ids must be transactions, accounts or derived facts of this chat's case"
                      if pin else "ids are not pinned" if rejected else "no ids given",
            )
        updated = (list(dict.fromkeys([*current, *ids])) if pin
                   else [i for i in current if i not in ids])
        if len(updated) > MAX_PINS:
            return PinResult(status="rejected", chat_id=chat_id, pinned_evidence_ids=current,
                             error=f"at most {MAX_PINS} pinned ids")
        with entry.lock:
            session.context = build_context(entry.scope, updated)
        audit.record(
            services.audit, action="chat.pin" if pin else "chat.unpin", object_type="case",
            object_id=session.case_id, actor_id=session.actor_id, actor_role=session.actor_role,
            metadata={"chat_id": chat_id, "ids": ",".join(ids)},
        )
    except Exception as exc:
        return PinResult(status="error", chat_id=chat_id, error=_internal(exc, "pin_evidence"))
    return PinResult(chat_id=chat_id, pinned_evidence_ids=updated)


def pin_evidence(chat_id: str, ids: list[str]) -> PinResult:
    """Pin transactions, accounts or derived facts of this chat's case; chips then start from
    them. All-or-nothing: any id outside the case rejects the call."""
    return _set_pins(chat_id, ids, pin=True)


def unpin_evidence(chat_id: str, ids: list[str]) -> PinResult:
    return _set_pins(chat_id, ids, pin=False)


def get_chat(chat_id: str) -> ChatResult:
    """The chat session: its case context (alert, evidence, pins) and every turn so far."""
    try:
        return ChatResult(chat=_chats.get(chat_id).session.model_copy(deep=True))
    except KeyError:
        return ChatResult(status="error", error=_short(f"unknown chat_id {chat_id!r}"))
    except Exception as exc:
        return ChatResult(status="error", error=_internal(exc, "get_chat"))


def record_feedback(
    chat_id: str, turn_id: str, helpful: bool, note: str | None = None
) -> FeedbackResult:
    """Reviewer feedback on one turn. The note is audited as a hash and length only."""
    services, _ = _ensure()
    result = {"chat_id": chat_id, "turn_id": turn_id}
    if not isinstance(helpful, bool):
        return FeedbackResult(status="error", error="helpful must be true or false", **result)
    if note is not None and len(note) > MAX_FEEDBACK_NOTE_CHARS:
        return FeedbackResult(status="error", **result,
                              error=f"note must be at most {MAX_FEEDBACK_NOTE_CHARS} characters")
    try:
        entry = _chats.get(chat_id)
        turn = next((t for t in entry.session.turns if t.turn_id == turn_id), None)
        if turn is None:
            return FeedbackResult(status="error", error=_short(f"unknown turn_id {turn_id!r}"),
                                  **result)
        note_hash = audit.sha256_json(note) if note else None
        with entry.lock:
            turn.feedback = ChatFeedback(helpful=helpful, note_hash=note_hash,
                                         recorded_at=datetime.now(UTC))
        session = entry.session
        event = audit.record(
            services.audit, action="chat.feedback", object_type="case",
            object_id=session.case_id, actor_id=session.actor_id, actor_role=session.actor_role,
            metadata={"chat_id": chat_id, "turn_id": turn_id, "trace_id": turn.trace_id,
                      "helpful": helpful, "note_hash": note_hash,
                      "note_chars": len(note) if note else 0},
        )
    except KeyError:
        return FeedbackResult(status="error", error=_short(f"unknown chat_id {chat_id!r}"),
                              **result)
    except Exception as exc:
        return FeedbackResult(status="error", error=_internal(exc, "record_feedback"), **result)
    return FeedbackResult(audit_event_id=event.event_id, **result)
