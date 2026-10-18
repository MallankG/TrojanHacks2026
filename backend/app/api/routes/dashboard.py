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
    text, number, limit = parsed["text"], parsed["number"], dash.SEARCH_LIMIT
    results: dict[str, list[dict]] = {"alerts": [], "cases": [], "accounts": [], "transactions": []}
    if len(text) < 2 and number is None:
        return {"query": text, "results": results, "cases_included": user.role in CASE_ROLES}

    alert_cols = "id, alert_number, typology, priority_band, status"
    alerts: dict[str, dict] = {}
    if number is not None:
        for a in db.table("alerts").select(alert_cols).eq("tenant_id", tid).eq("alert_number", number).limit(1).execute().data:
            alerts[a["id"]] = a
    if parsed["typologies"]:
        ranker = dash.latest_ranker(tid)
        query = db.table("alerts").select(alert_cols).eq("tenant_id", tid).in_("typology", parsed["typologies"])
        if ranker:
            query = query.eq("model_run_id", ranker["id"])
        for a in query.order("priority_rank", nullsfirst=False).limit(limit).execute().data:
            alerts.setdefault(a["id"], a)
    results["alerts"] = [_alert_item(a) for a in list(alerts.values())[:limit]]

    if user.role in CASE_ROLES:
        case_cols = "id, case_number, title, status"
        cases: dict[str, dict] = {}
        if number is not None:
            for c in db.table("cases").select(case_cols).eq("tenant_id", tid).eq("case_number", number).limit(1).execute().data:
                cases[c["id"]] = c
        if len(text) >= 2:
            for c in db.table("cases").select(case_cols).eq("tenant_id", tid).ilike("title", dash.like_pattern(text)).order("updated_at", desc=True).limit(limit).execute().data:
                cases.setdefault(c["id"], c)
        results["cases"] = [
            {"type": "case", "id": c["id"], "label": f"CASE-{c['case_number']:04d}", "description": f"Case · {c['status'].replace('_', ' ')} · {c['title']}"}
            for c in list(cases.values())[:limit]
        ]

    if len(text) >= 2:
        pattern = dash.like_pattern(text)
        accounts = (
            db.table("accounts").select("id, account_ref, institution_ref, status, is_internal").eq("tenant_id", tid)
            .ilike("account_ref", pattern).order("account_ref").limit(limit).execute().data
        )
        results["accounts"] = [
            {"type": "account", "id": a["id"], "label": a["account_ref"],
             "description": f"Account · {'internal' if a['is_internal'] else 'external'} · {a['status']}" + (f" · {a['institution_ref']}" if a.get("institution_ref") else "")}
            for a in accounts
        ]
        txns = (
            db.table("transactions").select("id, transaction_ref, occurred_at, amount, currency, channel").eq("tenant_id", tid)
            .ilike("transaction_ref", pattern).order("occurred_at", desc=True).limit(limit).execute().data
        )
        results["transactions"] = [
            {"type": "transaction", "id": t["id"], "label": t["transaction_ref"],
             "description": f"Transaction · {t['channel']} · {t['amount']} {t['currency']} · {t['occurred_at'][:10]}"}
            for t in txns
        ]
    return {"query": text, "results": results, "cases_included": user.role in CASE_ROLES}


@router.get("/notifications")
def notifications(user: CurrentUser = Depends(ANY_USER)) -> dict:
    """Derived, read-only notifications for the current user. Nothing is stored or marked read."""
    db = get_db()
    tid = user.tenant_id
    can_review = user.role in REVIEW_ROLES
    can_investigate = user.role in CASE_ROLES
    since = (datetime.now(UTC) - timedelta(days=NOTIFICATION_WINDOW_DAYS)).isoformat()

    pending_cases, pending_total = [], 0
    if can_review:
        result = (
            db.table("cases").select("id, case_number, title, updated_at", count="exact").eq("tenant_id", tid)
            .eq("status", "pending_review").order("updated_at", desc=True).limit(dash.NOTIFICATION_LIMIT).execute()
        )
        pending_cases, pending_total = result.data or [], result.count or 0

    ranker, new_alerts = None, 0
    if can_investigate:
        ranker = dash.latest_ranker(tid)
        if ranker:
            new_alerts = dash.count_rows(
                db.table("alerts").select("id", count="exact", head=True).eq("tenant_id", tid)
                .eq("model_run_id", ranker["id"]).eq("status", "new")
            )

    bad_runs = (
        db.table("ingestion_runs").select("id, status, started_at, completed_at, rejected_count, error_message, source_files(filename)")
        .eq("tenant_id", tid).in_("status", ["failed", "completed_with_errors"]).gte("started_at", since)
        .order("started_at", desc=True).limit(dash.NOTIFICATION_LIMIT).execute().data
    )
    critical = (
        db.table("data_quality_issues").select("created_at", count="exact").eq("tenant_id", tid)
        .eq("status", "open").eq("severity", "critical").order("created_at", desc=True).limit(1).execute()
    )
    items = dash.build_notifications(
        can_investigate=can_investigate, can_review=can_review, pending_cases=pending_cases, pending_total=pending_total,
        ranker=ranker, new_alerts=new_alerts, bad_runs=bad_runs, critical_issues=critical.count or 0,
        latest_critical_at=critical.data[0]["created_at"] if critical.data else None,
    )
    return {"items": items, "total": len(items)}
