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
from typing import Annotated, Any, Literal, get_args

from pydantic import (
    AfterValidator,
    AliasChoices,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationInfo,
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
SPELLED_NUMBER_RE = re.compile(
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
    r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|"
    r"ninety|hundreds?|thousands?|millions?|billions?|dozens?|twice|double|triple|half|halves)\b",
    re.IGNORECASE,
)
# A standalone number (a count like "14 accounts"). Digits inside ids (TX-9), amounts ($1,200.50,
# 300 USD) and dates (2026-01-03) don't match: the verifier grounds those and flags invented ones.
BARE_NUMBER_RE = re.compile(
    r"(?<![\w$€£¥.,:/-])\d+(?:\.\d+)?(?![\w.,:/%-]|\s*(?:USD|EUR|GBP|JPY|CAD|AUD|CHF|CNY|INR)\b)"
)


def _model_states_numbers(texts: list[str]) -> bool:
    """True when model prose states a count or multiplier the verifier could not ground."""
    return any(SPELLED_NUMBER_RE.search(t) or BARE_NUMBER_RE.search(t) for t in texts)


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
LLMProvider = Literal["ollama", "template"]
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
    query_caveats: list[str] = Field(default_factory=list)
    truncated: bool = False

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
    limitations: list[str] = Field(default_factory=list)
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
    # narrative_v4: sectioned view of the draft (`llm.templates.narrative_sections`); None on
    # drafts made before v4.
    sections: NarrativeSections | None = None
    # 1 for the drafted narrative; a reviewer's edited summary is saved as version + 1 with the
    # previous version's `draft_hash` (`audit.sha256_json(draft)`) as parent.
    version: int = Field(default=1, ge=1)
    parent_draft_hash: Sha256Hex | None = None
    edited_by: str | None = None


NarrativeSectionName = Literal[
    "observed_facts",
    "model_rule_inferences",
    "analyst_hypotheses",
    "contradictory_evidence",
    "data_gaps",
    "relevant_policy",
    "recommended_next_steps",
]
NARRATIVE_SECTIONS: tuple[str, ...] = get_args(NarrativeSectionName)


class NarrativeSectionItem(Contract):
    """One line of a narrative section. A `factual` item states something about the evidence and
    must cite `evidence_ids` (transaction, account, chunk, quality-check, lineage, derived-fact,
    `model_run:<id>`, `graph_snapshot:<id>` or `not_available:<item>` ids); hypotheses and next
    steps are not factual and may cite the facts they rest on."""

    text: NonBlankStr
    factual: bool
    evidence_ids: list[str] = Field(default_factory=list)


class NarrativeSections(Contract):
    """The narrative split the way a reviewer reads it (PRD 8.2). Built in Python from the draft,
    the evidence bundle and the alert; the LLM only contributes hypothesis, contradiction and
    next-step prose."""

    observed_facts: list[NarrativeSectionItem] = Field(default_factory=list)
    model_rule_inferences: list[NarrativeSectionItem] = Field(default_factory=list)
    analyst_hypotheses: list[NarrativeSectionItem] = Field(default_factory=list)
    contradictory_evidence: list[NarrativeSectionItem] = Field(default_factory=list)
    data_gaps: list[NarrativeSectionItem] = Field(default_factory=list)
    relevant_policy: list[NarrativeSectionItem] = Field(default_factory=list)
    recommended_next_steps: list[NarrativeSectionItem] = Field(default_factory=list)


class DerivedFact(Contract):
    """A number computed in Python from an `EvidenceBundle` (`nodes.derive`), so the narrative can
    quote totals without the LLM doing arithmetic. `value` is canonical text: an amount with two
    decimals ("9950.00"), an integer count, hours with two decimals, or an ISO UTC timestamp."""

    fact_id: str = Field(pattern=r"^DF-\d+$")
    label: str
    kind: Literal["amount", "count", "duration_hours", "timestamp"]
    value: str
    currency: CurrencyCode | None = None
    transaction_ids: list[str] = Field(min_length=1)
    account_ids: list[str] = Field(default_factory=list)


class NarrativeOutput(Contract):
    """A merged narrative (`llm.templates.build_narrative`: deterministic fact and policy claims
    plus a `NarrativeSynthesis`). `draft_narrative` adds the derived citation/evidence lists and
    provenance to make a `NarrativeDraft`."""

    summary: NonBlankStr
    observed_facts: list[NarrativeClaim] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)


