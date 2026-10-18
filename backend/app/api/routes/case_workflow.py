"""Case workflow extensions: assignees, readiness checklist, four-eyes escalation requests and hashed evidence packages."""

import uuid
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.core import audit
from app.core.config import get_settings
from app.core.db import get_db, get_one, insert_many, to_json, with_actor
from app.core.errors import NotFound
from app.core.security import ANY_CALLER, CAN_INVESTIGATE, CurrentUser
from app.schemas import EscalationRequestIn
from app.services import evidence_package as pkg

router = APIRouter(prefix="/cases", tags=["cases"])

SIGNED_URL_SECONDS = 300
MAX_EXPORTS_LISTED = 50
INVESTIGATOR_ROLES = ("admin", "investigator", "analyst", "reviewer")


def _case_or_404(user: CurrentUser, case_id: UUID, columns: str = "*") -> dict:
    case = get_one("cases", user.tenant_id, str(case_id), columns)
    if not case:
        raise NotFound("Case")
    return case


@router.get("/assignees")
def list_assignees(user: CurrentUser = Depends(CAN_INVESTIGATE)) -> list[dict]:
    """Users in the caller's tenant who can own a case (for the owner picker)."""
    return (get_db().table("profiles").select("id, display_name, role").eq("tenant_id", user.tenant_id)
            .in_("role", list(INVESTIGATOR_ROLES)).order("display_name").limit(200).execute().data)


@router.get("/{case_id}/checklist")
def checklist(case_id: UUID, user: CurrentUser = Depends(ANY_CALLER)) -> dict:
    """Investigation readiness computed from the case's real evidence references (never inferred)."""
    _case_or_404(user, case_id, "id")
    material = pkg.load_case_material(user.tenant_id, str(case_id))
    items = pkg.build_checklist(material)
    return {"case_id": str(case_id), "as_of": datetime.now(UTC).isoformat(), "items": items,
            "summary": pkg.checklist_summary(items), "unresolved_refs": material["unresolved"]}


@router.post("/{case_id}/escalation-request", status_code=201)
def request_escalation(case_id: UUID, body: EscalationRequestIn, user: CurrentUser = Depends(CAN_INVESTIGATE)) -> dict:
    """Ask a reviewer to escalate. The requester can never record that escalation or the disposition (four-eyes)."""
    case = _case_or_404(user, case_id, "id, status, escalation_requested_at")
    if case["status"] in ("closed", "escalated"):
        raise HTTPException(409, f"Case is already {case['status']}")
    if case["escalation_requested_at"]:
        raise HTTPException(409, "An escalation request is already waiting for a reviewer")
    changes = {"escalation_requested_by": user.id, "escalation_requested_at": datetime.now(UTC),
               "escalation_reason": body.reason, "status": "pending_review"}
    query = get_db().table("cases").update(to_json(changes)).eq("tenant_id", user.tenant_id).eq("id", str(case_id))
    updated = with_actor(query, user).execute().data[0]
    audit.record(user, "case.escalation_requested", "case", str(case_id), {"reason": body.reason})
    return updated


def _signed_urls(paths: list[str]) -> dict[str, str]:
    if not paths:
        return {}
    storage = get_db().storage.from_(get_settings().case_exports_bucket)
    signed = storage.create_signed_urls(paths, SIGNED_URL_SECONDS)
    out = {}
    for entry in signed:
        url = entry.get("signedURL") or entry.get("signedUrl")
        if entry.get("path") and url:
            out[entry["path"]] = url
    return out


def _with_urls(rows: list[dict]) -> list[dict]:
    paths = [f"{r['storage_prefix']}/{f['name']}" for r in rows for f in r["files"]]
    urls = _signed_urls(paths)
    return [{**r, "files": [{**f, "url": urls.get(f"{r['storage_prefix']}/{f['name']}")} for f in r["files"]],
             "url_expires_in": SIGNED_URL_SECONDS} for r in rows]


@router.post("/{case_id}/evidence-export", status_code=201)
def evidence_export(case_id: UUID, user: CurrentUser = Depends(CAN_INVESTIGATE)) -> dict:
    """Build the SAR-support evidence package (not a FinCEN filing), hash every file and store it in case-exports."""
    _case_or_404(user, case_id, "id")
    tid, cid = user.tenant_id, str(case_id)
    material = pkg.load_case_material(tid, cid, include_audit=True)
    if material["unresolved"]:
        raise HTTPException(409, {"message": "Evidence references do not resolve in your tenant; nothing was exported.",
                                  "unresolved_refs": material["unresolved"]})
    files, manifest = pkg.build_package(material)
    sums = pkg.sha256sums(files)
    files["SHA256SUMS"] = sums
    manifest_sha = pkg.sha256_hex(files["manifest.json"])
    package_sha = pkg.sha256_hex(sums)

    export_id = str(uuid.uuid4())
    settings = get_settings()
    prefix = f"{tid}/cases/{cid}/exports/{export_id}"
    storage = get_db().storage.from_(settings.case_exports_bucket)
    entries = []
    for name, data in sorted(files.items()):
        content_type = "text/csv" if name.endswith(".csv") else "text/plain" if name == "SHA256SUMS" else "application/json"
        storage.upload(f"{prefix}/{name}", data, {"content-type": content_type})
        entries.append({"name": name, "sha256": pkg.sha256_hex(data), "bytes": len(data), "content_type": content_type})

    row = {"id": export_id, "tenant_id": tid, "case_id": cid, "storage_bucket": settings.case_exports_bucket,
           "storage_prefix": prefix, "manifest_sha256": manifest_sha, "package_sha256": package_sha, "files": entries,
           "manifest": manifest, "checklist": manifest["checklist"], "created_by": user.id}
    record = insert_many("evidence_exports", [row], actor=user)[0]
    audit.record(user, "case.exported", "case", cid, {
        "export_id": export_id, "format": pkg.PACKAGE_SCHEMA, "path": prefix,
        "sha256": package_sha, "manifest_sha256": manifest_sha, "file_count": len(entries),
    })
    return _with_urls([record])[0]


@router.get("/{case_id}/exports")
def list_exports(case_id: UUID, user: CurrentUser = Depends(CAN_INVESTIGATE)) -> list[dict]:
    """Evidence packages for the case, newest first, with short-lived signed download URLs per file."""
    _case_or_404(user, case_id, "id")
    rows = (get_db().table("evidence_exports")
            .select("id, case_id, storage_prefix, manifest_sha256, package_sha256, files, checklist, created_by, created_at, "
                    "creator:profiles!evidence_exports_created_by_fkey(display_name, role)")
            .eq("tenant_id", user.tenant_id).eq("case_id", str(case_id)).order("created_at", desc=True)
            .limit(MAX_EXPORTS_LISTED).execute().data)
    return _with_urls(rows)
