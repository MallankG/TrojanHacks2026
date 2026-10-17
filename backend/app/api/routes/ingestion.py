import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.api.deps import Limit, Offset, page
from app.core.config import get_settings
from app.core.db import get_db, get_one
from app.core.errors import NotFound
from app.core.security import ANY_USER, CAN_INGEST, CurrentUser
from app.services import canonical, ingestion

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


@router.get("/schema")
def canonical_schema(_: CurrentUser = Depends(ANY_USER)) -> dict:
    settings = get_settings()
    return {
        "schema_version": settings.canonical_schema_version,
        "required_fields": list(canonical.REQUIRED_FIELDS),
        "optional_fields": list(canonical.OPTIONAL_FIELDS),
        "descriptions": canonical.FIELD_DESCRIPTIONS,
        "channels": sorted(set(canonical.CHANNEL_ALIASES.values())),
        "currencies": sorted(canonical.KNOWN_CURRENCIES),
    }


@router.post("/preview-mapping")
async def preview_mapping(
    file: UploadFile = File(...),
    field_mapping: str | None = Form(default=None),
    _: CurrentUser = Depends(CAN_INGEST),
) -> dict:
    """Read only the header row and propose a canonical mapping; nothing is stored."""
    head = (await file.read(64 * 1024)).decode("utf-8-sig", errors="replace").splitlines()
    headers = [h.strip() for h in head[0].split(",")] if head else []
    mapping = canonical.auto_map(headers, _parse_mapping(field_mapping))
    return {
        "headers": headers,
        "mapping": mapping,
        "missing_required": [f for f in canonical.REQUIRED_FIELDS if f not in mapping],
        "unmapped_columns": [h for h in headers if h not in mapping.values()],
    }


@router.post("/upload", status_code=201)
def upload(
    file: UploadFile = File(...),
    source_system: str = Form(..., max_length=200),
    data_owner: str = Form(..., max_length=200),
    field_mapping: str | None = Form(default=None, description='JSON object {"canonical_field": "csv column"}'),
    is_synthetic: bool = Form(default=True),
    user: CurrentUser = Depends(CAN_INGEST),
):
    # Sync endpoint: FastAPI runs it in the threadpool so blocking DB/storage calls never stall the event loop.
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    content = file.file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(413, f"File exceeds {get_settings().max_upload_mb} MB")
    if not content.strip():
        raise HTTPException(422, "File is empty")
    try:
        return ingestion.ingest_csv(
            user, file.filename or "upload.csv", content, source_system, data_owner,
            _parse_mapping(field_mapping), is_synthetic,
        )
    except ingestion.DuplicateFile as dup:
        return JSONResponse(status_code=409, content={
            "detail": "This file (same checksum) was already ingested",
            "source_file_id": dup.source_file["id"],
            "ingestion_run_id": dup.latest_run["id"] if dup.latest_run else None,
        })


def _parse_mapping(raw: str | None) -> dict[str, str] | None:
    if not raw:
        return None
    try:
        mapping = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "field_mapping must be a JSON object") from exc
    if not isinstance(mapping, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()):
        raise HTTPException(422, "field_mapping must map canonical field names to column names")
    unknown = set(mapping) - set(canonical.CANONICAL_FIELDS)
    if unknown:
        raise HTTPException(422, f"Unknown canonical fields: {sorted(unknown)}")
    return mapping


@router.get("/runs")
def list_runs(limit: Limit = 50, offset: Offset = 0, user: CurrentUser = Depends(ANY_USER)) -> dict:
    query = (
        get_db().table("ingestion_runs").select("*, source_files(filename, source_system, data_owner)", count="exact")
        .eq("tenant_id", user.tenant_id).order("started_at", desc=True)
    )
    return page(query, limit, offset)


@router.get("/runs/{run_id}")
def get_run(run_id: UUID, user: CurrentUser = Depends(ANY_USER)) -> dict:
    if not get_one("ingestion_runs", user.tenant_id, str(run_id), "id"):
        raise NotFound("Ingestion run")
    return ingestion.summarize_run(user.tenant_id, str(run_id))


@router.get("/runs/{run_id}/rows")
def list_rows(
    run_id: UUID,
    status: Literal["accepted", "accepted_with_warnings", "rejected"] | None = None,
    limit: Limit = 100,
    offset: Offset = 0,
    user: CurrentUser = Depends(ANY_USER),
) -> dict:
    query = (
        get_db().table("source_rows").select("*, data_quality_issues(*)", count="exact")
        .eq("tenant_id", user.tenant_id).eq("run_id", str(run_id)).order("row_number")
    )
    if status:
        query = query.eq("status", status)
    return page(query, limit, offset)


@router.get("/runs/{run_id}/issues")
def list_run_issues(
    run_id: UUID,
    severity: Literal["info", "warning", "error", "critical"] | None = None,
    issue_type: str | None = None,
    limit: Limit = 100,
    offset: Offset = 0,
    user: CurrentUser = Depends(ANY_USER),
) -> dict:
    query = (
        get_db().table("data_quality_issues").select("*, source_rows(row_number)", count="exact")
        .eq("tenant_id", user.tenant_id).eq("run_id", str(run_id)).order("created_at")
    )
    if severity:
        query = query.eq("severity", severity)
    if issue_type:
        query = query.eq("issue_type", issue_type)
    return page(query, limit, offset)


@router.get("/source-files/{file_id}/download-url")
def source_file_url(file_id: UUID, user: CurrentUser = Depends(ANY_USER)) -> dict:
    """Short-lived signed URL to the original uploaded file."""
    source_file = get_one("source_files", user.tenant_id, str(file_id))
    if not source_file:
        raise NotFound("Source file")
    signed = get_db().storage.from_(source_file["storage_bucket"]).create_signed_url(source_file["storage_path"], 300)
    return {"url": signed.get("signedURL") or signed.get("signedUrl"), "expires_in": 300}
