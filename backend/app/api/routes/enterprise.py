"""PRD 20.10: case collaboration, versioned evidence, operational health and boundaries."""
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from app.core import audit
from app.core.config import get_settings
from app.core.db import get_db, get_one, to_json, with_actor
from app.core.errors import NotFound
from app.core.security import ADMIN_ONLY, ANY_USER, CAN_WRITE_SCORES, CurrentUser
from app.schemas_enterprise import EvidenceBundleV1
from app.services import enterprise as service
from app.services.governance import StrictModel
from app.services.monitoring import DriftCheck, ServiceMetric, drift_report, slo_report


def enabled():
    if not get_settings().enterprise_enabled:
        raise HTTPException(404, "Enterprise workspace is disabled")


router = APIRouter(prefix="/enterprise", tags=["enterprise workspace"], dependencies=[Depends(enabled)])


def step_up(user):
    if user.aal != "aal2":
        raise HTTPException(403, "Step-up authentication (aal2) required")


@router.put("/users/{user_id}/attributes")
def attributes(user_id: UUID, body: service.Attributes, user: CurrentUser = Depends(ADMIN_ONLY)):
    step_up(user)
    if not get_one("profiles", user.tenant_id, str(user_id)):
        raise NotFound("Tenant user")
    return with_actor(get_db().table("investigator_attributes").upsert({"id": str(user_id), "tenant_id": user.tenant_id,
                      **body.model_dump(), "updated_at": datetime.now(UTC).isoformat()}), user).execute().data[0]


@router.put("/cases/{case_id}/policy")
def policy(case_id: UUID, body: service.CasePolicy, user: CurrentUser = Depends(ADMIN_ONLY)):
    step_up(user)
    if not get_one("cases", user.tenant_id, str(case_id)):
        raise NotFound("Case")
    return with_actor(get_db().table("case_access_policies").upsert({"tenant_id": user.tenant_id, "case_id": str(case_id),
                      **body.model_dump(mode="json"), "updated_by": user.id, "updated_at": datetime.now(UTC).isoformat()}), user).execute().data[0]


@router.put("/cases/{case_id}/collaborators")
def add_collaborator(case_id: UUID, body: service.Collaborator, user: CurrentUser = Depends(ANY_USER)):
    service.require_case(user, str(case_id), "manage")
    step_up(user)
    profile = get_one("profiles", user.tenant_id, str(body.user_id))
    if not profile:
        raise NotFound("Tenant user")
    case, attrs, policy, _ = service.access_records(service.CurrentUser(str(body.user_id), user.tenant_id, profile["role"]), str(case_id))
    service.check_access(service.CurrentUser(str(body.user_id), user.tenant_id, profile["role"]), case, attrs, policy, {"case_role": body.case_role})
    return with_actor(get_db().table("case_collaborators").upsert({"tenant_id": user.tenant_id, "case_id": str(case_id),
                      **body.model_dump(mode="json"), "created_by": user.id, "updated_at": datetime.now(UTC).isoformat()}, on_conflict="case_id,user_id"), user).execute().data[0]


@router.get("/cases/{case_id}/room")
def room(case_id: UUID, user: CurrentUser = Depends(ANY_USER)):
    case, policy, role = service.require_case(user, str(case_id))
    events = get_db().table("case_room_events").select("*").eq("tenant_id", user.tenant_id).eq("case_id", str(case_id)).order("revision", desc=True).limit(100).execute().data
    members = get_db().table("case_collaborators").select("user_id,case_role").eq("tenant_id", user.tenant_id).eq("case_id", str(case_id)).limit(100).execute().data
    evidence = get_db().table("case_evidence").select("id,evidence_type,explanation").eq("tenant_id", user.tenant_id).eq("case_id", str(case_id)).limit(100).execute().data
    audit.record(user, "collaboration.viewed", "case", str(case_id))
    return {"schema_version": "room-v1", "case": {"id": case["id"], "title": case["title"]}, "policy": policy, "case_role": role,
            "members": members, "evidence": evidence, "events": events, "revision": events[0]["revision"] if events else 0, "limit": 100}


@router.post("/cases/{case_id}/room/events", status_code=201)
def room_action(case_id: UUID, body: service.RoomAction, user: CurrentUser = Depends(ANY_USER)):
    return service.room_event(user, str(case_id), body)


class HandoffRequest(StrictModel):
    recipient_id: UUID


