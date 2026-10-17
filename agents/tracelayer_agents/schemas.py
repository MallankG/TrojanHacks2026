"""Typed contracts for the agent layer.

Every node output, Protocol return value and facade result is one of these models.
Models are flat and JSON-serializable: `model_dump(mode="json")` round-trips through
`model_validate`.

Conventions:
- Money is `Decimal` (floats are rejected so binary rounding never reaches evidence or audit).
- Timestamps are timezone-aware and normalized to UTC (naive datetimes are rejected).
- Field names follow the PRD §13 tables where they overlap (noted per model).
- Score dicts are keyed by `transaction_id`; higher score = riskier.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    AliasChoices,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# --------------------------------------------------------------------------- annotated types


def _reject_float(value: Any) -> Any:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("amounts must be Decimal, int or str, not float/bool")
    return value


def _finite(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("amounts must be finite")
    return value


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _sha256_hex(value: str) -> str:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError("must be a lowercase hex sha256 digest")
    return value


def _non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be empty or whitespace")
    return value


Money = Annotated[Decimal, BeforeValidator(_reject_float), AfterValidator(_finite)]
UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]
Sha256Hex = Annotated[str, AfterValidator(_sha256_hex)]
NonBlankStr = Annotated[str, AfterValidator(_non_blank)]
CurrencyCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]

Mode = Literal["strict", "exploratory"]
GovernanceStatus = Literal["pass", "qualified", "fail"]
Severity = Literal["critical", "warning", "info"]
RemediationStatus = Literal["open", "in_progress", "resolved", "accepted_risk"]
AlertTier = Literal["high", "medium", "low"]
ClaimType = Literal["fact", "hypothesis", "policy"]
LLMProvider = Literal["groq", "ollama", "template"]
NodeStatus = Literal["ok", "error", "skipped"]


class Contract(BaseModel):
    """Base for all contracts: unknown fields are an error, so contract drift fails loudly."""

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- ingestion & quality


class QualityIssue(Contract):
    """One data-quality finding. Overlaps PRD `data_quality_issues` (field_name, issue_type,
    severity, remediation_note); `remediation_status` corresponds to that table's `status`."""

    check_id: str
    rule_id: str
    asset_id: str
    severity: Severity
    affected_rows: int = Field(ge=0)
    sample_record_ids: list[str] = Field(default_factory=list)
    owner_role: str
    steward_role: str
    remediation_status: RemediationStatus = "open"
    field_name: str | None = None
    issue_type: str | None = None
    remediation_note: str | None = None


class IngestionResult(Contract):
    """Result of loading one source file. Overlaps PRD `ingestion_runs`
    (`ingestion_run_id` is that table's `run_id`)."""

    ingestion_run_id: str
    transaction_batch_id: str
    source_asset_id: str
    file_id: str | None = None
    status: Literal["completed", "partial", "failed"]
    row_count: int = Field(ge=0)
    accepted_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    started_at: UtcDatetime
    completed_at: UtcDatetime | None = None

    @model_validator(mode="after")
    def _counts_add_up(self) -> IngestionResult:
        if self.accepted_count + self.rejected_count != self.row_count:
            raise ValueError("accepted_count + rejected_count must equal row_count")
        return self


class QualityReport(Contract):
    """Outcome of running the data contract on a batch. `status` drives the governance gate:
    any `critical` issue means `fail`; only warnings means `qualified`."""

    transaction_batch_id: str
    contract_version: str
    status: GovernanceStatus
    issues: list[QualityIssue] = Field(default_factory=list)
    rows_checked: int = Field(ge=0)
    checked_at: UtcDatetime

    @model_validator(mode="after")
    def _status_matches_issues(self) -> QualityReport:
        has_critical = any(i.severity == "critical" for i in self.issues)
        if has_critical and self.status != "fail":
            raise ValueError("a critical issue requires status='fail'")
        return self


# --------------------------------------------------------------------------- graph


