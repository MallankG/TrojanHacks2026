"""Command center: dashboard aggregates, global search and derived notifications (all read-only)."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import ANY_USER, CurrentUser
from app.services import dashboard as dash

router = APIRouter(tags=["command center"])

# Mirrors CAN_INVESTIGATE / CAN_REVIEW: case records are only for case-capable roles (not data stewards).
CASE_ROLES = ("admin", "investigator", "analyst", "reviewer")
REVIEW_ROLES = ("admin", "reviewer")
NOTIFICATION_WINDOW_DAYS = 14


@router.get("/dashboard/summary")
def dashboard_summary(
    sla_days: Annotated[int | None, Query(ge=1, le=90, description="Review SLA in days (defaults to REVIEW_SLA_DAYS)")] = None,
    user: CurrentUser = Depends(ANY_USER),
) -> dict:
    """Open alerts, alert age, cases, ingestion health and model freshness for the command center.

    Alerts come from the latest completed ranker run, like the alert queue, so re-scoring never double counts.
    """
    tid = user.tenant_id
    now = datetime.now(UTC)
    sla = sla_days or get_settings().review_sla_days
    can_see_cases = user.role in CASE_ROLES

    def alert_section() -> dict:
        ranker = dash.latest_ranker(tid)
        if not ranker:
            return {**dash.summarize_alerts([], now, sla), "truncated": False, "run_alert_total": 0,
                    "connected_accounts": 0, "connected_accounts_truncated": False, "model_run": None}
        (rows, total), (connected, connected_capped) = dash.parallel(
            lambda: dash.load_alerts(tid, ranker["id"]), lambda: dash.load_connected_accounts(tid, ranker["id"])
        )
        return {**dash.summarize_alerts(rows, now, sla), "truncated": total > len(rows), "run_alert_total": total,
                "connected_accounts": connected, "connected_accounts_truncated": connected_capped, "model_run": ranker}

    def case_section() -> dict | None:
        if not can_see_cases:
            return None
        statuses, case_total = dash.load_case_statuses(tid)
        return {**dash.summarize_cases(statuses), "truncated": case_total > len(statuses)}

    alerts, cases, (runs, run_total), dq_open, (freshness, ranker_attempt) = dash.parallel(
        alert_section, case_section, lambda: dash.load_runs(tid),
        lambda: dash.load_open_dq_by_severity(tid), lambda: dash.load_freshness(tid, now),
    )
    ingestion = {**dash.summarize_ingestion(runs, dq_open), "truncated": run_total > len(runs)}

    return {
        "generated_at": now.isoformat(),
        "alerts": alerts,
        "cases": cases,
        "ingestion": ingestion,
        "model_freshness": freshness,
        "pipeline": dash.pipeline_flags(ingestion, freshness, ranker_attempt, now),
    }


def _alert_item(a: dict) -> dict:
    return {
        "type": "alert", "id": a["id"], "label": f"AL-{a['alert_number']}",
        "description": f"Alert · {a['typology'].replace('_', ' ').capitalize()} · {a['priority_band']} priority · {a['status'].replace('_', ' ')}",
    }


@router.get("/search")
def global_search(
    q: Annotated[str, Query(min_length=1, max_length=100)],
    user: CurrentUser = Depends(ANY_USER),
) -> dict:
    """Bounded search over alerts, cases, accounts and transactions (at most 8 of each)."""
    db = get_db()
    tid = user.tenant_id
    parsed = dash.parse_search(q)
    text, limit = parsed["text"], dash.SEARCH_LIMIT
    alert_number, case_number = parsed["alert_number"], parsed["case_number"]
    results: dict[str, list[dict]] = {"alerts": [], "cases": [], "accounts": [], "transactions": []}
    if len(text) < 2 and alert_number is None and case_number is None:
        return {"query": text, "results": results, "cases_included": user.role in CASE_ROLES}

    pattern = dash.like_pattern(text) if len(text) >= 2 else None

    def search_alerts() -> list[dict]:
        cols = "id, alert_number, typology, priority_band, status"
        found: dict[str, dict] = {}
        if alert_number is not None:
            for a in db.table("alerts").select(cols).eq("tenant_id", tid).eq("alert_number", alert_number).limit(1).execute().data:
                found[a["id"]] = a
        if parsed["typologies"]:
            # Typology matches come from the current queue (latest ranker run) so re-scoring does not repeat them.
            ranker = dash.latest_ranker(tid)
            query = db.table("alerts").select(cols).eq("tenant_id", tid).in_("typology", parsed["typologies"])
            if ranker:
                query = query.eq("model_run_id", ranker["id"])
            for a in query.order("priority_rank", nullsfirst=False).limit(limit).execute().data:
                found.setdefault(a["id"], a)
        return [_alert_item(a) for a in list(found.values())[:limit]]

    def search_cases() -> list[dict]:
        if user.role not in CASE_ROLES:
            return []
        cols = "id, case_number, title, status"
        found: dict[str, dict] = {}
        if case_number is not None:
            for c in db.table("cases").select(cols).eq("tenant_id", tid).eq("case_number", case_number).limit(1).execute().data:
                found[c["id"]] = c
        if pattern:
            for c in db.table("cases").select(cols).eq("tenant_id", tid).ilike("title", pattern).order("updated_at", desc=True).limit(limit).execute().data:
                found.setdefault(c["id"], c)
        return [
            {"type": "case", "id": c["id"], "label": f"CASE-{c['case_number']:04d}", "description": f"Case · {c['status'].replace('_', ' ')} · {c['title']}"}
            for c in list(found.values())[:limit]
        ]

    def search_accounts() -> list[dict]:
        if not pattern:
            return []
        rows = (
            db.table("accounts").select("id, account_ref, institution_ref, status, is_internal").eq("tenant_id", tid)
            .ilike("account_ref", pattern).order("account_ref").limit(limit).execute().data
        )
        return [
            {"type": "account", "id": a["id"], "label": a["account_ref"],
             "description": f"Account · {'internal' if a['is_internal'] else 'external'} · {a['status']}" + (f" · {a['institution_ref']}" if a.get("institution_ref") else "")}
            for a in rows
        ]

    def search_transactions() -> list[dict]:
        if not pattern:
            return []
        rows = (
            db.table("transactions").select("id, transaction_ref, occurred_at, amount::text, currency, channel, origin_account_id").eq("tenant_id", tid)
            .ilike("transaction_ref", pattern).order("occurred_at", desc=True).limit(limit).execute().data
        )
        return [
            {"type": "transaction", "id": t["id"], "label": t["transaction_ref"], "account_id": t["origin_account_id"],
             "description": f"Transaction · {t['channel']} · {t['amount']} {t['currency']} · {t['occurred_at'][:10]}"}
            for t in rows
        ]

    for key, items in zip(results, dash.parallel(search_alerts, search_cases, search_accounts, search_transactions), strict=True):
        results[key] = items
    return {"query": text, "results": results, "cases_included": user.role in CASE_ROLES}


@router.get("/notifications")
def notifications(user: CurrentUser = Depends(ANY_USER)) -> dict:
    """Derived, read-only notifications for the current user. Nothing is stored or marked read."""
    db = get_db()
    tid = user.tenant_id
    can_review = user.role in REVIEW_ROLES
    can_investigate = user.role in CASE_ROLES
    since = (datetime.now(UTC) - timedelta(days=NOTIFICATION_WINDOW_DAYS)).isoformat()

    def pending() -> tuple[list[dict], int]:
        if not can_review:
            return [], 0
        result = (
            db.table("cases").select("id, case_number, title, updated_at", count="exact").eq("tenant_id", tid)
            .eq("status", "pending_review").order("updated_at", desc=True).limit(dash.NOTIFICATION_LIMIT).execute()
        )
        return result.data or [], result.count or 0

    def new_in_latest_run() -> tuple[dict | None, int]:
        ranker = dash.latest_ranker(tid) if can_investigate else None
        if not ranker:
            return None, 0
        return ranker, dash.count_rows(
            db.table("alerts").select("id", count="exact", head=True).eq("tenant_id", tid)
            .eq("model_run_id", ranker["id"]).eq("status", "new")
        )

    (pending_cases, pending_total), (ranker, new_alerts), bad_runs, critical = dash.parallel(
        pending,
        new_in_latest_run,
        lambda: db.table("ingestion_runs").select("id, status, started_at, completed_at, rejected_count, error_message, source_files(filename)")
        .eq("tenant_id", tid).in_("status", ["failed", "completed_with_errors"]).gte("started_at", since)
        .order("started_at", desc=True).limit(dash.NOTIFICATION_LIMIT).execute().data,
        lambda: db.table("data_quality_issues").select("created_at", count="exact").eq("tenant_id", tid)
        .eq("status", "open").eq("severity", "critical").order("created_at", desc=True).limit(1).execute(),
    )
    items = dash.build_notifications(
        can_investigate=can_investigate, can_review=can_review, pending_cases=pending_cases, pending_total=pending_total,
        ranker=ranker, new_alerts=new_alerts, bad_runs=bad_runs, critical_issues=critical.count or 0,
        latest_critical_at=critical.data[0]["created_at"] if critical.data else None,
    )
    return {"items": items, "total": len(items)}
