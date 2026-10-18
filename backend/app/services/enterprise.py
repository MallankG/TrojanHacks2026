"""Case-scoped collaboration and ABAC. Tenant authorization is always repeated server-side."""
from datetime import UTC, datetime
import hashlib
import json
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, model_validator

from app.core.audit import request_id_var
from app.core.config import get_settings
from app.core.db import get_db, get_one
from app.core.errors import NotFound
from app.core.security import CurrentUser
from app.services.governance import StrictModel

CLASSIFICATION = {"internal": 0, "confidential": 1, "restricted": 2}


class Attributes(StrictModel):
    region: str = Field(min_length=2, max_length=20)
    clearance: Literal["internal", "confidential", "restricted"]
    purposes: list[str] = Field(min_length=1, max_length=10)


class CasePolicy(StrictModel):
    region: str = Field(min_length=2, max_length=20)
    classification: Literal["internal", "confidential", "restricted"] = "confidential"
    purpose: str = Field(default="investigation", min_length=1, max_length=120)
    review_at: datetime

    @model_validator(mode="after")
    def clock(self):
        if self.review_at.tzinfo is None:
            raise ValueError("review_at requires timezone")
        return self


class Collaborator(StrictModel):
    user_id: UUID
    case_role: Literal["lead", "investigator", "reviewer", "observer"]


class RoomAction(StrictModel):
    client_id: UUID
    expected_revision: int = Field(ge=0)
    kind: Literal["comment", "assignment", "bookmark", "hypothesis", "vote", "review_request", "conflict", "resolution"]
    text: str = Field(default="", max_length=2000)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=20)
    mentions: list[UUID] = Field(default_factory=list, max_length=10)
    target_id: UUID | None = None
    target_user_id: UUID | None = None
    vote: Literal["support", "challenge", "abstain"] | None = None

    @model_validator(mode="after")
    def semantics(self):
        if self.kind in ("comment", "hypothesis", "review_request", "conflict", "resolution") and len(self.text.strip()) < 5:
            raise ValueError("A meaningful note is required")
        if self.kind == "bookmark" and not self.evidence_ids:
            raise ValueError("A bookmark must link case evidence")
        if self.kind in ("vote", "resolution") and self.target_id is None:
            raise ValueError("Choose a hypothesis or conflict")
        if self.kind == "vote" and self.vote is None:
            raise ValueError("Choose a vote")
        if self.kind == "assignment" and self.target_user_id is None:
            raise ValueError("Choose an assignee")
        return self


def check_access(user: CurrentUser, case: dict, attrs: dict | None, policy: dict, member: dict | None,
                 action: str = "read", purpose: str | None = None) -> str:
    if case["tenant_id"] != user.tenant_id or user.is_service or not user.id:
        raise HTTPException(403, "Case access denied")
    if not attrs or attrs.get("region") != policy["region"] or CLASSIFICATION.get(attrs.get("clearance"), -1) < CLASSIFICATION[policy["classification"]]:
        raise HTTPException(403, "Region or classification clearance does not match this case")
    if policy["purpose"] not in attrs.get("purposes", []) or (purpose is not None and purpose != policy["purpose"]):
        raise HTTPException(403, "Case purpose is not permitted")
    case_role = member.get("case_role") if member else "lead" if user.id in (case.get("assigned_to"), case.get("created_by")) else None
    if not case_role:
        raise HTTPException(403, "Case membership required")
    if action in ("manage", "resolve") and (case_role not in ("lead", "reviewer") or user.role not in ("admin", "investigator", "reviewer")):
        raise HTTPException(403, "Case lead or reviewer required")
    if action == "write" and (case_role == "observer" or user.role not in ("admin", "investigator", "analyst", "reviewer")):
        raise HTTPException(403, "Case role is read-only")
    if action == "export":
        if case_role not in ("lead", "reviewer") or user.role not in ("admin", "investigator", "reviewer"):
            raise HTTPException(403, "Case lead or reviewer required for sensitive export")
        if user.aal != "aal2":
            raise HTTPException(403, "Step-up authentication (aal2) required for export")
    return case_role