@router.post("/cases/{case_id}/handoff")
def briefing(case_id: UUID, body: HandoffRequest, user: CurrentUser = Depends(ANY_USER)):
    case, policy, _ = service.require_case(user, str(case_id))
    recipient = get_one("profiles", user.tenant_id, str(body.recipient_id))
    if not recipient:
        raise NotFound("Recipient")
    service.require_case(CurrentUser(str(body.recipient_id), user.tenant_id, recipient["role"]), str(case_id))
    events = get_db().table("case_room_events").select("*").eq("tenant_id", user.tenant_id).eq("case_id", str(case_id)).order("revision", desc=True).limit(100).execute().data
    narratives = get_db().table("case_narratives").select("id").eq("tenant_id", user.tenant_id).eq("case_id", str(case_id)).order("version", desc=True).limit(1).execute().data
    claims = get_db().table("narrative_claims").select("*").eq("tenant_id", user.tenant_id).eq("narrative_id", narratives[0]["id"]).order("claim_index").limit(50).execute().data if narratives else []
    evidence = get_db().table("case_evidence").select("transaction_ids").eq("tenant_id", user.tenant_id).eq("case_id", str(case_id)).limit(501).execute().data
    if len(evidence) > 500:
        raise HTTPException(422, "Narrow the case before generating a handoff")
    allowed = {t for e in evidence for t in (e.get("transaction_ids") or [])}
    result = service.handoff(case, policy, events, claims, str(body.recipient_id), allowed)
    audit.record(user, "collaboration.handoff", "case", str(case_id), {"recipient_id": str(body.recipient_id), "hash": service.canonical_hash(result)})
    return result


@router.get("/cases/{case_id}/evidence/v1", response_model=EvidenceBundleV1)
def export_evidence(case_id: UUID, purpose: str, user: CurrentUser = Depends(ANY_USER)):
    case, policy, _ = service.require_case(user, str(case_id), "export", purpose)
    service.check_egress("local", policy["region"], policy["classification"], True)
    # Versioned, bounded projections over existing case references; no vendor adapter claims.
    db = get_db()
    tid, cid = user.tenant_id, str(case_id)
    evidence = db.table("case_evidence").select("id,evidence_type,transaction_ids,account_id,alert_id,risk_signal_id").eq("tenant_id", tid).eq("case_id", cid).order("created_at").limit(501).execute().data
    if len(evidence) > 500:
        raise HTTPException(422, "Evidence exceeds export cap; narrow the case before export")
    from app.core.db import fetch_by_ids
    transactions = fetch_by_ids("transactions", tid, {t for e in evidence for t in e.get("transaction_ids", [])}, "id,occurred_at,amount,currency,origin_account_id,destination_account_id,source_row_id")
    alerts = fetch_by_ids("alerts", tid, {e["alert_id"] for e in evidence if e.get("alert_id")}, "id,typology,rank_score,model_run_id")
    if len(transactions) > 500:
        raise HTTPException(422, "Transaction export cap exceeded")
    entity_ids = {t[k] for t in transactions for k in ("origin_account_id", "destination_account_id")}
    entity_ids |= {e["account_id"] for e in evidence if e.get("account_id")}
    entities = fetch_by_ids("accounts", tid, entity_ids, "id,is_internal,status")
    narratives = db.table("case_narratives").select("id").eq("tenant_id", tid).eq("case_id", cid).order("version", desc=True).limit(1).execute().data
    claims = db.table("narrative_claims").select("id,claim_text,claim_kind,verification,referenced_transaction_ids").eq("tenant_id", tid).eq("narrative_id", narratives[0]["id"]).order("claim_index").limit(100).execute().data if narratives else []
    allowed_txns = {t["id"] for t in transactions}
    claims = [c for c in claims if c["verification"] == "supported" and c["claim_kind"] == "fact" and c.get("referenced_transaction_ids") and set(c["referenced_transaction_ids"]) <= allowed_txns]
    dispositions = db.table("case_decisions").select("id,decision,disposition,decided_at").eq("tenant_id", tid).eq("case_id", cid).order("decided_at", desc=True).limit(100).execute().data
    result = {"schema_version": "evidence-bundle-v1", "as_of": datetime.now(UTC).isoformat(), "tenant_id": tid, "case_id": cid,
              "case": {"id": cid, "status": case["status"], "disposition": case.get("disposition")}, "evidence": evidence, "transactions": transactions,
              "alerts": alerts, "entities": entities, "graph_paths": [], "claims": claims, "dispositions": dispositions,
              "availability": {"graph_paths": "unavailable", "claims": "unavailable" if not narratives else "partial", "dispositions": "partial" if len(dispositions) == 100 else "available"},
              "caveats": ["IDs and transaction facts only. Account identifiers and free text are minimized.", "Graph paths are unavailable in this schema version. Claims use the latest narrative (100 maximum) and must be supported by exported transaction IDs. Latest 100 decisions only.", "No incumbent-platform import mappings have been validated."]}
    result = EvidenceBundleV1.model_validate({**to_json(result), "manifest_sha256": ""}).model_dump(mode="json", exclude={"manifest_sha256"})
    result["manifest_sha256"] = service.canonical_hash(result)
    audit.record(user, "evidence.exported", "case", cid, {"schema_version": "evidence-bundle-v1", "hash": result["manifest_sha256"]})
    return result