NARRATIVE_MAX_HYPOTHESES = 3
NARRATIVE_MAX_SYNTHESIS_ITEMS = 5


class NarrativeHypothesis(Contract):
    """One possible interpretation. `fact_refs` are 1-based numbers of the fact claims it rests on;
    `draft_narrative` maps them back to those claims' transaction and account ids."""

    text: NonBlankStr
    fact_refs: list[int] = Field(min_length=1)


class NarrativeSynthesis(Contract):
    """What the `narrative` prompt (v2, or its template) returns: only the prose around the fact
    and policy claims, which are built deterministically. `draft_narrative` validates it against
    `nodes.draft_narrative.synthesis_model(n)`, which limits `fact_refs` to the real claim
    numbers."""

    summary: NonBlankStr
    hypotheses: list[NarrativeHypothesis] = Field(
        default_factory=list, max_length=NARRATIVE_MAX_HYPOTHESES
    )
    uncertainties: list[str] = Field(
        default_factory=list, max_length=NARRATIVE_MAX_SYNTHESIS_ITEMS
    )
    follow_up_questions: list[str] = Field(
        default_factory=list, max_length=NARRATIVE_MAX_SYNTHESIS_ITEMS
    )
    # narrative_v4: observations that cut against the alert, by fact claim number.
    contradictions: list[NarrativeHypothesis] = Field(
        default_factory=list, max_length=NARRATIVE_MAX_HYPOTHESES
    )

    @model_validator(mode="after")
    def synthesis_cannot_spell_out_numbers(self, info: ValidationInfo):
        # Model prose may not state counts or multipliers in words: the verifier parses ids,
        # amounts and dates and flags any it cannot ground (PRD 16), but it cannot check
        # "twelve accounts" or "double". Only model output is restricted; the template and
        # checkpoint deserialization (no context) may quote computed facts.
        if (info.context or {}).get("source") == "ollama":
            prose = [self.summary, *self.uncertainties, *self.follow_up_questions,
                     *(h.text for h in [*self.hypotheses, *self.contradictions])]
            if _model_states_numbers(prose):
                raise ValueError("Model prose must not state counts or multipliers; "
                                 "reference fact claims by number instead")
        return self


class PolicyClaim(Contract):
    """One statement in a policy answer and the retrieved chunks that support it."""

    text: NonBlankStr
    citation_chunk_ids: list[str] = Field(default_factory=list)


class PolicyQAOutput(Contract):
    """What the `policy_qa` prompt (or its template) returns. The node adds provenance to make a
    `PolicyAnswer`."""

    answer: str
    claims: list[PolicyClaim] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    no_answer: bool = False


class PolicyAnswer(Contract):
    """Copilot answer. After `verify_citations`, every claim cites only retrieved chunk_ids and
    `citations` is the ordered union of those ids. `no_answer=True` means the retrieved sources
    don't cover the question."""

    question: NonBlankStr
    answer: str
    claims: list[PolicyClaim] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    no_answer: bool = False
    provider: LLMProvider
    model: str
    prompt_version: str
    fallback_used: bool = False


class LLMMeta(Contract):
    """How one `llm.client.generate_structured` call was answered. `errors` are short codes, never
    prompt text; `input_hash` is sha256 of the canonical JSON of prompt_version + variables.
    `fallback_used` is True whenever the deterministic template produced the output."""

    provider: LLMProvider
    model: str
    prompt_version: str
    input_hash: Sha256Hex
    attempts: int = Field(ge=0)  # LLM HTTP calls made (0 when the template answered directly)
    fallback_used: bool
    latency_ms: float = Field(ge=0)
    errors: list[str] = Field(default_factory=list)


