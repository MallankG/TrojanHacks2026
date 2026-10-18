from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import Limit, Offset, ensure_tenant_ids, page
from app.core import audit
from app.core.config import get_settings
from app.core.db import fetch_by_ids, get_db, get_one, insert_many, to_json, with_actor
from app.core.errors import NotFound
from app.core.security import ANY_CALLER, CAN_INVESTIGATE, CAN_INVESTIGATE_OR_SERVICE, CurrentUser
from app.schemas import CaseAlertsAdd, CaseCreate, CaseUpdate, DecisionIn, EvidenceIn
from app.services import cases as case_service

router = APIRouter(prefix="/cases", tags=["cases"])


def _case_or_404(user: CurrentUser, case_id: UUID, columns: str = "*") -> dict:
    case = get_one("cases", user.tenant_id, str(case_id), columns)
    if not case:
        raise NotFound("Case")
    return case


def _check_assignee(user: CurrentUser, assignee: UUID | None) -> None:
    if assignee and not get_db().table("profiles").select("id").eq("id", str(assignee)).eq("tenant_id", user.tenant_id).execute().data:
        raise HTTPException(422, "assigned_to must be a user in your tenant")


@router.post("", status_code=201)
def create_case(body: CaseCreate, user: CurrentUser = Depends(CAN_INVESTIGATE)) -> dict:
    _check_assignee(user, body.assigned_to)
    ensure_tenant_ids("alerts", user.tenant_id, body.alert_ids, "alert ids")
    due_at = datetime.now(UTC) + timedelta(hours=get_settings().case_review_sla_hours)
    row = {"tenant_id": user.tenant_id, "title": body.title, "summary": body.summary, "priority_band": body.priority_band,
           "assigned_to": body.assigned_to or user.id, "created_by": user.id, "due_at": due_at}
    case = with_actor(get_db().table("cases").insert(to_json(row)), user).execute().data[0]
    evidence = case_service.attach_alerts(user, case["id"], [str(a) for a in body.alert_ids])
    return {**case, "evidence_added": len(evidence)}


@router.get("")
def list_cases(
    status: Literal["open", "in_review", "pending_review", "escalated", "closed"] | None = None,
    assigned_to_me: bool = False,
    limit: Limit = 50,
    offset: Offset = 0,
    user: CurrentUser = Depends(ANY_CALLER),
) -> dict:
    query = (
        get_db().table("cases").select("*, assignee:profiles!cases_assigned_to_fkey(id, display_name, role)", count="exact")
        .eq("tenant_id", user.tenant_id).order("updated_at", desc=True)
    )
    if status:
        query = query.eq("status", status)
    if assigned_to_me and user.id:
        query = query.eq("assigned_to", user.id)
    return page(query, limit, offset)


@router.get("/{case_id}")
def get_case(case_id: UUID, user: CurrentUser = Depends(ANY_CALLER)) -> dict:
    db = get_db()
    tid, cid = user.tenant_id, str(case_id)
    case = _case_or_404(user, case_id, "*, assignee:profiles!cases_assigned_to_fkey(id, display_name, role)")
    detail = {
        **case,
        "alerts": db.table("case_alerts").select("added_at, alerts(*)").eq("tenant_id", tid).eq("case_id", cid).execute().data,
        "evidence": db.table("case_evidence").select("*").eq("tenant_id", tid).eq("case_id", cid).order("created_at").execute().data,
        "narratives": db.table("case_narratives").select("id, version, author_type, verification_status, created_at, llm_model")
        .eq("tenant_id", tid).eq("case_id", cid).order("version", desc=True).execute().data,
        "decisions": db.table("case_decisions").select("*, decided_by_profile:profiles!case_decisions_decided_by_fkey(display_name, role)")
        .eq("tenant_id", tid).eq("case_id", cid).order("decided_at", desc=True).execute().data,
        "agent_runs": db.table("agent_runs").select("id, graph_name, status, current_step, created_at, completed_at")
        .eq("tenant_id", tid).eq("case_id", cid).order("created_at", desc=True).execute().data,
    }
    audit.record(user, "case.evidence_viewed", "case", cid)
    return detail


@router.patch("/{case_id}")
def update_case(case_id: UUID, body: CaseUpdate, user: CurrentUser = Depends(CAN_INVESTIGATE)) -> dict:
    case = _case_or_404(user, case_id, "id, status")
    if case["status"] == "closed":
        raise HTTPException(409, "Case is closed")
    changes = body.model_dump(exclude_unset=True)
    if "assigned_to" in changes:
        _check_assignee(user, body.assigned_to)
    if not changes:
        raise HTTPException(422, "Nothing to update")
    if "due_at" in changes and changes["due_at"] is not None:
        changes["due_at"] = changes["due_at"].astimezone(UTC)
    updated = case_service.update_case(user, str(case_id), changes)
    # Status and owner changes are audited by a database trigger; record the other fields here.
    other = {k: v for k, v in changes.items() if k not in ("status", "assigned_to")}
    if other:
        audit.record(user, "case.updated", "case", str(case_id), {"fields": sorted(other), "due_at": changes.get("due_at")})
    return updated