def access_records(user: CurrentUser, case_id: str):
    case = get_one("cases", user.tenant_id, case_id)
    if not case:
        raise NotFound("Case")
    policies = get_db().table("case_access_policies").select("*").eq("tenant_id", user.tenant_id).eq("case_id", case_id).limit(1).execute().data
    if not policies:
        raise HTTPException(403, "An administrator must configure the case access policy first")
    policy = policies[0]
    attrs = get_one("investigator_attributes", user.tenant_id, user.id or "00000000-0000-0000-0000-000000000000")
    members = get_db().table("case_collaborators").select("*").eq("tenant_id", user.tenant_id).eq("case_id", case_id).eq("user_id", user.id).limit(1).execute().data if user.id else []
    return case, attrs, policy, members[0] if members else None


def require_case(user: CurrentUser, case_id: str, action="read", purpose=None):
    case, attrs, policy, member = access_records(user, case_id)
    role = check_access(user, case, attrs, policy, member, action, purpose)
    return case, policy, role


def canonical_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def room_event(user: CurrentUser, case_id: str, body: RoomAction) -> dict:
    require_case(user, case_id, "resolve" if body.kind in ("assignment", "resolution") else "write")
    db = get_db()
    for mentioned in list(body.mentions) + ([body.target_user_id] if body.target_user_id else []):
        # Verify the recipient's own attributes and membership before recording an @mention.
        profile = get_one("profiles", user.tenant_id, str(mentioned))
        if not profile:
            raise HTTPException(422, "Recipient is not in this tenant")
        require_case(CurrentUser(str(mentioned), user.tenant_id, profile["role"]), case_id)
    for evidence_id in body.evidence_ids:
        evidence = get_one("case_evidence", user.tenant_id, str(evidence_id))
        if not evidence or evidence["case_id"] != case_id:
            raise HTTPException(422, "Evidence does not belong to this case")
    if body.target_id:
        target = get_one("case_room_events", user.tenant_id, str(body.target_id))
        expected = "hypothesis" if body.kind == "vote" else "conflict"
        if not target or target["case_id"] != case_id or target["kind"] != expected:
            raise HTTPException(422, "Target is not a matching case hypothesis/conflict")
    payload = body.model_dump(mode="json", exclude={"client_id", "expected_revision"})
    return db.rpc("append_case_room_event", {"p_tenant": user.tenant_id, "p_case": case_id, "p_actor": user.id,
                  "p_client": str(body.client_id), "p_expected": body.expected_revision, "p_kind": body.kind,
                  "p_payload": payload, "p_hash": canonical_hash(payload), "p_request": request_id_var.get()}).execute().data


def check_egress(destination: str, region: str, classification: str, synthetic: bool):
    settings = get_settings()
    if destination not in settings.approved_data_destinations or region != settings.deployment_region:
        raise HTTPException(403, "Destination or region is outside the approved environment boundary")
    if destination not in ("local", "supabase") and (not synthetic or classification == "restricted"):
        raise HTTPException(403, "External services accept only approved synthetic, non-restricted payloads")
    return {"allowed": True, "destination": destination, "region": region, "synthetic": synthetic,
            "caveat": "Policy check only; no data was transmitted."}


def handoff(case: dict, policy: dict, events: list[dict], claims: list[dict], recipient_id: str, allowed_transactions: set[str]) -> dict:
    # Unverified collaborator text is carried only as open questions; never promoted to facts.
    verified = [{"text": c["claim_text"], "evidence_refs": c.get("referenced_transaction_ids", []), "claim_id": c["id"]}
                for c in claims if c["verification"] == "supported" and c["claim_kind"] == "fact" and c.get("referenced_transaction_ids") and set(c["referenced_transaction_ids"]) <= allowed_transactions]
    open_items = [{"event_id": e["id"], "kind": e["kind"], "note": e["payload"].get("text", ""), "verification": "unverified_collaborator_note"}
                  for e in events if e["kind"] in ("hypothesis", "review_request", "conflict") and not any(r["kind"] == "resolution" and r["payload"].get("target_id") == e["id"] for r in events)]
    return {"schema_version": "handoff-v1", "as_of": datetime.now(UTC).isoformat(), "case_id": case["id"], "recipient_id": recipient_id,
            "verified_developments": verified[:50], "pending_questions": open_items[:50], "review_at": policy.get("review_at"),
            "case_link": f"/cases/{case['id']}", "caveats": ["Only verified, evidence-linked factual claims are included as developments.", "Latest 100 room events and 50 claims; unavailable or unverified facts are omitted."]}