class VerifierError(Contract):
    """`claim_index` points into `NarrativeDraft.observed_facts` (case) or into the copilot claims
    as the LLM returned them, before verification. Free text outside a claim is -1 in the case
    verifier (summary, uncertainties, follow-up questions) and None in the copilot (answer,
    unknowns). The upper-case codes are the case verifier's (`nodes.verify.verify_evidence`)."""

    claim_index: int | None = Field(default=None, ge=-1)
    code: Literal[
        "unknown_transaction_id",
        "unknown_account_id",
        "unknown_amount",
        "unknown_date",
        "unknown_citation",
        "missing_citation",
        "prohibited_language",
        "schema_invalid",
        "UNKNOWN_TX",
        "UNKNOWN_ACCOUNT",
        "UNSUPPORTED_AMOUNT",
        "UNSUPPORTED_DATE",
        "MISSING_EVIDENCE",
        "BAD_CITATION",
        "UNGROUNDED_TOKEN",
        "PROHIBITED_LANGUAGE",
        "MISSING_ALTERNATIVE",
        "SECTION_UNCITED",
        # chat verifier (`chat.verify_chat`)
        "NOT_ON_PATH",
        "ARITHMETIC_MISMATCH",
        "OUT_OF_SCOPE",
        "UNSUPPORTED_BENIGN",
        "DISPOSITION_LANGUAGE",
        "BAD_FACT_REF",
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
ActorRole = Literal["analyst", "supervisor"]

DECISION_TO_STATUS: dict[str, CaseStatus] = {
    "escalate": CaseStatus.ESCALATED,
    "dismiss": CaseStatus.DISMISSED,
    "request_more_data": CaseStatus.MORE_DATA_REQUESTED,
}


class Decision(Contract):
    """A human reviewer's decision. Escalation is an internal step, never a filing. Role-based
    rules (who may take which action) are enforced in `nodes.human_decision`."""

    action: DecisionAction
    actor_role: ActorRole
    reason: NonBlankStr
    actor_id: NonBlankStr
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


class AuditVerification(Contract):
    """Result of `audit.verify_chain`. `checked` counts the events that verified before the first
    bad one (all of them when `ok`); `first_bad_index` is that event's position in the list."""

    ok: bool
    checked: int = Field(ge=0)
    first_bad_index: int | None = Field(default=None, ge=0)
    reason: str | None = None


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


class RetrievePolicyOutput(Contract):
    retrieved_sources: list[RetrievedChunk]


class CopilotAnswerOutput(Contract):
    answer: PolicyAnswer
    precomputed: bool | None = None  # True when served from the golden run (`precomputed`)


class VerifyCitationsOutput(Contract):
    answer: PolicyAnswer
    verifier_errors: list[VerifierError]


class LoadCaseOutput(Contract):
    alert: Alert
    evidence_bundle: EvidenceBundle
    derived_facts: list[DerivedFact]


class DraftNarrativeOutput(Contract):
    draft_narrative: NarrativeDraft
    draft_attempts: int = Field(ge=1)
    llm_meta: LLMMeta
    precomputed: bool | None = None  # True when served from the golden run (`precomputed`)


class VerifyEvidenceOutput(Contract):
    verifier_errors: list[VerifierError]
    flagged_claims: list[int]  # observed_facts indexes with at least one error
    case_status: CaseStatus


class SaveDraftOutput(Contract):
    case_status: CaseStatus
    audit_events: list[AuditEvent]


class HumanDecisionOutput(Contract):
    decision: Decision


class RecordDecisionOutput(Contract):
    case_status: CaseStatus
    audit_events: list[AuditEvent]
    edited_draft: NarrativeDraft | None = None
    edited_verifier_errors: list[VerifierError] = Field(default_factory=list)


ResultStatus = Literal["ok", "error"]


def _require_when_ok(model: Any, *names: str) -> None:
    if model.status == "ok":
        missing = [n for n in names if getattr(model, n) is None]
        if missing:
            raise ValueError(f"status='ok' requires {', '.join(missing)}")


class CaseDraftResult(Contract):
    """What `workflows.case.start_case` returns. With `case_status="needs_revision"` the draft is
    kept and `flagged_claims` lists the claims the UI must show as unverified. The facade returns
    `status="error"` with a short `error` (and the trace so far) instead of raising."""

    status: ResultStatus = "ok"
    error: str | None = None
    trace_id: str | None = None
    case_id: str | None = None
    alert_id: str
    draft: NarrativeDraft | None = None
    verifier_errors: list[VerifierError] = Field(default_factory=list)
    flagged_claims: list[int] = Field(default_factory=list)
    llm_meta: LLMMeta | None = None
    case_status: CaseStatus | None = None
    trace: list[TraceEvent] = Field(default_factory=list)
    draft_hash: Sha256Hex | None = None  # `audit.sha256_json(draft)`, also in the audit trail
    # True when the draft was served from the precomputed golden run; `draft` and `llm_meta` then
    # keep the original run's provider, model and prompt_version.
    precomputed: bool = False

    @model_validator(mode="after")
    def _complete_when_ok(self) -> CaseDraftResult:
        _require_when_ok(self, "trace_id", "case_id", "draft", "llm_meta", "case_status")
        return self


class CaseDecisionResult(Contract):
    """What `workflows.case.resume_case` returns: the case's new status, the `case.decision` audit
    event and the full node trace of the case run. The facade returns `status="rejected"` when the
    decision is invalid or the role may not take it (the case stays paused), and `status="error"`
    for an unknown or already-decided case."""

    status: Literal["ok", "rejected", "error"] = "ok"
    error: str | None = None
    case_id: str
    case_status: CaseStatus | None = None
    audit_event: AuditEvent | None = None
    trace: list[TraceEvent] = Field(default_factory=list)
    # Set when the decision carried `edited_summary`: the reviewer's version of the draft
    # (version + 1, `parent_draft_hash` = the drafted version), re-verified for information only
    # (the reviewer owns the text; errors are shown, not blocking).
    edited_draft: NarrativeDraft | None = None
    edited_draft_hash: Sha256Hex | None = None
    edited_verifier_errors: list[VerifierError] = Field(default_factory=list)

    @model_validator(mode="after")
    def _complete_when_ok(self) -> CaseDecisionResult:
        _require_when_ok(self, "case_status", "audit_event")
        return self


class CopilotResult(Contract):
    """What `workflows.copilot.run_copilot` returns: the verified answer, the sources it was
    checked against, the verifier's findings and the node trace."""

    status: ResultStatus = "ok"
    error: str | None = None
    trace_id: str | None = None
    answer: PolicyAnswer | None = None
    sources: list[RetrievedChunk] = Field(default_factory=list)
    verifier_errors: list[VerifierError] = Field(default_factory=list)
    trace: list[TraceEvent] = Field(default_factory=list)
    # True when the answer was served from the precomputed golden run (original provenance kept).
    precomputed: bool = False

    @model_validator(mode="after")
    def _complete_when_ok(self) -> CopilotResult:
        _require_when_ok(self, "trace_id", "answer")
        return self


class AnalysisResult(Contract):
    """What `api.run_analysis` returns. `halted` (status `"halted"`) means the strict governance
    gate stopped the run with `case_status="held_for_data_review"` and no alerts."""

    status: Literal["ok", "halted", "error"]
    error: str | None = None
    trace_id: str | None = None
    governance_status: GovernanceStatus | None = None
    halted: bool = False
    alerts: list[Alert] = Field(default_factory=list)
    trace: list[TraceEvent] = Field(default_factory=list)


# --------------------------------------------------------------------------- alert graph (UI)


class CytoscapeNodeData(Contract):
    id: str  # account_id
    label: str
    risk: float | None = None  # the alert's composite score for accounts on an evidence edge
    quality: GovernanceStatus | None = None  # the alert's governance status


class CytoscapeNode(Contract):
    data: CytoscapeNodeData


class CytoscapeEdgeData(Contract):
    id: str  # transaction_id
    source: str
    target: str
    amount: str  # decimal string, e.g. "9500.00"
    timestamp: UtcDatetime
    tx_id: str
    highlighted: bool  # True for the alert's evidence transactions


class CytoscapeEdge(Contract):
    data: CytoscapeEdgeData


class CytoscapeGraph(Contract):
    """What `api.get_alert_graph` returns: Cytoscape.js elements (`{nodes, edges}`)."""

    status: ResultStatus = "ok"
    error: str | None = None
    nodes: list[CytoscapeNode] = Field(default_factory=list)
    edges: list[CytoscapeEdge] = Field(default_factory=list)


# --------------------------------------------------------------------------- investigation chat
#
# A chat is bound to one case. Tools are read-only and allowlisted (`tracelayer_agents.tools`);
# every claim in an answer carries a label and the ids it rests on, and `chat.verify_chat` drops
# any claim it can't trace to this turn's tool results or the case context.

ChatChip = Literal[
    "explain_alert",
    "trace_backward",
    "trace_forward",
    "why_connected",
    "evidence_per_signal",
    "benign_explanations",
    "policy_guidance",
    "missing_data",
    "similar_cases",
    "next_steps",
    "shared_identifiers",
]
CHAT_CHIPS: tuple[str, ...] = get_args(ChatChip)
ChatToolName = Literal[
    "transaction_lookup",
    "trace_backward",
    "trace_forward",
    "time_window_aggregate",
    "cycle_search",
    "related_entities",
    "model_explanation",
    "quality_lineage",
    "policy_retrieval",
    "similar_cases",
    "shared_token_search",
]
CHAT_TOOLS: tuple[str, ...] = get_args(ChatToolName)
ClaimLabel = Literal["observed_fact", "model_inference", "policy", "hypothesis", "missing_evidence"]
BenignKind = Literal["payroll", "treasury_sweep", "refund", "reversal", "shared_service"]
BENIGN_KINDS: tuple[str, ...] = get_args(BenignKind)
ToolStatus = Literal["ok", "error", "timeout", "rejected"]
VerificationStatus = Literal["verified", "partial", "unverified"]
MAX_TOOL_CALLS_PER_TURN = 3
CHAT_MAX_HYPOTHESES = 3
CHAT_MAX_NEXT_STEPS = 5


class ToolCall(Contract):
    """One allowlisted tool call. `args` are checked against the tool's own input model."""

    tool: ChatToolName
    args: dict[str, Any] = Field(default_factory=dict)


class ChatPlan(Contract):
    """What the `chat_plan` prompt (or its template) returns for a free-text question."""

    intent: NonBlankStr
    tool_calls: list[ToolCall] = Field(default_factory=list, max_length=MAX_TOOL_CALLS_PER_TURN)


class ChatPlanDraft(Contract):
    """The `chat_plan` prompt's raw output: tool calls are loose dicts here so that an invalid
    plan reaches `chat_classify` (which asks for a narrower question) instead of being retried
    into the template."""

    intent: str
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)


