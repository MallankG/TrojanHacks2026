"""Public v1 evidence exchange contract. Changes to semantics require a new version."""
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class CaseV1(BaseModel):
    id: UUID
    status: str
    disposition: str | None = None


class EvidenceRefV1(BaseModel):
    id: UUID
    evidence_type: str
    transaction_ids: list[UUID]
    account_id: UUID | None = None
    alert_id: UUID | None = None
    risk_signal_id: UUID | None = None


class TransactionV1(BaseModel):
    id: UUID
    occurred_at: datetime
    amount: Decimal
    currency: str
    origin_account_id: UUID
    destination_account_id: UUID
    source_row_id: UUID


class EntityV1(BaseModel):
    id: UUID
    is_internal: bool
    status: str


class AlertV1(BaseModel):
    id: UUID
    typology: str
    rank_score: Decimal | None
    model_run_id: UUID


class ClaimV1(BaseModel):
    id: UUID
    claim_text: str
    claim_kind: Literal['fact']
    verification: Literal['supported']
    referenced_transaction_ids: list[UUID]


class DispositionV1(BaseModel):
    id: UUID
    decision: str
    disposition: str | None
    decided_at: datetime


class GraphPathV1(BaseModel):
    transaction_ids: list[UUID]
    graph_snapshot_id: UUID


class EvidenceBundleV1(BaseModel):
    schema_version: Literal['evidence-bundle-v1']
    as_of: datetime
    tenant_id: UUID
    case_id: UUID
    case: CaseV1
    evidence: list[EvidenceRefV1]
    transactions: list[TransactionV1]
    entities: list[EntityV1]
    alerts: list[AlertV1]
    graph_paths: list[GraphPathV1]
    claims: list[ClaimV1]
    dispositions: list[DispositionV1]
    availability: dict[str, Literal["available", "unavailable", "partial"]]
    caveats: list[str]
    manifest_sha256: str
