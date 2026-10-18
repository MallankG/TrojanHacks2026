"""Request bodies. Enum values mirror the Postgres enums in supabase/migrations."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, model_validator

Typology = Literal[
    "rapid_in_out", "layering", "mule_network", "funnel", "fan_out", "cycle", "structuring",
    "dormant_reactivation", "behavioral_deviation", "scam_proceeds", "model_anomaly", "other",
]
PriorityBand = Literal["low", "medium", "high", "critical"]
AlertStatus = Literal["new", "under_review", "in_case", "closed_no_action", "dismissed"]
CaseStatus = Literal["open", "in_review", "pending_review", "escalated", "closed"]
Disposition = Literal[
    "no_further_action", "false_positive", "monitor", "needs_more_information", "escalate_for_sar_consideration"
]
UserRole = Literal["admin", "investigator", "analyst", "reviewer", "data_steward"]


class Body(BaseModel):
    model_config = {"extra": "forbid"}


# --- Data quality -----------------------------------------------------------------------------
class DQIssueUpdate(Body):
    status: Literal["open", "acknowledged", "remediated", "waived"]
    remediation_note: str | None = Field(default=None, max_length=2000)


# --- Model runs / features / signals (written by the model-training worker) ---------------------
class ModelRunCreate(Body):
    model_name: str = Field(max_length=200)
    model_kind: Literal["rule_engine", "supervised", "anomaly", "graph_model", "ranker"]
    model_version: str = Field(max_length=100)
    dataset_version: str = Field(max_length=200)
    feature_set_version: str | None = None
    code_version: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    split_config: dict[str, Any] = Field(default_factory=dict)
    random_seed: int | None = None


class ModelRunUpdate(Body):
    status: Literal["running", "completed", "failed"] | None = None
    metrics: dict[str, Any] | None = None
    metrics_are_demonstration: bool | None = None
    artifact_path: str | None = Field(default=None, description="Path inside the model-artifacts bucket")
    error_message: str | None = None


class FeatureIn(Body):
    entity_type: Literal["transaction", "account"]
    transaction_id: UUID | None = None
    account_id: UUID | None = None
    feature_set_version: str
    as_of_timestamp: datetime
    features: dict[str, Any]

    @model_validator(mode="after")
    def check_entity(self):
        if (self.entity_type == "transaction") != (self.transaction_id is not None) or (
            self.entity_type == "account") != (self.account_id is not None):
            raise ValueError("Set exactly the id matching entity_type")
        return self


class FeatureBatch(Body):
    features: list[FeatureIn] = Field(max_length=5000)


class SignalIn(Body):
    client_ref: str | None = Field(default=None, description="Caller-chosen key, echoed back to map signal ids")
    source: Literal["rule", "supervised_model", "anomaly_model", "graph_model"]
    signal_name: str
    signal_version: str
    typology: Typology | None = None
    transaction_id: UUID | None = None
    account_id: UUID | None = None
    feature_id: UUID | None = None
    score: float | None = None
    threshold: float | None = None
    triggered: bool
    window_start: datetime | None = None
    window_end: datetime | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    evidence_transaction_ids: list[UUID] = Field(default_factory=list)


class SignalBatch(Body):
    signals: list[SignalIn] = Field(max_length=5000)


# --- Alerts (written by the ranking worker) -----------------------------------------------------
class AlertTransactionLink(Body):
    transaction_id: UUID
    role: Literal["trigger", "supporting", "context"] = "supporting"


class AlertAccountLink(Body):
    account_id: UUID
    role: Literal["subject", "counterparty", "intermediary"] = "counterparty"


class AlertIn(Body):
    scope: Literal["transaction", "account", "network"]
    primary_account_id: UUID | None = None
    primary_transaction_id: UUID | None = None
    typology: Typology
    priority_band: PriorityBand
    priority_rank: int | None = Field(default=None, gt=0)
    rank_score: float | None = None
    ranking_strategy: str
    component_signals: list[dict[str, Any]] = Field(default_factory=list)
    window_start: datetime | None = None
    window_end: datetime | None = None
    total_amount: Decimal | None = Field(default=None, ge=0)
    amount_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    limitations: str | None = None
    explanation: dict[str, Any] = Field(default_factory=dict)
    signal_ids: list[UUID] = Field(default_factory=list)
    transactions: list[AlertTransactionLink] = Field(default_factory=list)
    accounts: list[AlertAccountLink] = Field(default_factory=list)


class AlertBatch(Body):
    model_run_id: UUID = Field(description="The ranker model run that produced these alerts")
    alerts: list[AlertIn] = Field(max_length=2000)


class AlertUpdate(Body):
    status: AlertStatus


# --- Cases ---------------------------------------------------------------------------------------
class CaseCreate(Body):
    title: str = Field(max_length=300)
    summary: str | None = None
    priority_band: PriorityBand = "medium"
    alert_ids: list[UUID] = Field(default_factory=list)
    assigned_to: UUID | None = None


class CaseUpdate(Body):
    title: str | None = Field(default=None, max_length=300)
    summary: str | None = None
    status: Literal["open", "in_review", "pending_review"] | None = None
    priority_band: PriorityBand | None = None
    assigned_to: UUID | None = None
    # Internal review SLA (a demo setting, not a legal deadline). Must carry a timezone; stored in UTC.
    due_at: AwareDatetime | None = None


class EscalationRequestIn(Body):
    reason: str = Field(min_length=1, max_length=4000)


class CaseAlertsAdd(Body):
    alert_ids: list[UUID] = Field(min_length=1)


class EvidenceIn(Body):
    evidence_type: Literal[
        "transaction", "transaction_path", "account", "alert", "risk_signal", "data_quality_issue", "policy_citation",
        "analyst_note",
    ]
    explanation: str = Field(max_length=4000)
    source_ref: str | None = None
    transaction_ids: list[UUID] = Field(default_factory=list)
    alert_id: UUID | None = None
    account_id: UUID | None = None
    risk_signal_id: UUID | None = None
    dq_issue_id: UUID | None = None
    policy_chunk_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class DecisionIn(Body):
    decision: Literal["accept", "edit", "reject", "escalate"]
    disposition: Disposition | None = None
    narrative_id: UUID | None = None
    notes: str | None = Field(default=None, max_length=4000)


# --- Agent runs and narratives (written by the agent service) -------------------------------------
class AgentRunCreate(Body):
    graph_name: str
    graph_version: str | None = None
    alert_id: UUID | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    embedding_model: str | None = None
    prompt_version: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class AgentStep(Body):
    name: str
    status: Literal["started", "completed", "failed", "skipped"]
    detail: dict[str, Any] = Field(default_factory=dict)
    at: datetime | None = None


class AgentRunUpdate(Body):
    status: Literal["queued", "running", "awaiting_review", "completed", "failed"] | None = None
    current_step: str | None = None
    append_steps: list[AgentStep] = Field(default_factory=list)
    error_message: str | None = None


class ClaimIn(Body):
    claim_text: str
    claim_kind: Literal["fact", "hypothesis", "unknown"]
    referenced_transaction_ids: list[UUID] = Field(default_factory=list)
    referenced_account_ids: list[UUID] = Field(default_factory=list)
    referenced_values: dict[str, Any] = Field(default_factory=dict)
    cited_chunk_ids: list[UUID] = Field(default_factory=list)
    verification: Literal["pending", "supported", "unsupported", "unverifiable", "not_applicable"] = "pending"
    verifier_notes: str | None = None


class CitationIn(Body):
    policy_chunk_id: UUID
    quoted_text: str | None = None


class NarrativeCreate(Body):
    content: str
    parent_narrative_id: UUID | None = None
    agent_run_id: UUID | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    prompt_version: str | None = None
    claims: list[ClaimIn] = Field(default_factory=list)
    citations: list[CitationIn] = Field(default_factory=list)


class ClaimVerification(Body):
    claim_index: int
    verification: Literal["supported", "unsupported", "unverifiable", "not_applicable"]
    verifier_notes: str | None = None


class NarrativeVerification(Body):
    verification_status: Literal["passed", "flagged", "failed"]
    verification_report: dict[str, Any] = Field(default_factory=dict)
    claims: list[ClaimVerification] = Field(default_factory=list)


# --- Policy corpus (RAG storage) -------------------------------------------------------------------
class ChunkIn(Body):
    chunk_index: int = Field(ge=0)
    content: str
    section: str | None = None
    token_count: int | None = None
    embedding: list[float] | None = None


class PolicyDocumentCreate(Body):
    title: str
    publisher: str
    source_url: str = Field(pattern=r"^https://")
    document_type: str | None = None
    published_date: str | None = None
    effective_date: str | None = None
    retrieved_at: datetime
    shared: bool = Field(default=False, description="True = public guidance for all tenants (platform workers only)")
    embedding_model: str | None = None
    chunks: list[ChunkIn] = Field(min_length=1, max_length=2000)


class PolicySearch(Body):
    query_text: str | None = None
    query_embedding: list[float] | None = None
    match_count: int = Field(default=8, ge=1, le=30)

    @model_validator(mode="after")
    def need_query(self):
        if not self.query_text and not self.query_embedding:
            raise ValueError("Provide query_text and/or query_embedding")
        return self


# --- Admin ---------------------------------------------------------------------------------------------
class UserCreate(Body):
    email: str
    password: str = Field(min_length=8)
    role: UserRole
    display_name: str | None = None


class UserUpdate(Body):
    role: UserRole | None = None
    display_name: str | None = None