class ToolRefs(Contract):
    """Every id a tool result may be cited by. `paths` are the edge (transaction) id lists of the
    paths or cycles it returned, for path-membership checks."""

    tx_ids: list[str] = Field(default_factory=list)
    account_ids: list[str] = Field(default_factory=list)
    edge_ids: list[str] = Field(default_factory=list)
    paths: list[list[str]] = Field(default_factory=list)
    chunk_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)  # lineage refs, check ids, references
    model_run_id: str | None = None


class ComputedValue(Contract):
    """A number a tool computed in Python. Amount values are recomputed by the chat verifier from
    `transaction_ids`."""

    key: str
    kind: Literal["amount", "count", "timestamp"]
    value: str
    currency: CurrencyCode | None = None
    transaction_ids: list[str] = Field(default_factory=list)


class ToolResult(Contract):
    """Outcome of one tool call. `rows` are plain JSON (money as decimal strings), at most
    `tools.MAX_ROWS`; `truncated` says more existed. `warnings` name parts that could not be
    checked (e.g. a scorer that failed); `error` is a short reason, never a stack trace."""

    tool: ChatToolName
    status: ToolStatus
    args: dict[str, Any] = Field(default_factory=dict)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = Field(default=0, ge=0)
    truncated: bool = False
    refs: ToolRefs = Field(default_factory=ToolRefs)
    computed: list[ComputedValue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    duration_ms: float = Field(default=0, ge=0)


class ToolCallRecord(Contract):
    """A tool call as shown to the UI and audited: no rows, only counts."""

    tool: ChatToolName
    args: dict[str, Any] = Field(default_factory=dict)
    status: ToolStatus
    row_count: int = Field(ge=0)
    truncated: bool = False
    duration_ms: float = Field(ge=0)
    error: str | None = None


class ChatClaim(Contract):
    """One labelled statement in a chat answer and everything it cites. Every label except
    `missing_evidence` must cite at least one id."""

    text: NonBlankStr
    label: ClaimLabel
    transaction_ids: list[str] = Field(default_factory=list)
    account_ids: list[str] = Field(default_factory=list)
    edge_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    chunk_ids: list[str] = Field(default_factory=list)
    model_run_id: str | None = None
    amounts: list[Money] = Field(default_factory=list)
    source_tools: list[ChatToolName] = Field(default_factory=list)
    benign_kind: BenignKind | None = None


class ChatGap(Contract):
    """A claim (or free-text part) the verifier dropped. The dropped text itself is not repeated,
    only its hash, so unverified content never reaches the UI."""

    label: ClaimLabel | Literal["summary", "next_step"]
    text_hash: Sha256Hex
    codes: list[str] = Field(min_length=1)
    detail: str
    suggested_query: str


class ChatHypothesis(Contract):
    """A possible interpretation. `fact_refs` are 1-based numbers of the fact claims it rests on;
    `benign_kind` is set for a benign counter-hypothesis, which needs tool support."""

    text: NonBlankStr
    fact_refs: list[int] = Field(min_length=1)
    benign_kind: BenignKind | None = None


class ChatSynthesis(Contract):
    """What the `chat_answer` prompt (or its template) returns: prose around the deterministic
    claims, never facts of its own."""

    summary: NonBlankStr
    hypotheses: list[ChatHypothesis] = Field(default_factory=list, max_length=CHAT_MAX_HYPOTHESES)
    next_steps: list[str] = Field(default_factory=list, max_length=CHAT_MAX_NEXT_STEPS)

    @model_validator(mode="after")
    def synthesis_cannot_state_counts(self, info: ValidationInfo):
        # Same rule as NarrativeSynthesis: only model output (parsed with source=ollama) is checked.
        if (info.context or {}).get("source") == "ollama" and _model_states_numbers(
                [self.summary, *self.next_steps, *(h.text for h in self.hypotheses)]):
            raise ValueError("Model prose must not state counts or multipliers; cite claims instead")
        return self


class ChatContext(Contract):
    """The case a chat is bound to. Returned with every chat response."""

    case_id: str
    alert_id: str
    alert: Alert
    evidence_bundle: EvidenceBundle
    derived_facts: list[DerivedFact] = Field(default_factory=list)
    quality_issues: list[QualityIssue] = Field(default_factory=list)
    graph_snapshot_id: str
    window_start: UtcDatetime
    window_end: UtcDatetime
    entity_scope: list[str] = Field(default_factory=list)  # account ids the chat may query
    scope_transaction_count: int = Field(ge=0)
    pinned_evidence_ids: list[str] = Field(default_factory=list)
    scope_notes: list[str] = Field(default_factory=list)
    # Transactions (graph edges) the reviewer selected; chips start from them before pins.
    selected_ids: list[str] = Field(default_factory=list)
    # Bumped on every confirmed context change (selection, pins).
    context_version: int = Field(default=1, ge=1)


class SourceRef(Contract):
    """A policy source retrieved for one answer (no excerpt: the claims quote what they use)."""

    chunk_id: str
    doc_title: str
    section: str | None = None
    source_uri: str | None = None


class Coverage(Contract):
    """How much of an answer survived verification, as counts (no single confidence number):
    `verified` claims are fully grounded facts, inferences and policy; `partial` are kept
    hypotheses and claims from truncated or warned tool results; `unsupported` are dropped parts
    plus missing-evidence statements."""

    verified: int = Field(ge=0)
    partial: int = Field(ge=0)
    unsupported: int = Field(ge=0)


class AnswerMetadata(Contract):
    """Provenance of one chat answer. `as_of` is the data freshness: the latest evidence
    timestamp the answer could see (`as_of_basis` says so)."""

    as_of: UtcDatetime
    as_of_basis: Literal["latest_evidence_timestamp"] = "latest_evidence_timestamp"
    graph_snapshot_id: str
    context_version: int = Field(ge=1)
    retrieved_sources: list[SourceRef] = Field(default_factory=list)
    tool_actions: list[ToolCallRecord] = Field(default_factory=list)
    coverage: Coverage


class ChatFeedback(Contract):
    helpful: bool
    note_hash: Sha256Hex | None = None
    recorded_at: UtcDatetime


class ChatTurn(Contract):
    """One question and its verified answer, as kept on the session."""

    turn_id: str
    trace_id: str
    created_at: UtcDatetime
    chip: ChatChip | None = None
    question: str | None = None
    question_hash: Sha256Hex
    status: Literal["ok", "needs_clarification", "denied", "error"]
    intent: str
    answer: str
    claims: list[ChatClaim] = Field(default_factory=list)
    gaps: list[ChatGap] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    suggested_chips: list[ChatChip] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    verification_status: VerificationStatus
    llm_meta: list[LLMMeta] = Field(default_factory=list)
    feedback: ChatFeedback | None = None
    # Plain-language notes on how an unbounded request was narrowed (hops, window, accounts).
    bounds_applied: list[str] = Field(default_factory=list)
    metadata: AnswerMetadata | None = None
    # True when an LLM output was served from the precomputed golden run (`llm_meta` keeps the
    # original provider, model and prompt_version); tools and the verifier still ran live.
    precomputed: bool = False


class ChatSession(Contract):
    chat_id: str
    case_id: str
    alert_id: str
    actor_id: NonBlankStr
    actor_role: ActorRole
    tenant_id: str | None = None
    created_at: UtcDatetime
    context: ChatContext
    turns: list[ChatTurn] = Field(default_factory=list)


class ChatResult(Contract):
    """What `api.start_chat` and `api.get_chat` return. `denied` = the access policy refused this
    actor the case; nothing about it is returned."""

    status: Literal["ok", "denied", "error"] = "ok"
    error: str | None = None
    chat: ChatSession | None = None
    available_chips: list[ChatChip] = Field(default_factory=lambda: list(CHAT_CHIPS))

    @model_validator(mode="after")
    def _complete_when_ok(self) -> ChatResult:
        _require_when_ok(self, "chat")
        return self


class ChatTurnResult(Contract):
    """What `api.send_message` returns. `needs_clarification` means no tool ran: the question
    couldn't be mapped to an allowlisted plan inside this case;
    `suggested_chips` say what to ask. `denied` means the request was refused (access policy, or
    it names another case's records): `turn` explains it without ids, or is None when the
    access policy refused the case itself."""

    status: Literal["ok", "needs_clarification", "denied", "error"] = "ok"
    error: str | None = None
    chat_id: str
    turn: ChatTurn | None = None
    context: ChatContext | None = None
    trace: list[TraceEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def _complete_when_ok(self) -> ChatTurnResult:
        if self.status in ("ok", "needs_clarification") and (
                self.turn is None or self.context is None):
            raise ValueError(f"status={self.status!r} requires turn and context")
        return self


class PinResult(Contract):
    """`rejected` = at least one id is not part of this chat's case; nothing was changed."""

    status: Literal["ok", "rejected", "denied", "error"] = "ok"
    error: str | None = None
    chat_id: str
    pinned_evidence_ids: list[str] = Field(default_factory=list)
    rejected_ids: list[str] = Field(default_factory=list)


class ContextChangePreview(Contract):
    """What `update_chat_context` would change. Out-of-scope ids are only counted."""

    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    selected_ids: list[str] = Field(default_factory=list)  # the selection after the change
    out_of_scope_count: int = Field(default=0, ge=0)
    current_context_version: int = Field(ge=1)
    next_context_version: int = Field(ge=1)


class ContextChangeResult(Contract):
    """What `api.update_chat_context` returns. `preview` = nothing changed yet (call again with
    confirm=true); `rejected` = some id is outside this chat's case, nothing changed; `denied` =
    the access policy refused the case."""

    status: Literal["ok", "preview", "rejected", "denied", "error"] = "ok"
    error: str | None = None
    chat_id: str
    preview: ContextChangePreview | None = None
    context: ChatContext | None = None
    audit_event_id: str | None = None


class FeedbackResult(Contract):
    status: Literal["ok", "denied", "error"] = "ok"
    error: str | None = None
    chat_id: str
    turn_id: str
    audit_event_id: str | None = None


class ChatGuardOutput(Contract):
    """`chat_guard`: `denied` names why the request was refused (reason codes, no ids);
    `narrowed` lists the bounds applied to an unbounded request."""

    denied: list[str] = Field(default_factory=list)
    narrowed: list[str] = Field(default_factory=list)
    requested_hops: int | None = None
    clarification: str | None = None
    intent: str | None = None


class ChatClassifyOutput(Contract):
    intent: str
    plan: list[ToolCall]
    clarification: str | None = None
    narrowed: list[str] = Field(default_factory=list)
    llm_meta: list[LLMMeta] = Field(default_factory=list)
    precomputed: bool = False


class ChatToolQueryOutput(Contract):
    tool_results: list[ToolResult]


class ChatDraftOutput(Contract):
    draft_claims: list[ChatClaim]
    synthesis: ChatSynthesis
    benign_support: dict[str, list[str]]
    llm_meta: list[LLMMeta] = Field(default_factory=list)
    precomputed: bool = False


class ChatVerifyOutput(Contract):
    claims: list[ChatClaim]
    gaps: list[ChatGap]
    verifier_errors: list[VerifierError]
    answer: str
    next_steps: list[str]
    verification_status: VerificationStatus


class ChatRespondOutput(Contract):
    audit_events: list[AuditEvent]


# Allowlisted tool arguments at the live adapter boundary. No free-form query language.
class EvidenceLookup(Contract):
    transaction_ids:list[NonBlankStr]=Field(min_length=1,max_length=500)

class PathSearch(Contract):
    account_id:NonBlankStr
    hops:int=Field(default=3,ge=1,le=3)
    max_gap_hours:int=Field(default=72,ge=1,le=72)
    limit:int=Field(default=10,ge=1,le=10)

class PolicyLookup(Contract):
    query:NonBlankStr=Field(max_length=4000)
    top_k:int=Field(default=8,ge=1,le=8)
    filters:dict[str,str]=Field(default_factory=dict,max_length=0)
