"""CSV ingestion: storage upload, lineage records, data-quality checks and canonical transactions."""

import csv
import hashlib
import io
import logging
import re
from datetime import UTC, datetime

from app.core.config import get_settings
from app.core.db import fetch_all, get_db, insert_many, to_json
from app.core.security import CurrentUser
from app.services import canonical
from app.services.quality import RULES_VERSION, Issue, RowValidator, row_hash, schema_issues

logger = logging.getLogger(__name__)

# Institution identifiers treated as "our bank"; accounts at other institutions are external graph nodes.
HOME_INSTITUTION_REFS = {"TLBANK", "HOME", "SELF"}


class DuplicateFile(Exception):
    def __init__(self, source_file: dict, latest_run: dict | None):
        self.source_file = source_file
        self.latest_run = latest_run


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name).strip("._")
    return cleaned[:120] or "upload.csv"


def _issue_row(tenant_id: str, run_id: str, issue: Issue, source_row_id: str | None) -> dict:
    return {
        "tenant_id": tenant_id,
        "run_id": run_id,
        "source_row_id": source_row_id,
        "field_name": issue.field_name,
        "issue_type": issue.issue_type,
        "severity": issue.severity,
        "rule_id": issue.rule_id,
        "message": issue.message,
        "observed_value": issue.observed_value,
        "expected_value": issue.expected_value,
    }


def _existing_transaction_refs(tenant_id: str) -> set[str]:
    db = get_db()
    rows = fetch_all(
        lambda: db.table("transactions").select("transaction_ref").eq("tenant_id", tenant_id)
        .not_.is_("transaction_ref", "null").order("id")
    )
    return {r["transaction_ref"] for r in rows}


def _resolve_counterparties(tenant_id: str, names: set[str]) -> dict[str, str]:
    """Deterministic entity resolution: exact match on normalized name. Returns normalized_name -> id."""
    if not names:
        return {}
    db = get_db()
    existing = fetch_all(
        lambda: db.table("counterparties").select("id, normalized_name").eq("tenant_id", tenant_id).order("id")
    )
    by_name = {r["normalized_name"]: r["id"] for r in existing}
    raw_by_normalized: dict[str, str] = {}
    for raw in names:
        normalized = canonical.normalize_entity_name(raw)
        if normalized:
            raw_by_normalized.setdefault(normalized, raw)
    missing = [
        {
            "tenant_id": tenant_id,
            "raw_name": raw,
            "normalized_name": normalized,
            "entity_type": "business" if canonical.ENTITY_SUFFIXES.search(raw.upper()) else "unknown",
            "resolution_status": "resolved",
            "resolution_method": "deterministic_normalized_name",
            "resolution_confidence": 1,
        }
        for normalized, raw in raw_by_normalized.items()
        if normalized not in by_name
    ]
    for row in insert_many("counterparties", missing):
        by_name[row["normalized_name"]] = row["id"]
    return by_name


def _resolve_accounts(tenant_id: str, run_id: str, accepted: list[dict],
                      is_synthetic: bool) -> dict[tuple[str | None, str], str]:
    """Return (institution_ref, account_ref) -> account id, creating accounts first seen in this run."""
    db = get_db()
    existing = fetch_all(
        lambda: db.table("accounts").select("id, institution_ref, account_ref").eq("tenant_id", tenant_id).order("id")
    )
    accounts = {(r["institution_ref"], r["account_ref"]): r["id"] for r in existing}

    names = {n[f"{side}_name"] for n in accepted for side in ("origin", "destination") if n[f"{side}_name"]}
    counterparty_ids = _resolve_counterparties(tenant_id, names)

    new_accounts: dict[tuple[str | None, str], dict] = {}
    for n in accepted:
        for side in ("origin", "destination"):
            key = (n[f"{side}_bank"], n[f"{side}_ref"])
            if key in accounts or key in new_accounts:
                continue
            name = n[f"{side}_name"]
            bank = n[f"{side}_bank"]
            new_accounts[key] = {
                "tenant_id": tenant_id,
                "institution_ref": bank,
                "account_ref": key[1],
                "is_internal": bank is None or bank.upper() in HOME_INSTITUTION_REFS,
                "holder_counterparty_id": counterparty_ids.get(canonical.normalize_entity_name(name)) if name else None,
                "first_seen_run_id": run_id,
                "is_synthetic": is_synthetic,
            }
    for row in insert_many("accounts", list(new_accounts.values())):
        accounts[(row["institution_ref"], row["account_ref"])] = row["id"]
    return accounts


