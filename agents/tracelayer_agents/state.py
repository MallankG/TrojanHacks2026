"""LangGraph state for the three workflows.

Keys written by parallel branches carry an `Annotated` reducer; every other key is overwritten by
the last node that sets it. All states are `total=False`: nodes return partial updates.
"""

import operator
from typing import Annotated, TypedDict, TypeVar

from tracelayer_agents.schemas import (
    Alert,
    AuditEvent,
    CaseStatus,
    Decision,
    DerivedFact,
    DetectorOutput,
    EvidenceBundle,
    GovernanceStatus,
    LLMMeta,
    Mode,
    NarrativeDraft,
    PolicyAnswer,
    QualityIssue,
    RetrievedChunk,
    TraceEvent,
    VerifierError,
)

K = TypeVar("K")
V = TypeVar("V")


def merge_dicts(left: dict[K, V] | None, right: dict[K, V] | None) -> dict[K, V]:
    """Reducer for `model_scores`: union of both dicts; on a repeated key the newer value wins."""
    return {**(left or {}), **(right or {})}


class _TracedState(TypedDict, total=False):
    trace_id: str
    case_status: CaseStatus
    trace_events: Annotated[list[TraceEvent], operator.add]
    audit_events: Annotated[list[AuditEvent], operator.add]


class AnalysisState(_TracedState, total=False):
    # input
    batch_ref: str
    mode: Mode
    # lineage
    ingestion_run_id: str
    transaction_batch_id: str
    graph_snapshot_id: str
    feature_schema_version: str
    # governance
    governance_status: GovernanceStatus
    quality_issues: Annotated[list[QualityIssue], operator.add]
    # detection (detect_graphml and detect_behavior run in parallel)
    model_scores: Annotated[dict[str, DetectorOutput], merge_dicts]
    alerts: list[Alert]
    top_evidence_transaction_ids: list[str]
    evidence_bundle: EvidenceBundle | None


class CaseState(_TracedState, total=False):
    case_id: str
    alert_id: str
    alert: Alert
    ingestion_run_id: str
    graph_snapshot_id: str
    feature_schema_version: str
    evidence_bundle: EvidenceBundle | None
    derived_facts: list[DerivedFact]
    retrieved_sources: list[RetrievedChunk]
    draft_narrative: NarrativeDraft | None
    draft_attempts: int
    llm_meta: LLMMeta
    verifier_errors: list[VerifierError]
    flagged_claims: list[int]
    decision: Decision | None


class CopilotState(_TracedState, total=False):
    question: str
    alert_id: str | None
    evidence_bundle: EvidenceBundle | None
    retrieved_sources: list[RetrievedChunk]
    answer: PolicyAnswer | None
    verifier_errors: list[VerifierError]