class GraphSnapshot(Contract):
    graph_snapshot_id: str
    transaction_batch_id: str
    feature_schema_version: str
    node_count: int = Field(ge=0)
    edge_count: int = Field(ge=0)
    window_start: UtcDatetime
    window_end: UtcDatetime
    built_at: UtcDatetime


class SubgraphNode(Contract):
    account_id: str
    account_type: str | None = None


class SubgraphEdge(Contract):
    transaction_id: str
    origin_account_id: str
    destination_account_id: str
    amount: Money
    currency: CurrencyCode
    timestamp: UtcDatetime


class SubgraphJSON(Contract):
    graph_snapshot_id: str
    nodes: list[SubgraphNode]
    edges: list[SubgraphEdge]


# --------------------------------------------------------------------------- detection & fusion


class Factor(Contract):
    """One feature or rule that contributed to a score."""

    name: str
    kind: Literal["feature", "rule"]
    value: float | str | None = None
    contribution: float | None = None


class DetectorOutput(Contract):
    """Scores from one detector. Keys of every dict are transaction IDs present in `scores`."""

    detector_name: str
    model_version: str
    status: Literal["ok", "unavailable"] = "ok"
    scores: dict[str, float] = Field(default_factory=dict)
    top_factors: dict[str, list[Factor]] = Field(default_factory=dict)
    feature_snapshot_id: str
    typology_flags: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _keys_are_scored(self) -> DetectorOutput:
        scored = self.scores.keys()
        for name in ("top_factors", "typology_flags"):
            extra = getattr(self, name).keys() - scored
            if extra:
                raise ValueError(f"{name} has tx_ids not in scores: {sorted(extra)[:5]}")
        return self


class FusionResult(Contract):
    """Composite ranking. `coefficients` are whatever the fitted method learned (empty for
    parameter-free methods); they are recorded, never invented."""

    method: NonBlankStr
    coefficients: dict[str, float] = Field(default_factory=dict)
    composite: dict[str, float]
    model_run_id: str | None = None


class Alert(Contract):
    """A ranked alert. Overlaps PRD `alerts` (alert_id, rank, model_run_id; `component_scores`
    is that table's "component signals")."""

    alert_id: str
    tx_ids: list[str] = Field(min_length=1)
    composite_score: float
    component_scores: dict[str, float] = Field(min_length=1)
    typology_tags: list[str] = Field(default_factory=list)
    tier: AlertTier
    quality_status: GovernanceStatus
    confidence_caveat: Literal["incomplete_data"] | None = None
    graph_snapshot_id: str
    model_versions: dict[str, str]
    rank: int | None = Field(default=None, ge=1)
    model_run_id: str | None = None

    @model_validator(mode="after")
    def _failed_gate_is_caveated(self) -> Alert:
        if self.quality_status == "fail" and self.confidence_caveat is None:
            raise ValueError("alerts on a failed governance gate need confidence_caveat")
        return self


# --------------------------------------------------------------------------- flows & evidence


class FlowPattern(Contract):
    """One path, cycle, fan-in, fan-out or pass-through pattern, as account and transaction IDs."""

    account_ids: list[str] = Field(min_length=1)
    transaction_ids: list[str] = Field(min_length=1)
    total_amount: Money | None = None
    currency: CurrencyCode | None = None
    window_start: UtcDatetime | None = None
    window_end: UtcDatetime | None = None


class FlowFindings(Contract):
    alert_id: str
    graph_snapshot_id: str
    paths: list[FlowPattern] = Field(default_factory=list)
    cycles: list[FlowPattern] = Field(default_factory=list)
    fan_in: list[FlowPattern] = Field(default_factory=list)
    fan_out: list[FlowPattern] = Field(default_factory=list)
    pass_through: list[FlowPattern] = Field(default_factory=list)
    evidence_tx_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _patterns_cite_evidence(self) -> FlowFindings:
        evidence = set(self.evidence_tx_ids)
        for name in ("paths", "cycles", "fan_in", "fan_out", "pass_through"):
            for pattern in getattr(self, name):
                missing = set(pattern.transaction_ids) - evidence
                if missing:
                    raise ValueError(
                        f"{name} cites tx_ids not in evidence_tx_ids: {sorted(missing)}"
                    )
        return self


