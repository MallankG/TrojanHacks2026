"""Data operations and governance views: source-feed health and the data-quality rule catalog."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.config import get_settings
from app.core.db import fetch_all, get_db
from app.core.security import ANY_USER, CurrentUser
from app.services import data_ops
from app.services.quality import RULES_VERSION, rule_catalog

router = APIRouter(tags=["data operations"])


@router.get("/ingestion/sources")
def list_sources(
    freshness_sla_hours: Annotated[int | None, Query(ge=1, le=24 * 90)] = None,
    user: CurrentUser = Depends(ANY_USER),
) -> dict:
    """Source feeds grouped by source_system: owners, freshness against the SLA, completeness and open issues."""
    sla = freshness_sla_hours or get_settings().source_freshness_sla_hours
    db = get_db()
    files = fetch_all(
        lambda: db.table("source_files")
        .select("id, source_system, data_owner, schema_version, is_synthetic, uploaded_at")
        .eq("tenant_id", user.tenant_id).order("id")
    )
    runs = fetch_all(
        lambda: db.table("ingestion_runs")
        .select("id, file_id, status, row_count, accepted_count, warning_count, rejected_count, started_at")
        .eq("tenant_id", user.tenant_id).order("id")
    )
    now = datetime.now(UTC)
    items = data_ops.summarize_sources(files, runs, data_ops.open_issue_counts(user.tenant_id, files, runs), now, sla)
    return {"items": items, "total": len(items), "freshness_sla_hours": sla, "as_of": now.isoformat()}


@router.get("/data-quality/rules")
def list_rules(_: CurrentUser = Depends(ANY_USER)) -> dict:
    """The data-quality rules every ingested row runs through, with severity and what a failure does."""
    items = rule_catalog()
    return {"rules_version": RULES_VERSION, "items": items, "total": len(items)}