def ingest_csv(
    user: CurrentUser,
    filename: str,
    content: bytes,
    source_system: str,
    data_owner: str,
    field_mapping: dict[str, str] | None = None,
    is_synthetic: bool = True,
) -> dict:
    db = get_db()
    tenant_id = user.tenant_id
    checksum = hashlib.sha256(content).hexdigest()

    existing = db.table("source_files").select("*").eq("tenant_id", tenant_id).eq("checksum_sha256", checksum).execute().data
    source_file = None
    if existing:
        runs = (
            db.table("ingestion_runs").select("*").eq("tenant_id", tenant_id).eq("file_id", existing[0]["id"])
            .order("started_at", desc=True).limit(1).execute().data
        )
        # A file whose last run failed may be retried: the failed run's partial writes were rolled back.
        if not runs or runs[0]["status"] != "failed":
            raise DuplicateFile(existing[0], runs[0] if runs else None)
        source_file = existing[0]

    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    headers = canonical.dedupe_headers([h.strip() for h in (reader.fieldnames or [])])
    reader.fieldnames = headers
    raw_rows = [{k: (v if v is not None else "") for k, v in row.items() if k is not None} for row in reader]

    if source_file is None:
        source_file = _store_file(user, filename, content, checksum, source_system, data_owner, is_synthetic)
    mapping = canonical.auto_map(headers, field_mapping)
    run_id = _start_run(user, source_file["id"], mapping, len(raw_rows))

    try:
        return _process_rows(tenant_id, run_id, source_file, headers, mapping, raw_rows, is_synthetic)
    except Exception as exc:
        logger.exception("Ingestion run %s failed", run_id)
        _rollback_partial_run(tenant_id, run_id)
        db.table("ingestion_runs").update({
            "status": "failed",
            "error_message": str(exc)[:1000],
            "completed_at": datetime.now(UTC).isoformat(),
        }).eq("id", run_id).eq("tenant_id", tenant_id).execute()
        raise


def _store_file(user: CurrentUser, filename: str, content: bytes, checksum: str, source_system: str,
                data_owner: str, is_synthetic: bool) -> dict:
    settings = get_settings()
    db = get_db()
    storage_path = f"{user.tenant_id}/{datetime.now(UTC):%Y/%m}/{checksum[:16]}_{_safe_filename(filename)}"
    db.storage.from_(settings.source_files_bucket).upload(
        storage_path, content, {"content-type": "text/csv", "upsert": "false"}
    )
    return db.table("source_files").insert(to_json({
        "tenant_id": user.tenant_id,
        "filename": filename,
        "storage_bucket": settings.source_files_bucket,
        "storage_path": storage_path,
        "checksum_sha256": checksum,
        "byte_size": len(content),
        "source_system": source_system,
        "data_owner": data_owner,
        "schema_version": settings.canonical_schema_version,
        "is_synthetic": is_synthetic,
        "uploaded_by": user.id,
    })).execute().data[0]


def _start_run(user: CurrentUser, file_id: str, mapping: dict[str, str], row_count: int) -> str:
    settings = get_settings()
    return get_db().table("ingestion_runs").insert(to_json({
        "tenant_id": user.tenant_id,
        "file_id": file_id,
        "status": "running",
        "schema_version": settings.canonical_schema_version,
        "field_mapping": mapping,
        "pipeline_version": f"{settings.pipeline_version}+{RULES_VERSION}",
        "row_count": row_count,
        "triggered_by": user.id,
    })).execute().data[0]["id"]


def _rollback_partial_run(tenant_id: str, run_id: str) -> None:
    """Remove canonical data written by a failed run so the file can be re-ingested cleanly.

    Source rows and DQ issues are kept as the record of what the failed run saw.
    """
    db = get_db()
    try:
        db.table("transactions").delete().eq("tenant_id", tenant_id).eq("ingestion_run_id", run_id).execute()
        db.table("accounts").delete().eq("tenant_id", tenant_id).eq("first_seen_run_id", run_id).execute()
    except Exception:  # noqa: BLE001 - best effort; the run is still marked failed
        logger.exception("Could not roll back partial writes of ingestion run %s", run_id)


