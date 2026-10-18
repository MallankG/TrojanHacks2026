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

import logging
from collections import OrderedDict
from typing import Any

from tracelayer_agents import audit
from tracelayer_agents.config import AgentConfig
from tracelayer_agents.interfaces import Services
from tracelayer_agents.nodes.human_decision import DecisionRejected
from tracelayer_agents.registry import get_services
from tracelayer_agents.schemas import (
    AnalysisResult,
    AuditVerification,
    CaseDecisionResult,
    CaseDraftResult,
    CaseStatus,
    CopilotResult,
    CytoscapeEdge,
    CytoscapeEdgeData,
    CytoscapeGraph,
    CytoscapeNode,
    CytoscapeNodeData,
    Decision,
    Mode,
    TraceEvent,
)
from tracelayer_agents.workflows import analysis as analysis_workflow
from tracelayer_agents.workflows import case as case_workflow
from tracelayer_agents.workflows import copilot as copilot_workflow
from tracelayer_agents.wrappers import NodeError

logger = logging.getLogger(__name__)

# How many runs' traces `get_trace` keeps in memory (oldest evicted first).
TRACE_HISTORY = 50
MAX_ERROR_CHARS = 200
MAX_GRAPH_HOPS = 3

_services: Services | None = None
_config: AgentConfig | None = None
_traces: OrderedDict[str, list[TraceEvent]] = OrderedDict()


def configure(services: Services | None = None, config: AgentConfig | None = None) -> None:
    """Resolve the services once (each missing one falls back to its mock, with one warning) and
    keep them with the config for every later call. Safe to call again: it replaces both."""
    global _services, _config
    _services = get_services(services)
    _config = config or AgentConfig.from_env()


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


class AgentRuntime:
    """Explicit live runtime. No module-global repositories, mock fallback or golden fixtures.

    The backend creates one per authorized run and supplies a durable, run-scoped checkpointer.
    Node traces are streamed through the audit adapter's optional record_trace method.
    """

    def __init__(self, services: Services, config: AgentConfig, *, checkpointer, thread_id: str):
        from dataclasses import fields
        from tracelayer_agents.registry import mocks_forced
        if mocks_forced() or config.use_precomputed:
            raise ValueError("Mock and precomputed execution are forbidden in the live runtime")
        if any(getattr(services, f.name) is None for f in fields(Services)):
            raise ValueError("Every live service adapter must be supplied explicitly")
        self.services, self.config = services, config
        self.checkpointer, self.thread_id = checkpointer, thread_id

    async def run_analysis(self, batch_ref: str, mode: Mode = "strict") -> AnalysisResult:
        state = await analysis_workflow.run_analysis(batch_ref, mode, self.services, self.config)
        halted = state.get("case_status") == CaseStatus.HELD_FOR_DATA_REVIEW
        return AnalysisResult(status="halted" if halted else "ok", halted=halted,
            trace_id=state["trace_id"], governance_status=state.get("governance_status"),
            alerts=state.get("alerts", []), trace=state.get("trace_events", []))

    async def start_case(self, alert_id: str, case_id: str) -> CaseDraftResult:
        return await case_workflow.start_case(alert_id, self.services, self.config,
            case_id=case_id, thread_id=self.thread_id, checkpointer=self.checkpointer)

    async def resume_case(self, case_id: str, decision: Decision) -> CaseDecisionResult:
        return await case_workflow.resume_case(case_id, decision, self.services, self.config,
            thread_id=self.thread_id, checkpointer=self.checkpointer)

    async def recover_case(self, alert_id: str, case_id: str):
        return await case_workflow.recover_case(case_id, alert_id, self.services, self.config,
            thread_id=self.thread_id, checkpointer=self.checkpointer)

    async def copilot_query(self, question: str, alert_id: str | None = None) -> CopilotResult:
        return await copilot_workflow.run_copilot(question, alert_id, self.services, self.config)

# Public contracts/helpers for backend adapters. Workflow internals remain private.
from tracelayer_agents import schemas as contracts
canonical_hash = audit.sha256_json
hash_event = audit.event_hash
verify_events = audit.verify_chain

def checkpoint_serializer():
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    types = [v for v in vars(contracts).values() if isinstance(v, type) and v.__module__ == contracts.__name__]
    return JsonPlusSerializer(allowed_msgpack_modules=types)


def verify_narrative(draft: contracts.NarrativeDraft, bundle: contracts.EvidenceBundle,
                     sources: list[contracts.RetrievedChunk]) -> list[contracts.VerifierError]:
    """Deterministic verification for human edits and preview; never modifies a workflow."""
    from tracelayer_agents.nodes.verify import verify_evidence
    return verify_evidence(draft,bundle,sources)