@router.post("/metrics", status_code=201)
def write_metric(body: ServiceMetric, user: CurrentUser = Depends(CAN_WRITE_SCORES)):
    return get_db().table("service_metrics").insert({"tenant_id": user.tenant_id, **body.model_dump(mode="json")}).execute().data[0]


@router.get("/operations")
def operations(user: CurrentUser = Depends(ANY_USER)):
    since = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
    rows = get_db().table("service_metrics").select("operation,duration_ms,success,source_ref,observed_at").eq("tenant_id", user.tenant_id).gte("observed_at", since).order("observed_at", desc=True).limit(5000).execute().data
    return {"as_of": datetime.now(UTC).isoformat(), "window_hours": 24, "sample_cap": 5000, "truncated": len(rows) == 5000,
            "metrics": slo_report(rows), "dependency_health": "Derived from operation samples; no active dependency probes.",
            "caveats": ["Fewer than 10 samples is unknown. Last sample older than one hour is stale.", "Targets are prototype defaults, not contractual SLOs."]}


@router.post("/drift/check", status_code=201)
def compare_drift(body: DriftCheck, user: CurrentUser = Depends(CAN_WRITE_SCORES)):
    report = drift_report(body)
    stored = get_db().table("drift_reports").insert({"tenant_id": user.tenant_id, "created_by": user.id, "report": report}).execute().data[0]
    audit.record(user, "monitoring.compared", "drift_report", stored["id"], {"hash": service.canonical_hash(report), "model_version": body.model_version})
    return {"id": stored["id"], "report": report}


@router.get("/drift/reports")
def drift_history(user: CurrentUser = Depends(ANY_USER)):
    return get_db().table("drift_reports").select("*").eq("tenant_id", user.tenant_id).order("created_at", desc=True).limit(50).execute().data


class EgressCheck(StrictModel):
    destination: str = Field(min_length=1, max_length=120)
    region: str = Field(min_length=2, max_length=20)
    classification: Literal["internal", "confidential", "restricted"]
    synthetic: bool = True


@router.post("/egress/check")
def egress(body: EgressCheck, user: CurrentUser = Depends(ANY_USER)):
    result = service.check_egress(**body.model_dump())
    audit.record(user, "environment.checked", "environment", get_settings().environment, {"destination": body.destination, "region": body.region})
    return result


@router.get("/environment")
def environment(user: CurrentUser = Depends(ANY_USER)):
    settings = get_settings()
    return {"environment": settings.environment, "declared_region": settings.deployment_region, "approved_destinations": settings.approved_data_destinations,
            "locations": {"raw_data": "Supabase Storage / Postgres", "embeddings": "Supabase pgvector", "prompts": "Versioned agents/ prompt files", "logs": "Backend host; sensitive request bodies excluded", "model_artifacts": "Backend artifact directory / configured Supabase bucket", "evidence_exports": "Local response or private Supabase case-exports bucket"},
            "caveat": "Declared deployment policy. Cloud account regions and external agent processes require separate verification; this endpoint does not inspect them."}


@router.get("/release")
def release_status(user: CurrentUser = Depends(ANY_USER)):
    from pathlib import Path
    from scripts.release import ROOT, verify
    import json
    package = ROOT / "backend/release-manifest.json"
    if not package.is_file():
        return {"status": "not_configured", "signature": "unknown", "caveat": "Generate and sign a release manifest with the release script first."}
    key = get_settings().release_public_key_path
    try:
        verified = verify(json.loads(package.read_text()), public_key=Path(key) if key else None)
    except Exception:
        return {"status": "verification_failed", "signature": "unverified", "caveat": "Pinned files or signature could not be verified against the configured trust anchor."}
    return {"status": "verified" if verified["signature"] == "verified" else "unsigned", **verified}
