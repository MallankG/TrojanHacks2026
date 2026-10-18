"""Data operations: source-feed health and the mapping preview shown before an upload.

Both are read-only views over what ingestion already records (source_files, ingestion_runs,
data_quality_issues); nothing here writes to the database.
"""

import csv
import io
from datetime import datetime
from itertools import islice

from app.core.db import get_db
from app.services import canonical
from app.services.quality import RowValidator, schema_issues

PREVIEW_ROWS = 20
PREVIEW_BYTES = 256 * 1024
MAX_RUNS_PER_SOURCE_QUERY = 100  # run ids per `in` filter when counting open issues (keeps URLs short)


# --- Mapping preview -------------------------------------------------------------------------------


def parse_preview(content: bytes, complete: bool) -> tuple[list[str], list[dict]]:
    """Header and the first PREVIEW_ROWS data rows, read the same way ingestion reads the full file.

    `complete` is False when `content` is only the start of a larger file; the last (possibly cut)
    line is then dropped.
    """
    text = content.decode("utf-8-sig", errors="replace")
    if not complete and "\n" in text:
        text = text[: text.rindex("\n")]
    rows = list(islice(csv.reader(io.StringIO(text)), PREVIEW_ROWS * 2 + 1))
    if not rows:
        return [], []
    headers = canonical.dedupe_headers([h.strip() for h in rows[0]])
    sample: list[dict] = []
    for cells in rows[1:]:
        if not any(cell.strip() for cell in cells):
            continue  # csv.DictReader (used by ingestion) skips blank lines too
        padded = cells + [""] * (len(headers) - len(cells))
        sample.append(dict(zip(headers, padded)))
        if len(sample) == PREVIEW_ROWS:
            break
    return headers, sample


def build_preview(headers: list[str], sample: list[dict], overrides: dict[str, str] | None,
                  existing_refs: set[str]) -> dict:
    """Proposed mapping, per-field detail and a dry-run of the row rules on the sample. Nothing is stored."""
    mapping = canonical.auto_map(headers, overrides)
    overrides = overrides or {}
    fields = []
    for field in canonical.CANONICAL_FIELDS:
        column = mapping.get(field)
        if field in overrides:
            match = "manual" if overrides[field] else "unmapped"
        else:
            match = canonical.match_kind(field, column) if column else None
        fields.append({
            "field": field,
            "required": field in canonical.REQUIRED_FIELDS,
            "type": canonical.FIELD_TYPES[field],
            "description": canonical.FIELD_DESCRIPTIONS[field],
            "column": column,
            "match": match,
            "column_present": column in headers if column else False,
        })

    file_issues = schema_issues(headers, mapping)
    blocked = any(i.rejects for i in file_issues)
    validator = RowValidator(mapping, existing_refs)
    rows = []
    for number, raw in enumerate(sample, start=1):
        result = None if blocked else validator.validate(number, raw)
        rows.append({
            "row_number": number,
            "values": raw,
            "status": result.status if result else None,
            "issues": [
                {"rule_id": i.rule_id, "severity": i.severity, "field_name": i.field_name, "message": i.message}
                for i in (result.issues if result else [])
            ],
        })
    return {
        "headers": headers,
        "mapping": mapping,
        "fields": fields,
        "missing_required": [f for f in canonical.REQUIRED_FIELDS if mapping.get(f) not in headers],
        "unmapped_columns": [h for h in headers if h not in mapping.values()],
        "schema_issues": [
            {"rule_id": i.rule_id, "severity": i.severity, "field_name": i.field_name, "message": i.message,
             "observed_value": i.observed_value}
            for i in file_issues
        ],
        "can_upload": not blocked,
        "rows": rows,
        "sampled_rows": len(rows),
    }


def existing_refs(tenant_id: str, refs: set[str]) -> set[str]:
    """Which of these transaction ids are already ingested (bounded by the preview sample size)."""
    if not refs:
        return set()
    rows = (
        get_db().table("transactions").select("transaction_ref").eq("tenant_id", tenant_id)
        .in_("transaction_ref", sorted(refs)).execute().data
    )
    return {r["transaction_ref"] for r in rows}


# --- Source feeds ----------------------------------------------------------------------------------