class EvidenceTransaction(Contract):
    """A transaction as evidence. Field names match PRD `transactions`."""

    transaction_id: str
    source_row_ref: str
    timestamp: UtcDatetime
    amount: Money
    currency: CurrencyCode
    channel: str | None = None
    direction: str | None = None
    origin_account_id: str
    destination_account_id: str
    transaction_status: str | None = None


class EvidenceBundle(Contract):
    """Everything the narrative may cite. The verifier treats this as the only source of facts.
    `bundle_hash` is sha256 of the canonical JSON of the other fields (computed by the node that
    builds the bundle)."""

    alert_id: str
    transactions: list[EvidenceTransaction] = Field(min_length=1)
    flow_findings: FlowFindings | None = None
    quality_caveats: list[QualityIssue] = Field(default_factory=list)
    lineage_refs: list[str] = Field(default_factory=list)
    bundle_hash: Sha256Hex

    @model_validator(mode="after")
    def _findings_reference_bundle(self) -> EvidenceBundle:
        if self.flow_findings is not None:
            known = {t.transaction_id for t in self.transactions}
            missing = set(self.flow_findings.evidence_tx_ids) - known
            if missing:
                raise ValueError(
                    f"flow_findings cite tx_ids not in transactions: {sorted(missing)}"
                )
        return self


# --------------------------------------------------------------------------- RAG


class RetrievedChunk(Contract):
    """A policy passage. Also accepts PRD `policy_chunks` column names (source_title, source_url,
    text) and ignores extra columns such as `embedding`."""

    model_config = ConfigDict(extra="ignore")

    chunk_id: str
    doc_title: str = Field(validation_alias=AliasChoices("doc_title", "source_title"))
    section: str | None = None
    source_uri: str = Field(validation_alias=AliasChoices("source_uri", "source_url"))
    excerpt: NonBlankStr = Field(validation_alias=AliasChoices("excerpt", "text"))
    score: float
    effective_date: date | None = None
    indexed_at: UtcDatetime | None = None


# --------------------------------------------------------------------------- narrative & verify


class NarrativeClaim(Contract):
    text: NonBlankStr
    claim_type: ClaimType
    transaction_ids: list[str] = Field(default_factory=list)
    account_ids: list[str] = Field(default_factory=list)
    amounts: list[Money] = Field(default_factory=list)
    dates: list[date] = Field(default_factory=list)
    citation_chunk_ids: list[str] = Field(default_factory=list)


class NarrativeDraft(Contract):
    summary: NonBlankStr
    observed_facts: list[NarrativeClaim] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    evidence_transaction_ids: list[str] = Field(default_factory=list)
    provider: LLMProvider
    model: str
    prompt_version: str
    fallback_used: bool = False


class PolicyAnswer(Contract):
    """Copilot answer. Every claim should be `claim_type="policy"` with citations."""

    question: NonBlankStr
    answer: str
    claims: list[NarrativeClaim] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    unanswerable: bool = False
    provider: LLMProvider
    model: str
    prompt_version: str
    fallback_used: bool = False


class VerifierError(Contract):
    """`claim_index` points into `NarrativeDraft.observed_facts`; None means the summary/draft."""

    claim_index: int | None = Field(default=None, ge=0)
    code: Literal[
        "unknown_transaction_id",
        "unknown_account_id",
        "unknown_amount",
        "unknown_date",
        "unknown_citation",
        "missing_citation",
        "prohibited_language",
        "schema_invalid",
    ]
    detail: str


# --------------------------------------------------------------------------- cases & decisions


