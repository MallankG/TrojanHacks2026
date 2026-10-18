from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import Limit, Offset, page
from app.core.db import get_db, get_one, with_actor
from app.core.errors import NotFound
from app.core.security import ANY_USER, CAN_STEWARD, CurrentUser
from app.schemas import DQIssueUpdate

router = APIRouter(prefix="/data-quality", tags=["data quality"])


@router.get("/issues")
def list_issues(
    status: Literal["open", "acknowledged", "remediated", "waived"] | None = None,
    severity: Literal["info", "warning", "error", "critical"] | None = None,
    issue_type: str | None = None,
    run_id: str | None = None,
    limit: Limit = 100,
    offset: Offset = 0,
    user: CurrentUser = Depends(ANY_USER),
) -> dict:
    query = (
        get_db().table("data_quality_issues").select("*, source_rows(row_number, status)", count="exact")
        .eq("tenant_id", user.tenant_id).order("created_at", desc=True).order("id")
    )
    for column, value in (("status", status), ("severity", severity), ("issue_type", issue_type), ("run_id", run_id)):
        if value:
            query = query.eq(column, value)
    return page(query, limit, offset)


@router.patch("/issues/{issue_id}")
def update_issue(issue_id: UUID, body: DQIssueUpdate, user: CurrentUser = Depends(CAN_STEWARD)) -> dict:
    if not get_one("data_quality_issues", user.tenant_id, str(issue_id), "id"):
        raise NotFound("Data-quality issue")
    resolved = body.status in ("remediated", "waived")
    changes = {
        "status": body.status,
        "remediation_note": body.remediation_note,
        "resolved_by": user.id if resolved else None,
        "resolved_at": datetime.now(UTC).isoformat() if resolved else None,
    }
    # The audit trigger records the status change.
    query = get_db().table("data_quality_issues").update(changes).eq("tenant_id", user.tenant_id).eq("id", str(issue_id))
    return with_actor(query, user).execute().data[0]


@router.get("/source-rows/{row_id}")
def source_row_detail(row_id: UUID, user: CurrentUser = Depends(ANY_USER)) -> dict:
    """A source row with its validation results, its canonical transaction and anything downstream of it."""
    db = get_db()
    row = get_one("source_rows", user.tenant_id, str(row_id), "*, ingestion_runs(id, field_mapping, schema_version, source_files(*))")
    if not row:
        raise NotFound("Source row")
    issues = db.table("data_quality_issues").select("*").eq("tenant_id", user.tenant_id).eq("source_row_id", str(row_id)).execute().data
    txns = db.table("transactions").select("*").eq("tenant_id", user.tenant_id).eq("source_row_id", str(row_id)).execute().data
    downstream: dict = {"features": [], "risk_signals": [], "alerts": []}
    if txns:
        txn_id = txns[0]["id"]
        downstream["features"] = (
            db.table("features").select("id, feature_set_version, as_of_timestamp").eq("tenant_id", user.tenant_id)
            .eq("transaction_id", txn_id).execute().data
        )
        downstream["risk_signals"] = (
            db.table("risk_signals").select("id, signal_name, typology, score, triggered, model_run_id")
            .eq("tenant_id", user.tenant_id).or_(f"transaction_id.eq.{txn_id},evidence_transaction_ids.cs.{{{txn_id}}}")
            .execute().data
        )
        downstream["alerts"] = (
            db.table("alert_transactions").select("role, alerts(id, alert_number, typology, priority_band, status)")
            .eq("tenant_id", user.tenant_id).eq("transaction_id", txn_id).execute().data
        )
    return {"source_row": row, "issues": issues, "transaction": txns[0] if txns else None, "downstream": downstream}