@router.post("/{case_id}/alerts", status_code=201)
def add_alerts(case_id: UUID, body: CaseAlertsAdd, user: CurrentUser = Depends(CAN_INVESTIGATE)) -> dict:
    _case_or_404(user, case_id, "id")
    ensure_tenant_ids("alerts", user.tenant_id, body.alert_ids, "alert ids")
    evidence = case_service.attach_alerts(user, str(case_id), [str(a) for a in body.alert_ids])
    return {"evidence_added": len(evidence)}


@router.post("/{case_id}/evidence", status_code=201)
def add_evidence(case_id: UUID, body: EvidenceIn, user: CurrentUser = Depends(CAN_INVESTIGATE_OR_SERVICE)) -> dict:
    _case_or_404(user, case_id, "id")
    ensure_tenant_ids("transactions", user.tenant_id, body.transaction_ids, "transaction ids")
    if body.policy_chunk_id:
        chunk = get_db().table("policy_chunks").select("id, tenant_id").eq("id", str(body.policy_chunk_id)).execute().data
        if not chunk or chunk[0]["tenant_id"] not in (None, user.tenant_id):
            raise HTTPException(422, "Unknown policy chunk")
    row = {**body.model_dump(), "tenant_id": user.tenant_id, "case_id": str(case_id), "added_by": user.id,
           "added_by_type": user.actor_type}
    evidence = insert_many("case_evidence", [row], actor=user)[0]
    audit.record(user, "case.evidence_added", "case", str(case_id),
                 {"evidence_id": evidence["id"], "evidence_type": body.evidence_type, "kind": body.payload.get("kind")})
    return evidence


@router.get("/{case_id}/evidence-bundle")
def evidence_bundle(case_id: UUID, user: CurrentUser = Depends(ANY_CALLER)) -> dict:
    """Structured, id-stable evidence for the agent service (narrative drafting and verification)."""
    _case_or_404(user, case_id, "id")
    audit.record(user, "case.evidence_bundle_accessed", "case", str(case_id))
    return case_service.evidence_bundle(user.tenant_id, str(case_id))


@router.post("/{case_id}/decisions", status_code=201)
def record_decision(case_id: UUID, body: DecisionIn, user: CurrentUser = Depends(CAN_INVESTIGATE)) -> dict:
    """Human review step. The database enforces that only reviewers/admins accept, reject, escalate or dispose."""
    case = _case_or_404(user, case_id, "id, status, escalation_requested_by")
    if case["status"] == "closed":
        raise HTTPException(409, "Case is already closed")
    case_service.check_four_eyes(case, user, body.decision, body.disposition)
    if body.decision != "edit" and user.role not in ("reviewer", "admin"):
        raise HTTPException(403, "Only reviewers can accept, reject or escalate")
    if body.disposition and user.role not in ("reviewer", "admin"):
        raise HTTPException(403, "Only reviewers can record a disposition")
    if body.narrative_id and not fetch_by_ids("case_narratives", user.tenant_id, [body.narrative_id], "id"):
        raise HTTPException(422, "Unknown narrative")
    row = {**body.model_dump(), "tenant_id": user.tenant_id, "case_id": str(case_id), "decided_by": user.id}
    decision = insert_many("case_decisions", [row], actor=user)[0]
    updated = get_one("cases", user.tenant_id, str(case_id), "id, status, disposition, disposed_by, disposed_at")
    return {"decision": decision, "case": updated}


@router.get("/{case_id}/decisions")
def list_decisions(case_id: UUID, user: CurrentUser = Depends(ANY_CALLER)) -> list[dict]:
    _case_or_404(user, case_id, "id")
    return (get_db().table("case_decisions").select("*").eq("tenant_id", user.tenant_id).eq("case_id", str(case_id))
            .order("decided_at", desc=True).execute().data)


@router.get("/{case_id}/export-url")
def export_url(case_id: UUID, user: CurrentUser = Depends(CAN_INVESTIGATE)) -> dict:
    """Writes the evidence bundle to the private case-exports bucket and returns a short-lived signed URL."""
    import hashlib
    import json

    _case_or_404(user, case_id, "id")
    bundle = case_service.evidence_bundle(user.tenant_id, str(case_id))
    path = f"{user.tenant_id}/cases/{case_id}/{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    data = json.dumps(bundle, indent=2).encode()
    digest = hashlib.sha256(data).hexdigest()
    storage = get_db().storage.from_(get_settings().case_exports_bucket)
    storage.upload(path, data, {"content-type": "application/json"})
    signed = storage.create_signed_url(path, 300)
    audit.record(user, "case.exported", "case", str(case_id), {"path": path, "sha256": digest, "format": "evidence-bundle-json"})
    return {"path": path, "url": signed.get("signedURL") or signed.get("signedUrl"), "expires_in": 300, "sha256": digest}
