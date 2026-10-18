"""Protocols for teammates' code. The signatures are the contract: change them only after telling
the owner. The backend passes real implementations to `api.configure(Services(...))`; any
service left as None falls back to the mock in `tracelayer_agents.mocks`.

All methods are synchronous for now. Nodes call them with `asyncio.to_thread`, so a
blocking implementation (pandas, torch, a sync DB driver) never stalls the event loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from tracelayer_agents.schemas import (
    Alert,
    AuditEvent,
    CaseRecord,
    DetectorOutput,
    EvidenceBundle,
    EvidenceTransaction,
    FlowFindings,
    FusionResult,
    GraphSnapshot,
    IngestionResult,
    NarrativeDraft,
    QualityReport,
    RetrievedChunk,
    SubgraphJSON,
)

# --------------------------------------------------------------------------- backend


@runtime_checkable
class IngestionService(Protocol):
    def ingest(self, payload: bytes | str, source_asset_id: str) -> IngestionResult:
        """Load one synthetic source file into the canonical schema.

        Inputs: `payload` is raw file bytes or a storage path/URI; `source_asset_id` names the
        governed source asset (used to look up its contract, owner and steward).
        Output: an `IngestionResult` with a new `ingestion_run_id` and `transaction_batch_id`.
        Invariants: idempotent per payload checksum (re-ingesting the same file returns the same
        batch); rejected rows are counted, not silently dropped; source values are kept
        alongside normalized ones (PRD §13).
        """
        ...


@runtime_checkable
class QualityService(Protocol):
    def run_contract(self, batch_id: str) -> QualityReport:
        """Run the source asset's data contract against a batch.

        Input: a `transaction_batch_id` from `IngestionService.ingest`.
        Output: a `QualityReport` whose `status` is `fail` if any issue is `critical`,
        `qualified` if there are only warnings, else `pass`.
        Invariants: read-only on the batch; every issue names a real `rule_id` and owner/steward
        role; `sample_record_ids` are real source row refs. Quality issues are governance
        findings and must never be emitted as risk signals.
        """
        ...


@runtime_checkable
class GraphService(Protocol):
    def build_snapshot(self, batch_id: str) -> GraphSnapshot:
        """Build the account/transaction graph and derived features for a batch.

        Output: a `GraphSnapshot` with a stable `graph_snapshot_id` and `feature_schema_version`.
        Invariants: time-respecting construction (features use only edges at or before each
        transaction's timestamp); the same batch and feature version give the same snapshot ID.
        """
        ...

    def subgraph(self, tx_ids: list[str], hops: int) -> SubgraphJSON:
        """Return the neighbourhood around the given transactions.

        Inputs: transaction IDs from the snapshot; `hops` is 1–3 (larger values are rejected).
        Output: nodes are accounts, edges are transactions with real IDs, amounts and timestamps.
        Invariants: bounded query only; no generated SQL/Cypher.
        """
        ...

    def get_transactions(self, tx_ids: list[str]) -> list[EvidenceTransaction]:
        """Full canonical rows (PRD §13 `transactions`) for the given IDs, for evidence bundles.

        Output: one `EvidenceTransaction` per requested ID, in request order, with the real
        `source_row_ref` so evidence links back to the source file.
        Invariants: raises `KeyError` if any ID is unknown (never returns partial evidence);
        read-only; no generated SQL/Cypher.
        """
        ...


# --------------------------------------------------------------------------- ML


@runtime_checkable
class GraphRiskScorer(Protocol):
    def score(self, snapshot_id: str) -> DetectorOutput:
        """GraphSAGE (or similar) transaction risk scores for a snapshot.

        Output: one `DetectorOutput`; `scores` keys are actual transaction IDs in the snapshot,
        higher = riskier; `model_version` and `feature_snapshot_id` identify the artifact.
        Invariants: must not use scenario/typology labels (AMLSim `is_sar`, alert IDs, typology
        names) as features; no future-neighbour leakage. If the model isn't available, return
        `status="unavailable"` with empty scores rather than raising or faking scores.
        """
        ...


@runtime_checkable
class BehaviorScorer(Protocol):
    def score(self, snapshot_id: str) -> list[DetectorOutput]:
        """Tabular and rule detectors: XGBoost, IsolationForest, typology rules.

        Output: one `DetectorOutput` per detector, each with a unique `detector_name`.
        Typology rules put their tags in `typology_flags`; IsolationForest scores are a ranking
        signal, not a probability.
        Invariants: must not use scenario/typology labels as features; trained on the train
        split only; every key is an actual transaction ID in the snapshot.
        """
        ...


@runtime_checkable
class RankFusion(Protocol):
    def fuse(self, outputs: dict[str, DetectorOutput]) -> FusionResult:
        """Combine detector scores into one ranking.

        Input: detector outputs keyed by `detector_name` (unavailable detectors may be present
        with `status="unavailable"` and must be skipped).
        Output: `composite` keyed by transaction ID; `method` and fitted `coefficients` are
        recorded.
        Invariants: fitted on the validation split, never the test split; no hand-invented
        weights; does not drop or rewrite individual detector scores.
        """
        ...


@runtime_checkable
class FlowInvestigator(Protocol):
    def investigate(self, alert: Alert, snapshot_id: str) -> FlowFindings:
        """Find paths, cycles, fan-in/out and pass-through patterns around an alert.

        Output: `FlowFindings` whose patterns cite only IDs in `evidence_tx_ids`.
        Invariants: must return actual transaction IDs from the snapshot; paths respect time
        order (each hop happens after the previous one); bounded search depth; no generated
        SQL/Cypher.
        """
        ...


# --------------------------------------------------------------------------- RAG


@runtime_checkable
class PolicyRetriever(Protocol):
    def search(self, query: str, filters: dict[str, Any], top_k: int) -> list[RetrievedChunk]:
        """Retrieve curated policy passages (FFIEC manual, FinCEN guidance).

        Inputs: free-text query; `filters` with keys agreed in CONTRACTS.md (empty = no filter);
        `top_k` >= 1.
        Output: at most `top_k` chunks, sorted by descending `score`; an empty list is allowed.
        Invariants: only the curated corpus (never transaction evidence); `excerpt` is verbatim
        source text; `chunk_id` is stable across re-indexing; retrieval only, no generated
        answers.
        """
        ...


# --------------------------------------------------------------------------- persistence


@runtime_checkable
class CaseRepository(Protocol):
    """Alerts, evidence, cases and drafts. Backend implements this on Supabase Postgres; agents ship
    an in-memory version."""

    def save_alerts(self, alerts: list[Alert]) -> None:
        """Insert or replace alerts by `alert_id`."""
        ...

    def get_alert(self, alert_id: str) -> Alert | None: ...

    def save_evidence(self, bundle: EvidenceBundle) -> None:
        """Store the evidence bundle for `bundle.alert_id`, keeping `bundle_hash` as-is."""
        ...

    def get_evidence(self, alert_id: str) -> EvidenceBundle | None: ...

    def create_case(self, case: CaseRecord) -> CaseRecord:
        """Insert a new case; raises if `case_id` already exists."""
        ...

    def get_case(self, case_id: str) -> CaseRecord | None: ...

    def update_case(self, case: CaseRecord) -> CaseRecord:
        """Replace the case row; the caller writes a matching audit event."""
        ...

    def save_draft(self, case_id: str, draft: NarrativeDraft) -> None:
        """Store a draft version; earlier drafts are kept, not overwritten."""
        ...


@runtime_checkable
class AuditRepository(Protocol):
    """Append-only store for hash-chained audit events."""

    def append(self, event: AuditEvent) -> AuditEvent:
        """Store an event that already has `prev_hash` and `hash` set (agents compute them).

        Invariants: insert only, never update or delete; reject the event if `prev_hash` is not
        the current `last_hash()` (so concurrent writers can't fork the chain).
        """
        ...

    def last_hash(self) -> str | None:
        """Hash of the most recent event, or None if the chain is empty."""
        ...

    def list_events(self, object_id: str | None = None) -> list[AuditEvent]:
        """Events in insertion order, optionally filtered to one object."""
        ...


# --------------------------------------------------------------------------- bundle


@dataclass
class Services:
    """Implementations the backend passes to `api.configure`. None means use the mock."""

    ingestion: IngestionService | None = None
    quality: QualityService | None = None
    graph: GraphService | None = None
    graph_risk: GraphRiskScorer | None = None
    behavior: BehaviorScorer | None = None
    fusion: RankFusion | None = None
    flows: FlowInvestigator | None = None
    policy: PolicyRetriever | None = None
    cases: CaseRepository | None = None
    audit: AuditRepository | None = None
