"""Accounts, counterparties and transactions (read APIs)."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import Limit, Offset, page
from app.core.db import get_db, get_one
from app.core.errors import NotFound
from app.core.security import ANY_CALLER, CurrentUser

router = APIRouter(tags=["accounts & transactions"])

ACCOUNT_BRIEF = "id, account_ref, institution_ref, is_internal"
TXN_SELECT = (
    f"*, origin:accounts!transactions_tenant_id_origin_account_id_fkey({ACCOUNT_BRIEF}), "
    f"destination:accounts!transactions_tenant_id_destination_account_id_fkey({ACCOUNT_BRIEF})"
)


@router.get("/accounts")
def list_accounts(
    q: str | None = Query(default=None, max_length=64, description="Part of account_ref (case-insensitive)"),
    is_internal: bool | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
    user: CurrentUser = Depends(ANY_CALLER),
) -> dict:
    query = (
        get_db().table("accounts").select("*, counterparties(normalized_name, entity_type)", count="exact")
        .eq("tenant_id", user.tenant_id).order("account_ref")
    )
    if q:
        # Substring match so "7011" finds ACC-7011; LIKE wildcards and filter syntax in q are stripped.
        term = "".join(ch for ch in q.strip() if ch not in "%_*,()")
        if term:
            query = query.ilike("account_ref", f"%{term}%")
    if is_internal is not None:
        query = query.eq("is_internal", is_internal)
    return page(query, limit, offset)


@router.get("/accounts/{account_id}")
def get_account(account_id: UUID, user: CurrentUser = Depends(ANY_CALLER)) -> dict:
    db = get_db()
    account = get_one("accounts", user.tenant_id, str(account_id), "*, counterparties(*)")
    if not account:
        raise NotFound("Account")
    alerts = (
        db.table("alert_accounts").select("role, alerts(id, alert_number, typology, priority_band, status, created_at)")
        .eq("tenant_id", user.tenant_id).eq("account_id", str(account_id)).execute().data
    )
    stats = {}
    for side in ("origin_account_id", "destination_account_id"):
        result = db.table("transactions").select("id", count="exact", head=True).eq("tenant_id", user.tenant_id).eq(side, str(account_id)).execute()
        stats["outbound_count" if side.startswith("origin") else "inbound_count"] = result.count
    return {"account": account, "alerts": alerts, "stats": stats}


@router.get("/counterparties")
def list_counterparties(
    q: str | None = Query(default=None, max_length=100),
    limit: Limit = 50,
    offset: Offset = 0,
    user: CurrentUser = Depends(ANY_CALLER),
) -> dict:
    query = get_db().table("counterparties").select("*", count="exact").eq("tenant_id", user.tenant_id).order("normalized_name")
    if q:
        query = query.ilike("normalized_name", f"%{q.upper().replace('%', '')}%")
    return page(query, limit, offset)


@router.get("/transactions")
def list_transactions(
    account_id: UUID | None = Query(default=None, description="Either side of the transaction"),
    start: datetime | None = None,
    end: datetime | None = None,
    min_amount_usd: Decimal | None = None,
    channel: str | None = None,
    ingestion_run_id: UUID | None = None,
    limit: Limit = 100,
    offset: Offset = 0,
    user: CurrentUser = Depends(ANY_CALLER),
) -> dict:
    query = get_db().table("transactions").select(TXN_SELECT, count="exact").eq("tenant_id", user.tenant_id)
    if account_id:
        query = query.or_(f"origin_account_id.eq.{account_id},destination_account_id.eq.{account_id}")
    if start:
        query = query.gte("occurred_at", start.isoformat())
    if end:
        query = query.lte("occurred_at", end.isoformat())
    if min_amount_usd is not None:
        query = query.gte("amount_usd", str(min_amount_usd))
    if channel:
        query = query.eq("channel", channel)
    if ingestion_run_id:
        query = query.eq("ingestion_run_id", str(ingestion_run_id))
    return page(query.order("occurred_at"), limit, offset)


@router.get("/transactions/{transaction_id}")
def get_transaction(transaction_id: UUID, user: CurrentUser = Depends(ANY_CALLER)) -> dict:
    txn = get_one("transactions", user.tenant_id, str(transaction_id), TXN_SELECT + ", source_rows(id, row_number, raw_data, status)")
    if not txn:
        raise NotFound("Transaction")
    return txn
