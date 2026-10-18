"""Governed catalog, evaluation receipts and typed steward queries (PRD 20.2)."""
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from app.core.db import get_db, get_one, with_actor
from app.core.errors import NotFound
from app.core.security import ANY_USER, CAN_STEWARD, CurrentUser
from app.services.governance import Evaluation, Product, StrictModel, demo, evaluate, impact

router = APIRouter(prefix="/governance", tags=["governance"])


class AssessmentRequest(Evaluation):
    purpose: str = Field(min_length=1, max_length=240)


class Review(StrictModel):
    kind: Literal["access_request", "exception", "waiver", "remediation", "mapping", "incident_note", "incident_resolution"]
    note: str = Field(min_length=10, max_length=2000)
    owner: str = Field(min_length=1, max_length=120)
    expires_at: datetime
    decision: Literal["requested", "approved", "rejected"] = "requested"
    request_id: UUID | None = None


class StewardQuery(StrictModel):
    question: str = Field(min_length=1, max_length=300)
    field: str | None = Field(default=None, max_length=120)
    row: int | None = Field(default=None, ge=1, le=500)
    assessment_id: UUID | None = None
    limit: int = Field(default=50, ge=1, le=100)
    execute: bool = False


def product_for(product_id: UUID, user: CurrentUser) -> tuple[dict, Product]:
    row = get_one("governance_products", user.tenant_id, str(product_id))
    if not row:
        raise NotFound("Governed product")
    return row, Product.model_validate(row["definition"])


def event(user, product_id, kind, payload):
    # Mandatory append-only event; failures abort the operation, unlike best-effort audit logging.
    return with_actor(get_db().table("governance_events").insert({
        "tenant_id": user.tenant_id, "product_id": str(product_id), "actor_id": user.id,
        "kind": kind, "payload": payload,
    }), user).execute().data[0]


@router.get("/demo")
def synthetic_demo(scenario: Literal["drift", "healthy"] = "drift"):
    """Public fixed synthetic example. No credentials, tenant data, persistence or arbitrary input."""
    return demo(scenario)


@router.get("/products")
def products(user: CurrentUser = Depends(ANY_USER)):
    return get_db().table("governance_products").select("*").eq("tenant_id", user.tenant_id).order("created_at", desc=True).limit(100).execute().data


@router.post("/products", status_code=201)
def register_product(body: Product, user: CurrentUser = Depends(CAN_STEWARD)):
    return with_actor(get_db().table("governance_products").insert({
        "tenant_id": user.tenant_id, "source_system": body.source_system,
        "definition": body.model_dump(mode="json"), "created_by": user.id,
    }), user).execute().data[0]


@router.get("/products/{product_id}")
def product_detail(product_id: UUID, user: CurrentUser = Depends(ANY_USER)):
    row, _ = product_for(product_id, user)
    events = get_db().table("governance_events").select("*").eq("tenant_id", user.tenant_id).eq("product_id", str(product_id)).order("created_at", desc=True).limit(100).execute().data
    return {"product": row, "events": events, "history_limit": 100}


@router.post("/products/{product_id}/assessments", status_code=201)
def assess(product_id: UUID, body: AssessmentRequest, user: CurrentUser = Depends(CAN_STEWARD)):
    _, product = product_for(product_id, user)
    if body.purpose != product.purpose:
        raise HTTPException(403, "Assessment purpose must match the registered permitted purpose")
    result = evaluate(product, body)
    stored = event(user, product_id, "assessment", result)
    return {"id": stored["id"], "assessment": result}


@router.post("/products/{product_id}/reviews", status_code=201)
def review(product_id: UUID, body: Review, user: CurrentUser = Depends(ANY_USER)):
    product_for(product_id, user)
    now = datetime.now(UTC)
    if body.expires_at.tzinfo is None or not now < body.expires_at <= now + timedelta(days=90):
        raise HTTPException(422, "Review expiry must include timezone and be within the next 90 days")
    if body.decision != "requested" and user.role not in ("admin", "reviewer"):
        raise HTTPException(403, "A human reviewer must approve or reject requests")
    if body.kind != "access_request" and user.role not in ("admin", "reviewer", "data_steward"):
        raise HTTPException(403, "Steward or reviewer role required")
    if body.decision != "requested":
        if body.request_id is None:
            raise HTTPException(422, "Approval or rejection must reference a saved request")
        original = get_one("governance_events", user.tenant_id, str(body.request_id))
        if (not original or original["product_id"] != str(product_id) or original["kind"] != body.kind
                or original["payload"].get("decision") != "requested"):
            raise NotFound("Governance request")
        original_expiry = datetime.fromisoformat(original["payload"]["expires_at"])
        if original_expiry <= now or body.expires_at > original_expiry:
            raise HTTPException(422, "Cannot approve an expired request or extend its expiry")
    elif body.request_id is not None:
        raise HTTPException(422, "New requests must not reference another request")
    return event(user, product_id, body.kind, body.model_dump(mode="json"))


@router.post("/products/{product_id}/steward-query")
def steward_query(product_id: UUID, body: StewardQuery, user: CurrentUser = Depends(ANY_USER)):
    _, product = product_for(product_id, user)
    question = body.question.lower().strip().rstrip("?")
    if question.startswith("which alerts depend on "):
        field = question.removeprefix("which alerts depend on ").strip()
        if body.field and body.field != field:
            raise HTTPException(422, "Field does not match question")
        intent = "field_impact"
    elif question == "why was this row quarantined":
        field = None
        intent = "quarantine_reason"
        if body.row is None or body.assessment_id is None:
            raise HTTPException(422, "Choose a row and assessment first")
    else:
        raise HTTPException(422, "Supported questions: 'which alerts depend on FIELD?' or 'why was this row quarantined?'")
    if field and field not in product.contract.fields:
        raise HTTPException(422, "Field is not in this product contract")
    plan = {"intent": intent, "field": field, "row": body.row, "row_cap": body.limit,
            "tenant_id": user.tenant_id, "product_id": str(product_id), "read_only": True}
    if not body.execute:
        return {"plan": plan, "executed": False}
    if intent == "field_impact":
        found = impact(product, [field])
        items = [n for n in found["nodes"] if n["kind"] == "alert"]
    else:
        assessment = get_one("governance_events", user.tenant_id, str(body.assessment_id))
        if not assessment or assessment["product_id"] != str(product_id) or assessment["kind"] != "assessment":
            raise NotFound("Assessment")
        items = [r for r in assessment["payload"]["rows"] if r["row"] == body.row]
    event(user, product_id, "steward_query", plan)
    return {"plan": plan, "executed": True, "items": items[:body.limit], "truncated": len(items) > body.limit}