class CaseStatus(StrEnum):
    OPEN = "open"
    DRAFTED = "drafted"
    NEEDS_REVISION = "needs_revision"
    AWAITING_REVIEW = "awaiting_review"
    ESCALATED = "escalated"
    DISMISSED = "dismissed"
    MORE_DATA_REQUESTED = "more_data_requested"
    HELD_FOR_DATA_REVIEW = "held_for_data_review"


DecisionAction = Literal["escalate", "dismiss", "request_more_data"]

DECISION_TO_STATUS: dict[str, CaseStatus] = {
    "escalate": CaseStatus.ESCALATED,
    "dismiss": CaseStatus.DISMISSED,
    "request_more_data": CaseStatus.MORE_DATA_REQUESTED,
}


class Decision(Contract):
    """A human reviewer's decision. Escalation is an internal step, never a filing."""

    action: DecisionAction
    actor_role: NonBlankStr
    reason: NonBlankStr
    actor_id: str | None = None
    edited_summary: str | None = None


class CaseRecord(Contract):
    """Fields match PRD `cases`, plus the alert the case was opened from."""

    case_id: str
    alert_id: str
    title: str
    status: CaseStatus
    assigned_to: str | None = None
    disposition: str | None = None
    created_at: UtcDatetime
    updated_at: UtcDatetime


# --------------------------------------------------------------------------- trace & audit


class TraceEvent(Contract):
    trace_id: str
    node: str
    status: NodeStatus
    started_at: UtcDatetime
    ended_at: UtcDatetime
    duration_ms: float = Field(ge=0)
    input_hash: Sha256Hex
    output_hash: Sha256Hex | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _ordered(self) -> TraceEvent:
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must not be before started_at")
        return self


class AuditEvent(Contract):
    """Append-only, hash-chained audit record. Overlaps PRD `audit_events` (event_id, actor_id,
    action, object_type, object_id, timestamp, metadata). `prev_hash`/`hash` are filled when the
    event is chained (`hash = sha256(prev_hash + canonical_json(event))`); None until then, and
    `prev_hash` stays None for the first event in the chain."""

    event_id: str
    actor_id: str | None = None
    actor_role: NonBlankStr
    action: NonBlankStr
    object_type: str
    object_id: str
    before_hash: Sha256Hex | None = None
    after_hash: Sha256Hex | None = None
    prev_hash: Sha256Hex | None = None
    hash: Sha256Hex | None = None
    model_versions: dict[str, str] = Field(default_factory=dict)
    prompt_version: str | None = None
    timestamp: UtcDatetime
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def _metadata_is_flat_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        for key, item in value.items():
            if not isinstance(item, str | int | float | bool | type(None)):
                raise ValueError(f"metadata[{key!r}] must be a JSON scalar")
        return value


# --------------------------------------------------------------------------- node outputs
#
# What each analysis node returns. `@traced_node` validates the model and merges every field that
# is not None into the graph state, so field names must match `state.AnalysisState` keys.


class IngestOutput(Contract):
    ingestion_run_id: str
    transaction_batch_id: str


class GovernanceOutput(Contract):
    governance_status: GovernanceStatus
    quality_issues: list[QualityIssue] = Field(default_factory=list)
    case_status: CaseStatus | None = None  # set only when the gate halts the run


class BuildGraphOutput(Contract):
    graph_snapshot_id: str
    feature_schema_version: str


class DetectorScoresOutput(Contract):
    """`model_scores` keyed by `detector_name`; parallel detector nodes merge via the reducer."""

    model_scores: dict[str, DetectorOutput]

    @model_validator(mode="after")
    def _keys_match_names(self) -> DetectorScoresOutput:
        for key, out in self.model_scores.items():
            if key != out.detector_name:
                raise ValueError(f"model_scores key {key!r} != detector_name {out.detector_name!r}")
        return self


class AggregateOutput(Contract):
    alerts: list[Alert]
    audit_events: list[AuditEvent] = Field(default_factory=list)


class InvestigateOutput(Contract):
    top_evidence_transaction_ids: list[str]
    evidence_bundle: EvidenceBundle