def _process_rows(tenant_id: str, run_id: str, source_file: dict, headers: list[str], mapping: dict[str, str],
                  raw_rows: list[dict], is_synthetic: bool) -> dict:
    db = get_db()
    file_issues = schema_issues(headers, mapping)
    if any(i.rejects for i in file_issues):
        insert_many("data_quality_issues", [_issue_row(tenant_id, run_id, i, None) for i in file_issues])
        db.table("ingestion_runs").update({
            "status": "failed",
            "rejected_count": len(raw_rows),
            "error_message": "Schema mismatch: required canonical fields are not mapped",
            "completed_at": datetime.now(UTC).isoformat(),
        }).eq("id", run_id).eq("tenant_id", tenant_id).execute()
        return summarize_run(tenant_id, run_id)

    validator = RowValidator(mapping, _existing_transaction_refs(tenant_id))
    results = [validator.validate(i + 1, raw) for i, raw in enumerate(raw_rows)]

    # Opt-in executable source contracts run on original values before canonical writes.
    # No proposed mapping or changed currency/time semantics is applied automatically.
    if get_settings().governance_contracts_enabled:
        from app.services.governance_superpowers import Product, check_row

        products = db.table("governance_products").select("id, definition").eq("tenant_id", tenant_id).eq(
            "source_system", source_file["source_system"]
        ).order("revision", desc=True).limit(1).execute().data
        if products:
            db.table("ingestion_runs").update({"governance_product_id": products[0]["id"]}).eq(
                "tenant_id", tenant_id
            ).eq("id", run_id).execute()
            contract = Product.model_validate(products[0]["definition"]).contract
            now = datetime.now(UTC)
            for result in results:
                failures = check_row(result.raw, contract, now)
                for failure in failures:
                    result.issues.append(Issue(
                        "GOV-CONTRACT-001", "invalid_format", "error",
                        f"Contract {contract.version}: {failure['code']}", field_name=failure["field"],
                    ))
                if failures:
                    result.normalized = None


    inserted_rows = insert_many("source_rows", [
        {
            "tenant_id": tenant_id,
            "run_id": run_id,
            "row_number": r.row_number,
            "raw_data": r.raw,
            "row_hash": row_hash(r.raw),
            "status": r.status,
        }
        for r in results
    ])
    row_ids = {row["row_number"]: row["id"] for row in inserted_rows}

    issues = [_issue_row(tenant_id, run_id, i, None) for i in file_issues]
    issues += [_issue_row(tenant_id, run_id, i, row_ids[r.row_number]) for r in results for i in r.issues]
    insert_many("data_quality_issues", issues)

    accepted = [r for r in results if r.normalized is not None]
    account_ids = _resolve_accounts(tenant_id, run_id, [r.normalized for r in accepted], is_synthetic)

    transactions = []
    for r in accepted:
        n = r.normalized
        transactions.append({
            "tenant_id": tenant_id,
            "transaction_ref": n["transaction_ref"],
            "ingestion_run_id": run_id,
            "source_row_id": row_ids[r.row_number],
            "occurred_at": n["occurred_at"],
            "amount": n["amount"],
            "currency": n["currency"],
            "amount_usd": n["amount_usd"],
            "fx_rate_to_usd": n["fx_rate_to_usd"],
            "channel": n["channel"],
            "direction": n["direction"],
            "origin_account_id": account_ids[(n["origin_bank"], n["origin_ref"])],
            "destination_account_id": account_ids[(n["destination_bank"], n["destination_ref"])],
            "transaction_status": n["transaction_status"],
            "description": n["description"],
            "original_values": n["original_values"],
            "dedupe_hash": n["dedupe_hash"],
        })
    inserted_txns = insert_many("transactions", transactions)

    txn_by_row = {t["source_row_id"]: t["id"] for t in inserted_txns}
    labels = [
        {
            "tenant_id": tenant_id,
            "transaction_id": txn_by_row[row_ids[r.row_number]],
            "is_suspicious": r.normalized["label"]["is_suspicious"],
            "typology": r.normalized["label"]["typology"],
            "label_source": source_file["source_system"],
            "dataset_version": source_file["checksum_sha256"][:16],
        }
        for r in accepted
        if "label" in r.normalized
    ]
    insert_many("transaction_labels", labels)

    rejected = sum(1 for r in results if r.status == "rejected")
    warned = sum(1 for r in results if r.status == "accepted_with_warnings")
    db.table("ingestion_runs").update({
        "status": "completed_with_errors" if rejected or warned else "completed",
        "accepted_count": len(accepted),
        "warning_count": warned,
        "rejected_count": rejected,
        "completed_at": datetime.now(UTC).isoformat(),
    }).eq("id", run_id).eq("tenant_id", tenant_id).execute()
    return summarize_run(tenant_id, run_id)


def summarize_run(tenant_id: str, run_id: str) -> dict:
    db = get_db()
    run = db.table("ingestion_runs").select("*, source_files(*)").eq("tenant_id", tenant_id).eq("id", run_id).single().execute().data
    issues = fetch_all(
        lambda: db.table("data_quality_issues").select("issue_type, severity").eq("tenant_id", tenant_id)
        .eq("run_id", run_id).order("id")
    )
    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for i in issues:
        by_type[i["issue_type"]] = by_type.get(i["issue_type"], 0) + 1
        by_severity[i["severity"]] = by_severity.get(i["severity"], 0) + 1
    return {"run": run, "issue_counts_by_type": by_type, "issue_counts_by_severity": by_severity}