def _at(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def summarize_sources(files: list[dict], runs: list[dict], open_issues: dict[str, int], now: datetime,
                      sla_hours: int) -> list[dict]:
    """One entry per source_system with ownership, freshness, completeness and status.

    `open_issues` maps source_system to its count of open data-quality issues.
    """
    runs_by_file: dict[str, list[dict]] = {}
    for run in runs:
        runs_by_file.setdefault(run["file_id"], []).append(run)
    groups: dict[str, list[dict]] = {}
    for f in files:
        groups.setdefault(f["source_system"], []).append(f)

    feeds = []
    for source, source_files in groups.items():
        source_runs = sorted((r for f in source_files for r in runs_by_file.get(f["id"], [])),
                             key=lambda r: r["started_at"], reverse=True)
        uploads = [t for t in (_at(f["uploaded_at"]) for f in source_files) if t]
        last_upload = max(uploads) if uploads else None
        age_hours = round((now - last_upload).total_seconds() / 3600, 2) if last_upload else None
        rows = sum(r.get("row_count") or 0 for r in source_runs)
        accepted = sum(r.get("accepted_count") or 0 for r in source_runs)
        warnings = sum(r.get("warning_count") or 0 for r in source_runs)
        rejected = sum(r.get("rejected_count") or 0 for r in source_runs)
        latest = source_runs[0] if source_runs else None

        reasons = []
        if latest is None:
            status = "no_runs"
            reasons.append("No ingestion run recorded")
        else:
            if latest["status"] == "failed":
                reasons.append("Latest run failed")
            if age_hours is not None and age_hours > sla_hours:
                reasons.append(f"No upload for {age_hours:.0f} h (SLA {sla_hours} h)")
            if (latest.get("rejected_count") or 0) > 0 and latest["status"] != "failed":
                reasons.append(f"Latest run rejected {latest['rejected_count']} row(s)")
            status = (
                "failed" if latest["status"] == "failed"
                else "late" if age_hours is not None and age_hours > sla_hours
                else "has_rejections" if (latest.get("rejected_count") or 0) > 0
                else "current"
            )

        feeds.append({
            "source_system": source,
            "owners": sorted({f["data_owner"] for f in source_files if f.get("data_owner")}),
            "schema_versions": sorted({f["schema_version"] for f in source_files if f.get("schema_version")}),
            "is_synthetic": all(f.get("is_synthetic", True) for f in source_files),
            "file_count": len(source_files),
            "run_count": len(source_runs),
            "last_upload_at": last_upload.isoformat() if last_upload else None,
            "hours_since_upload": age_hours,
            "freshness_sla_hours": sla_hours,
            "rows": rows,
            "accepted": accepted,
            "warnings": warnings,
            "rejected": rejected,
            # accepted includes rows kept with warnings
            "accepted_rate": _rate(accepted, rows),
            "warning_rate": _rate(warnings, rows),
            "rejected_rate": _rate(rejected, rows),
            "open_issue_count": open_issues.get(source, 0),
            "latest_run": {
                "id": latest["id"],
                "status": latest["status"],
                "started_at": latest["started_at"],
                "row_count": latest.get("row_count"),
                "accepted_count": latest.get("accepted_count"),
                "rejected_count": latest.get("rejected_count"),
            } if latest else None,
            "status": status,
            "status_reasons": reasons,
        })
    feeds.sort(key=lambda s: s["last_upload_at"] or "", reverse=True)
    return feeds


def open_issue_counts(tenant_id: str, files: list[dict], runs: list[dict]) -> dict[str, int]:
    """Open data-quality issue count per source_system, using exact counts (no rows transferred)."""
    db = get_db()
    source_by_file = {f["id"]: f["source_system"] for f in files}
    run_ids_by_source: dict[str, list[str]] = {}
    for run in runs:
        source = source_by_file.get(run["file_id"])
        if source:
            run_ids_by_source.setdefault(source, []).append(run["id"])
    counts: dict[str, int] = {}
    for source, run_ids in run_ids_by_source.items():
        total = 0
        for i in range(0, len(run_ids), MAX_RUNS_PER_SOURCE_QUERY):
            chunk = run_ids[i : i + MAX_RUNS_PER_SOURCE_QUERY]
            result = (
                db.table("data_quality_issues").select("id", count="exact").eq("tenant_id", tenant_id)
                .eq("status", "open").in_("run_id", chunk).limit(1).execute()
            )
            total += result.count or 0
        counts[source] = total
    return counts
