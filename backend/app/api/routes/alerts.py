from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import Limit, Offset, page
from app.core.db import get_db, get_one, with_actor
from app.core.errors import NotFound
from app.core.security import ANY_CALLER, CAN_INVESTIGATE, CAN_WRITE_SCORES, CurrentUser
from app.schemas import AlertBatch, AlertUpdate
from app.services import alerts as alert_service
from app.services import graph

router = APIRouter(prefix="/alerts", tags=["alerts"])

ALERT_TXN_SELECT = (
    "role, transactions(id, transaction_ref, occurred_at, amount, currency, amount_usd, channel, description, "
    "origin_account_id, destination_account_id, source_row_id)"
)


@router.post("", status_code=201)
def publish_alerts(body: AlertBatch, user: CurrentUser = Depends(CAN_WRITE_SCORES)) -> dict:
    run = get_one("model_runs", user.tenant_id, str(body.model_run_id), "id, model_kind")
    if not run:
        raise NotFound("Model run")
    if run["model_kind"] != "ranker":
        raise HTTPException(422, "Alerts must be published under a model run with model_kind='ranker'")
    alert_service.validate_batch(user, body)
    created = alert_service.publish(user, body)
    return {"created": len(created), "alerts": created}


def _latest_ranker_run(tenant_id: str) -> str | None:
    rows = (
        get_db().table("model_runs").select("id").eq("tenant_id", tenant_id).eq("model_kind", "ranker")
        .eq("status", "completed").order("completed_at", desc=True).limit(1).execute().data
    )
    return rows[0]["id"] if rows else None


@router.get("")
def alert_queue(
    model_run_id: UUID | None = None,
    all_runs: bool = False,
    status: Literal["new", "under_review", "in_case", "closed_no_action", "dismissed"] | None = None,
    priority_band: Literal["low", "medium", "high", "critical"] | None = None,
    typology: str | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
    user: CurrentUser = Depends(ANY_CALLER),
) -> dict:
    """Ranked queue. Defaults to the latest completed ranker run so re-scoring never duplicates the queue."""
    query = (
        get_db().table("alerts").select("*, model_runs(model_name, model_version, completed_at)", count="exact")
        .eq("tenant_id", user.tenant_id)
    )
    run_id = str(model_run_id) if model_run_id else (None if all_runs else _latest_ranker_run(user.tenant_id))
    if run_id:
        query = query.eq("model_run_id", run_id)
    for column, value in (("status", status), ("priority_band", priority_band), ("typology", typology)):
        if value:
            query = query.eq(column, value)
    result = page(query.order("priority_rank", nullsfirst=False).order("created_at"), limit, offset)
    result["model_run_id"] = run_id
    return result


@router.get("/{alert_id}")
def get_alert(alert_id: UUID, user: CurrentUser = Depends(ANY_CALLER)) -> dict:
    db = get_db()
    alert = get_one("alerts", user.tenant_id, str(alert_id), "*, model_runs(*)")
    if not alert:
        raise NotFound("Alert")
    tid = user.tenant_id
    return {
        **alert,
        "signals": db.table("alert_signals").select("risk_signals(*)").eq("tenant_id", tid).eq("alert_id", str(alert_id)).execute().data,
        "transactions": db.table("alert_transactions").select(ALERT_TXN_SELECT).eq("tenant_id", tid).eq("alert_id", str(alert_id)).execute().data,
        "accounts": db.table("alert_accounts").select("role, accounts(id, account_ref, institution_ref, is_internal, status)")
        .eq("tenant_id", tid).eq("alert_id", str(alert_id)).execute().data,
        "cases": db.table("case_alerts").select("cases(id, case_number, title, status)").eq("tenant_id", tid).eq("alert_id", str(alert_id)).execute().data,
    }


@router.get("/{alert_id}/timeline")
def alert_timeline(alert_id: UUID, user: CurrentUser = Depends(ANY_CALLER)) -> dict:
    if not get_one("alerts", user.tenant_id, str(alert_id), "id"):
        raise NotFound("Alert")
    links = get_db().table("alert_transactions").select(ALERT_TXN_SELECT).eq("tenant_id", user.tenant_id).eq("alert_id", str(alert_id)).execute().data
    events = sorted(({**link["transactions"], "role": link["role"]} for link in links), key=lambda t: t["occurred_at"])
    return {"alert_id": str(alert_id), "events": events}


@router.get("/{alert_id}/graph")
def alert_graph(alert_id: UUID, user: CurrentUser = Depends(ANY_CALLER)) -> dict:
    db = get_db()
    if not get_one("alerts", user.tenant_id, str(alert_id), "id"):
        raise NotFound("Alert")
    links = db.table("alert_transactions").select(ALERT_TXN_SELECT).eq("tenant_id", user.tenant_id).eq("alert_id", str(alert_id)).execute().data
    roles = {
        r["account_id"]: r["role"]
        for r in db.table("alert_accounts").select("account_id, role").eq("tenant_id", user.tenant_id).eq("alert_id", str(alert_id)).execute().data
    }
    edges = [link["transactions"] for link in links]
    for e in edges:
        e.setdefault("transaction_ref", None)
    return graph.graph_payload(user.tenant_id, edges, roles)


@router.patch("/{alert_id}")
def update_alert(alert_id: UUID, body: AlertUpdate, user: CurrentUser = Depends(CAN_INVESTIGATE)) -> dict:
    if body.status == "in_case":
        raise HTTPException(422, "Add the alert to a case instead; status moves to 'in_case' automatically")
    if not get_one("alerts", user.tenant_id, str(alert_id), "id"):
        raise NotFound("Alert")
    # The audit trigger records the status change.
    query = get_db().table("alerts").update({"status": body.status}).eq("tenant_id", user.tenant_id).eq("id", str(alert_id))
    return with_actor(query, user).execute().data[0]
