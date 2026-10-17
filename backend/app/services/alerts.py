"""Alert publication and the data-quality caveats attached to alerts."""

from app.api.deps import ensure_tenant_ids
from app.core.db import fetch_all, fetch_by_ids, get_db, insert_many
from app.core.security import CurrentUser
from app.schemas import AlertBatch


def _rejected_rows(tenant_id: str) -> list[dict]:
    db = get_db()
    return fetch_all(lambda: db.table("source_rows").select("id, raw_data").eq("tenant_id", tenant_id)
                     .eq("status", "rejected").order("id"))


def _dq_caveats(tenant_id: str, transaction_ids: set[str], account_ids: set[str],
                rejected: list[dict]) -> tuple[bool, str | None]:
    """Flag alerts whose evidence rests on rows with quality warnings, or whose accounts appear in rejected rows."""
    notes = []
    txns = fetch_by_ids("transactions", tenant_id, transaction_ids, "id, source_row_id")
    rows = fetch_by_ids("source_rows", tenant_id, {t["source_row_id"] for t in txns}, "id, status")
    warned = sum(1 for r in rows if r["status"] == "accepted_with_warnings")
    if warned:
        notes.append(f"{warned} supporting transaction(s) were accepted with data-quality warnings")

    refs = {a["account_ref"] for a in fetch_by_ids("accounts", tenant_id, account_ids, "id, account_ref")}
    if refs:
        hits = sum(1 for r in rejected if refs & {str(v).strip() for v in (r["raw_data"] or {}).values()})
        if hits:
            notes.append(f"{hits} rejected source row(s) reference involved accounts; activity may be incomplete")
    return bool(notes), "; ".join(notes) or None


def publish(user: CurrentUser, batch: AlertBatch) -> list[dict]:
    tenant_id = user.tenant_id
    created = []
    rejected = _rejected_rows(tenant_id)
    for alert in batch.alerts:
        txn_ids = {str(t.transaction_id) for t in alert.transactions}
        account_ids = {str(a.account_id) for a in alert.accounts}
        if alert.primary_account_id:
            account_ids.add(str(alert.primary_account_id))
        has_caveat, caveat_notes = _dq_caveats(tenant_id, txn_ids, account_ids, rejected)
        row = alert.model_dump(mode="json", exclude={"signal_ids", "transactions", "accounts"})
        row.update({
            "tenant_id": tenant_id,
            "model_run_id": str(batch.model_run_id),
            "transaction_count": len(txn_ids),
            "connected_account_count": len(account_ids),
            "has_dq_caveat": has_caveat,
            "dq_caveat_notes": caveat_notes,
        })
        record = insert_many("alerts", [row], actor=user)[0]
        alert_id = record["id"]
        try:
            insert_many("alert_signals", [{"alert_id": alert_id, "signal_id": s, "tenant_id": tenant_id}
                                          for s in sorted({str(s) for s in alert.signal_ids})])
            insert_many("alert_transactions", [{"alert_id": alert_id, "transaction_id": t, "tenant_id": tenant_id, "role": r}
                                               for t, r in _strongest_roles(alert.transactions, "transaction_id",
                                                                            TXN_ROLE_RANK).items()])
            links = _strongest_roles(alert.accounts, "account_id", ACCOUNT_ROLE_RANK)
            if alert.primary_account_id:
                links[str(alert.primary_account_id)] = "subject"
            insert_many("alert_accounts", [{"alert_id": alert_id, "account_id": a, "tenant_id": tenant_id, "role": r}
                                           for a, r in links.items()])
        except Exception:
            # No multi-statement transactions over PostgREST: compensate so no alert exists without its links.
            get_db().table("alerts").delete().eq("tenant_id", tenant_id).eq("id", alert_id).execute()
            raise
        created.append(record)
    return created


TXN_ROLE_RANK = {"trigger": 0, "supporting": 1, "context": 2}
ACCOUNT_ROLE_RANK = {"subject": 0, "intermediary": 1, "counterparty": 2}


def _strongest_roles(links, id_field: str, rank: dict[str, int]) -> dict[str, str]:
    """Collapse duplicate links to one per id, keeping the most significant role."""
    roles: dict[str, str] = {}
    for link in links:
        key = str(getattr(link, id_field))
        if key not in roles or rank[link.role] < rank[roles[key]]:
            roles[key] = link.role
    return roles


def validate_batch(user: CurrentUser, batch: AlertBatch) -> None:
    """Check every referenced id before writing anything, so a bad reference cannot leave a partial batch."""
    tid = user.tenant_id
    ensure_tenant_ids("transactions", tid, {t.transaction_id for a in batch.alerts for t in a.transactions}
                      | {a.primary_transaction_id for a in batch.alerts}, "transaction ids")
    ensure_tenant_ids("accounts", tid, {x.account_id for a in batch.alerts for x in a.accounts}
                      | {a.primary_account_id for a in batch.alerts}, "account ids")
    ensure_tenant_ids("risk_signals", tid, {s for a in batch.alerts for s in a.signal_ids}, "signal ids")
