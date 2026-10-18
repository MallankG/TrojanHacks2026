"""Lineage: source row -> canonical transaction -> features -> signals/model runs -> alerts -> cases ->
evidence -> narratives -> reviewer decisions."""

from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.db import chunked, fetch_by_ids, get_db, get_one
from app.core.errors import NotFound
from app.core.security import ANY_CALLER, CurrentUser

router = APIRouter(prefix="/lineage", tags=["lineage"])


def _batched(ids, query_for_batch, size: int = 100) -> list[dict]:
    """Run a query per batch of ids so filters stay well under URL length limits; de-duplicate by id."""
    ordered = sorted({str(i) for i in ids if i})
    rows: dict = {}
    for batch in chunked(ordered, size):
        for row in query_for_batch(batch).execute().data or []:
            rows[row.get("id") or tuple(sorted(row.items()))] = row
    return list(rows.values())


def _downstream_of_transactions(tenant_id: str, txn_ids: list[str]) -> dict:
    db = get_db()
    signals = _batched(txn_ids, lambda b: db.table("risk_signals")
                       .select("id, signal_name, signal_version, source, typology, score, triggered, model_run_id, feature_id")
                       .eq("tenant_id", tenant_id)
                       .or_(f"transaction_id.in.({','.join(b)}),evidence_transaction_ids.ov.{{{','.join(b)}}}"))
    alert_links = _batched(txn_ids, lambda b: db.table("alert_transactions").select("alert_id, transaction_id, role")
                           .eq("tenant_id", tenant_id).in_("transaction_id", b))
    alert_ids = {a["alert_id"] for a in alert_links}
    alerts = fetch_by_ids("alerts", tenant_id, alert_ids, "id, alert_number, typology, priority_band, status, model_run_id")
    case_links = _batched(alert_ids, lambda b: db.table("case_alerts").select("case_id, alert_id")
                          .eq("tenant_id", tenant_id).in_("alert_id", b))
    evidence = _batched(txn_ids, lambda b: db.table("case_evidence")
                        .select("id, case_id, evidence_type, explanation, added_by_type, created_at")
                        .eq("tenant_id", tenant_id).ov("transaction_ids", b))
    case_ids = {c["case_id"] for c in case_links} | {e["case_id"] for e in evidence}
    cases = fetch_by_ids("cases", tenant_id, case_ids, "id, case_number, title, status, disposition, disposed_at")
    claims = _batched(txn_ids, lambda b: db.table("narrative_claims")
                      .select("id, narrative_id, claim_text, claim_kind, verification")
                      .eq("tenant_id", tenant_id).ov("referenced_transaction_ids", b))
    narratives = sorted(_batched(case_ids, lambda b: db.table("case_narratives")
                                 .select("id, case_id, version, author_type, verification_status, created_at")
                                 .eq("tenant_id", tenant_id).in_("case_id", b)), key=lambda n: (n["case_id"], n["version"]))
    decisions = sorted(_batched(case_ids, lambda b: db.table("case_decisions")
                                .select("id, case_id, narrative_id, decision, disposition, decided_by, decided_at")
                                .eq("tenant_id", tenant_id).in_("case_id", b)), key=lambda d: d["decided_at"])
    run_ids = {s["model_run_id"] for s in signals} | {a["model_run_id"] for a in alerts}
    model_runs = fetch_by_ids("model_runs", tenant_id, run_ids,
                              "id, model_name, model_kind, model_version, feature_set_version, dataset_version, status")
    return {"risk_signals": signals, "model_runs": model_runs, "alerts": alerts, "cases": cases, "case_evidence": evidence,
            "narratives": narratives, "narrative_claims": claims, "decisions": decisions}


@router.get("/transactions/{transaction_id}")
def transaction_lineage(transaction_id: UUID, user: CurrentUser = Depends(ANY_CALLER)) -> dict:
    db = get_db()
    tid, txid = user.tenant_id, str(transaction_id)
    txn = get_one("transactions", tid, txid)
    if not txn:
        raise NotFound("Transaction")
    row = get_one("source_rows", tid, txn["source_row_id"], "id, row_number, raw_data, status, row_hash")
    run = get_one("ingestion_runs", tid, txn["ingestion_run_id"],
                  "id, status, schema_version, pipeline_version, field_mapping, started_at, completed_at, "
                  "source_files(id, filename, checksum_sha256, source_system, data_owner, uploaded_at)")
    return {
        "source": {"ingestion_run": run, "source_row": row,
                   "data_quality_issues": db.table("data_quality_issues").select("*").eq("tenant_id", tid)
                   .eq("source_row_id", txn["source_row_id"]).execute().data},
        "transaction": txn,
        "features": db.table("features").select("id, feature_set_version, as_of_timestamp, computed_at")
        .eq("tenant_id", tid).eq("transaction_id", txid).execute().data,
        **_downstream_of_transactions(tid, [txid]),
    }


@router.get("/alerts/{alert_id}")
def alert_lineage(alert_id: UUID, user: CurrentUser = Depends(ANY_CALLER)) -> dict:
    db = get_db()
    tid, aid = user.tenant_id, str(alert_id)
    alert = get_one("alerts", tid, aid)
    if not alert:
        raise NotFound("Alert")
    txn_ids = [r["transaction_id"] for r in db.table("alert_transactions").select("transaction_id")
               .eq("tenant_id", tid).eq("alert_id", aid).execute().data]
    txns = fetch_by_ids("transactions", tid, txn_ids, "id, transaction_ref, source_row_id, ingestion_run_id")
    rows = fetch_by_ids("source_rows", tid, {t["source_row_id"] for t in txns}, "id, row_number, status, run_id")
    runs = fetch_by_ids("ingestion_runs", tid, {t["ingestion_run_id"] for t in txns},
                        "id, status, schema_version, source_files(filename, checksum_sha256, data_owner)")
    issues = []
    row_ids = sorted({r["id"] for r in rows})
    for i in range(0, len(row_ids), 150):
        issues += db.table("data_quality_issues").select("id, source_row_id, issue_type, severity, status, message") \
            .eq("tenant_id", tid).in_("source_row_id", row_ids[i : i + 150]).execute().data
    downstream = _downstream_of_transactions(tid, txn_ids) if txn_ids else {}
    return {
        "alert": alert,
        "source": {"ingestion_runs": runs, "source_rows": rows, "data_quality_issues": issues},
        "transactions": txns,
        **downstream,
    }
