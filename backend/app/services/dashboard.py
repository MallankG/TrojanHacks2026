"""Command-center aggregates, global search and derived notifications.

Everything here is read-only. The pure functions (summaries, search parsing, flags) take plain rows so
they can be tested without a database; the `load_*` functions run the bounded, tenant-scoped queries.
Counts are leads for triage, not findings: an open alert is a model output waiting for a human.
"""

import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Any

from app.core.db import PAGE_SIZE, get_db

PRIORITY_BANDS = ("critical", "high", "medium", "low")
ALERT_STATUSES = ("new", "under_review", "in_case", "closed_no_action", "dismissed")
# Alerts still waiting for triage. Alerts in a case are tracked through the case instead.
OPEN_ALERT_STATUSES = ("new", "under_review")
CASE_STATUSES = ("open", "in_review", "pending_review", "escalated", "closed")
DQ_SEVERITIES = ("critical", "error", "warning", "info")
MODEL_KINDS = ("rule_engine", "supervised", "anomaly", "graph_model", "ranker")
TYPOLOGIES = (
    "rapid_in_out", "layering", "mule_network", "funnel", "fan_out", "cycle", "structuring",
    "dormant_reactivation", "behavioral_deviation", "scam_proceeds", "model_anomaly", "other",
)

# Server-side caps so a large tenant cannot turn the dashboard into a full table scan.
MAX_SUMMARY_ALERTS = 5000
MAX_SUMMARY_RUNS = 1000
MAX_SUMMARY_CASES = 5000
MAX_CONNECTED_LINKS = 20000
SEARCH_LIMIT = 8
SEARCH_MAX_LENGTH = 64
NOTIFICATION_LIMIT = 5
# A ranker older than this is shown as stale; it is a demo setting, not a regulatory threshold.
MODEL_STALE_DAYS = 7


# --- Pure helpers ---------------------------------------------------------------------------------
def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _hours(delta: timedelta) -> float:
    return round(delta.total_seconds() / 3600, 1)


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def age_buckets(ages: list[timedelta], sla_days: int) -> list[dict]:
    """Open-alert age histogram with edges at 1 day, 3 days, the SLA and twice the SLA."""
    edges = sorted({0, 1, 3, sla_days, sla_days * 2})
    buckets = []
    for i, low in enumerate(edges):
        high = edges[i + 1] if i + 1 < len(edges) else None
        label = f"{low}+ days" if high is None else (f"Under {high} day{'s' if high > 1 else ''}" if low == 0 else f"{low} to {high} days")
        count = sum(1 for a in ages if a >= timedelta(days=low) and (high is None or a < timedelta(days=high)))
        buckets.append({"label": label, "min_days": low, "max_days": high, "count": count, "past_sla": low >= sla_days})
    return buckets


def summarize_alerts(rows: list[dict], now: datetime, sla_days: int) -> dict:
    """Open-alert aggregates for one ranker run. `rows` are that run's alerts (any status)."""
    by_status = dict.fromkeys(ALERT_STATUSES, 0)
    for row in rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    open_rows = [r for r in rows if r["status"] in OPEN_ALERT_STATUSES]

    by_band = dict.fromkeys(PRIORITY_BANDS, 0)
    typologies: dict[str, int] = {}
    amounts: dict[str, Decimal] = {}
    missing_amount = 0
    ages: list[timedelta] = []
    for row in open_rows:
        by_band[row["priority_band"]] = by_band.get(row["priority_band"], 0) + 1
        typologies[row["typology"]] = typologies.get(row["typology"], 0) + 1
        amount = _decimal(row.get("total_amount"))
        if amount is None:
            missing_amount += 1
        else:
            currency = row.get("amount_currency") or "USD"
            amounts[currency] = amounts.get(currency, Decimal(0)) + amount
        created = parse_ts(row.get("created_at"))
        if created:
            ages.append(max(now - created, timedelta(0)))

    sla = timedelta(days=sla_days)
    return {
        "open_total": len(open_rows),
        "by_status": by_status,
        "by_priority_band": by_band,
        "review_first": by_band["critical"] + by_band["high"],
        # Most common first, ties by name so the order is stable.
        "by_typology": [{"typology": t, "count": n} for t, n in sorted(typologies.items(), key=lambda kv: (-kv[1], kv[0]))],
        "age": {
            "sla_days": sla_days,
            "oldest_hours": _hours(max(ages)) if ages else None,
            "median_hours": _hours(median(ages)) if ages else None,
            "past_sla": sum(1 for a in ages if a > sla),
            "buckets": age_buckets(ages, sla_days),
        },
        "estimated_amount": {
            "is_estimate": True,
            "note": "Sum of the amounts attached to open alerts. Alerts can share transactions, so this is an estimate, not a loss figure.",
            # Exact decimals as strings; never mixed across currencies.
            "by_currency": {c: format(v, "f") for c, v in sorted(amounts.items())},
            "alerts_without_amount": missing_amount,
        },
    }


def summarize_cases(statuses: list[str]) -> dict:
    by_status = dict.fromkeys(CASE_STATUSES, 0)
    for status in statuses:
        by_status[status] = by_status.get(status, 0) + 1
    return {
        "total": len(statuses),
        "open_total": len(statuses) - by_status["closed"],
        "pending_review": by_status["pending_review"],
        "by_status": by_status,
    }


def summarize_ingestion(runs: list[dict], dq_open_by_severity: dict[str, int]) -> dict:
    """`runs` newest first."""
    totals = {k: sum(r.get(k) or 0 for r in runs) for k in ("row_count", "accepted_count", "warning_count", "rejected_count")}
    latest = runs[0] if runs else None
    return {
        "run_count": len(runs),
        "latest_run": None if latest is None else {
            "id": latest["id"],
            "status": latest["status"],
            "started_at": latest.get("started_at"),
            "completed_at": latest.get("completed_at"),
            "filename": (latest.get("source_files") or {}).get("filename"),
            "row_count": latest.get("row_count") or 0,
            "rejected_count": latest.get("rejected_count") or 0,
        },
        "total_rows": totals["row_count"],
        "accepted_rows": totals["accepted_count"],
        "warning_rows": totals["warning_count"],
        "rejected_rows": totals["rejected_count"],
        "rejected_rate": round(totals["rejected_count"] / totals["row_count"], 4) if totals["row_count"] else None,
        "failed_runs": sum(1 for r in runs if r["status"] == "failed"),
        "partial_runs": sum(1 for r in runs if r["status"] == "completed_with_errors"),
        "open_issues_by_severity": {s: dq_open_by_severity.get(s, 0) for s in DQ_SEVERITIES},
        "open_issues_total": sum(dq_open_by_severity.get(s, 0) for s in DQ_SEVERITIES),
    }


def pipeline_flags(ingestion: dict, freshness: dict[str, dict | None], latest_ranker_attempt: dict | None, now: datetime) -> list[dict]:
    """Health flags for the shell. status: ok | attention | failed | not_run."""
    flags = []
    latest = ingestion["latest_run"]
    if latest is None:
        flags.append({"key": "ingestion", "status": "not_run", "detail": "No batch has been ingested yet."})
    elif latest["status"] == "failed":
        flags.append({"key": "ingestion", "status": "failed", "detail": "The latest batch failed to load."})
    elif latest["status"] in ("pending", "running"):
        flags.append({"key": "ingestion", "status": "attention", "detail": "A batch is still loading."})
    elif latest["status"] == "completed_with_errors":
        flags.append({"key": "ingestion", "status": "attention", "detail": f"Latest batch loaded with {latest['rejected_count']} rejected row(s)."})
    else:
        flags.append({"key": "ingestion", "status": "ok", "detail": "Latest batch loaded cleanly."})

    severity = ingestion["open_issues_by_severity"]
    blocking = severity["critical"] + severity["error"]
    if severity["critical"]:
        flags.append({"key": "data_quality", "status": "failed", "detail": f"{severity['critical']} open critical issue(s)."})
    elif blocking or severity["warning"]:
        flags.append({"key": "data_quality", "status": "attention", "detail": f"{blocking + severity['warning']} open issue(s) need a steward."})
    else:
        flags.append({"key": "data_quality", "status": "ok", "detail": "No open blocking issues."})

    ranker = freshness.get("ranker")
    if latest_ranker_attempt and latest_ranker_attempt["status"] == "failed":
        flags.append({"key": "scoring", "status": "failed", "detail": "The latest ranker run failed; the queue shows the previous run."})
    elif latest_ranker_attempt and latest_ranker_attempt["status"] == "running":
        flags.append({"key": "scoring", "status": "attention", "detail": "A ranker run is in progress."})
    elif ranker is None:
        flags.append({"key": "scoring", "status": "not_run", "detail": "No ranker run has been published yet."})
    else:
        completed = parse_ts(ranker.get("completed_at"))
        stale = completed is not None and now - completed > timedelta(days=MODEL_STALE_DAYS)
        flags.append({
            "key": "scoring",
            "status": "attention" if stale else "ok",
            "detail": f"Latest ranker is older than {MODEL_STALE_DAYS} days." if stale else "Latest ranker run completed.",
        })
    return flags


def freshness_row(run: dict | None, now: datetime) -> dict | None:
    if run is None:
        return None
    completed = parse_ts(run.get("completed_at"))
    return {
        "id": run["id"],
        "model_name": run.get("model_name"),
        "model_version": run.get("model_version"),
        "completed_at": run.get("completed_at"),
        "age_hours": _hours(now - completed) if completed else None,
    }


_SAFE = re.compile(r"[^A-Za-z0-9 _.\-@]")


def parse_search(q: str) -> dict:
    """Normalise a search string. Only a safe character set reaches the database filters."""
    text = _SAFE.sub("", q).strip()[:SEARCH_MAX_LENGTH]
    digits = re.fullmatch(r"(AL|CASE)?-?0*(\d{1,12})", text, flags=re.IGNORECASE)
    prefix = digits.group(1).upper() if digits and digits.group(1) else None
    number = int(digits.group(2)) if digits else None
    words = text.lower().replace("-", " ").replace("_", " ").strip()
    typologies = [t for t in TYPOLOGIES if words in t.replace("_", " ")] if len(words) >= 3 else []
    return {
        "text": text,
        # "AL-12" only looks up alerts and "CASE-0012" only cases; a bare number tries both.
        "alert_number": number if prefix in (None, "AL") else None,
        "case_number": number if prefix in (None, "CASE") else None,
        "typologies": typologies,
    }


def like_pattern(text: str) -> str:
    """ilike pattern that treats the user's text literally (escapes LIKE wildcards)."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def build_notifications(*, can_investigate: bool, can_review: bool, pending_cases: list[dict], pending_total: int,
                        ranker: dict | None, new_alerts: int, bad_runs: list[dict], critical_issues: int,
                        latest_critical_at: str | None) -> list[dict]:
    """Derived notifications, newest first. Each item names a target so the UI can link to it."""
    items: list[dict] = []
    if can_review:
        for case in pending_cases:
            items.append({
                "id": f"case-pending:{case['id']}", "kind": "case_pending_review", "at": case.get("updated_at"),
                "title": f"Review requested: CASE-{case['case_number']:04d}",
                "detail": case.get("title"), "target_type": "case", "target_id": case["id"],
            })
        if pending_total > len(pending_cases):
            items.append({
                "id": "case-pending:more", "kind": "case_pending_review", "at": None,
                "title": f"{pending_total - len(pending_cases)} more case(s) pending review",
                "detail": None, "target_type": "cases", "target_id": None,
            })
    if can_investigate and ranker and new_alerts:
        items.append({
            "id": f"ranker:{ranker['id']}", "kind": "new_alerts", "at": ranker.get("completed_at"),
            "title": f"{new_alerts} new alert(s) in the latest ranker run",
            "detail": f"{ranker.get('model_name')} {ranker.get('model_version')}".strip(), "target_type": "alerts", "target_id": None,
        })
    for run in bad_runs:
        failed = run["status"] == "failed"
        name = (run.get("source_files") or {}).get("filename") or run["id"][:8]
        outcome = "failed to load" if failed else f"loaded with {run.get('rejected_count') or 0} rejected row(s)"
        items.append({
            "id": f"run:{run['id']}", "kind": "ingestion_failed" if failed else "ingestion_partial",
            "at": run.get("completed_at") or run.get("started_at"),
            "title": f"{name} {outcome}",
            "detail": run.get("error_message"), "target_type": "ingestion_run", "target_id": run["id"],
        })
    if critical_issues:
        items.append({
            "id": "dq-critical", "kind": "critical_data_quality", "at": latest_critical_at,
            "title": f"{critical_issues} open critical data-quality issue(s)",
            "detail": None, "target_type": "data_quality", "target_id": None,
        })
    return sorted(items, key=lambda i: i["at"] or "", reverse=True)


# --- Queries --------------------------------------------------------------------------------------
def parallel(*calls: Callable[[], Any]) -> list[Any]:
    """Run independent read queries concurrently (each is one HTTP round trip to PostgREST)."""
    get_db().postgrest  # noqa: B018 - build the shared client once, before the threads use it
    with ThreadPoolExecutor(max_workers=min(len(calls), 8)) as pool:
        return [f.result() for f in [pool.submit(c) for c in calls]]


def count_rows(query) -> int:
    return query.limit(1).execute().count or 0


def _bounded(build_query, cap: int) -> tuple[list[dict], int]:
    """Page through an ordered query up to `cap` rows; returns rows and the exact total."""
    rows: list[dict] = []
    total = 0
    start = 0
    while start < cap:
        end = min(start + PAGE_SIZE, cap) - 1
        result = build_query().range(start, end).execute()
        total = result.count or 0
        page = result.data or []
        rows.extend(page)
        if len(page) < end - start + 1:
            break
        start = end + 1
    return rows, total


def latest_ranker(tenant_id: str) -> dict | None:
    rows = (
        get_db().table("model_runs").select("id, model_name, model_version, completed_at").eq("tenant_id", tenant_id)
        .eq("model_kind", "ranker").eq("status", "completed").order("completed_at", desc=True).limit(1).execute().data
    )
    return rows[0] if rows else None


def load_alerts(tenant_id: str, run_id: str) -> tuple[list[dict], int]:
    db = get_db()
    return _bounded(
        lambda: db.table("alerts").select("id, status, priority_band, typology, created_at, total_amount::text, amount_currency", count="exact")
        .eq("tenant_id", tenant_id).eq("model_run_id", run_id).order("created_at").order("id"),
        MAX_SUMMARY_ALERTS,
    )


def load_connected_accounts(tenant_id: str, run_id: str) -> tuple[int, bool]:
    """Distinct accounts linked to the run's open alerts, and whether the cap cut the count short."""
    db = get_db()
    links, total = _bounded(
        lambda: db.table("alert_accounts").select("account_id, alerts!inner(model_run_id, status)", count="exact")
        .eq("tenant_id", tenant_id).eq("alerts.model_run_id", run_id).in_("alerts.status", list(OPEN_ALERT_STATUSES))
        .order("alert_id").order("account_id"),
        MAX_CONNECTED_LINKS,
    )
    return len({link["account_id"] for link in links}), total > len(links)


def load_case_statuses(tenant_id: str) -> tuple[list[str], int]:
    db = get_db()
    rows, total = _bounded(
        lambda: db.table("cases").select("status", count="exact").eq("tenant_id", tenant_id).order("id"), MAX_SUMMARY_CASES
    )
    return [r["status"] for r in rows], total


def load_runs(tenant_id: str) -> tuple[list[dict], int]:
    db = get_db()
    return _bounded(
        lambda: db.table("ingestion_runs")
        .select("id, status, started_at, completed_at, row_count, accepted_count, warning_count, rejected_count, error_message, source_files(filename)", count="exact")
        .eq("tenant_id", tenant_id).order("started_at", desc=True).order("id"),
        MAX_SUMMARY_RUNS,
    )


def load_open_dq_by_severity(tenant_id: str) -> dict[str, int]:
    db = get_db()

    def count(severity: str) -> Callable[[], int]:
        return lambda: count_rows(
            db.table("data_quality_issues").select("id", count="exact", head=True).eq("tenant_id", tenant_id)
            .eq("status", "open").eq("severity", severity)
        )

    return dict(zip(DQ_SEVERITIES, parallel(*(count(s) for s in DQ_SEVERITIES)), strict=True))


def load_freshness(tenant_id: str, now: datetime) -> tuple[dict[str, dict | None], dict | None]:
    """Latest completed run per model kind, plus the latest ranker attempt (any status)."""
    db = get_db()

    def latest_completed(kind: str) -> Callable[[], list[dict]]:
        return lambda: (
            db.table("model_runs").select("id, model_name, model_version, completed_at").eq("tenant_id", tenant_id)
            .eq("model_kind", kind).eq("status", "completed").order("completed_at", desc=True).limit(1).execute().data
        )

    *completed, attempt = parallel(
        *(latest_completed(k) for k in MODEL_KINDS),
        lambda: db.table("model_runs").select("id, status, started_at").eq("tenant_id", tenant_id).eq("model_kind", "ranker")
        .order("started_at", desc=True).limit(1).execute().data,
    )
    freshness = {k: freshness_row(rows[0] if rows else None, now) for k, rows in zip(MODEL_KINDS, completed, strict=True)}
    return freshness, attempt[0] if attempt else None


def llm_status() -> dict:
    """The agents' configured LLM (no network call): provider, model, and why it would fall back to the template.
    Mirrors tracelayer_agents.llm.client: Groq needs a key and an approved egress destination."""
    from tracelayer_agents.llm.client import LLMSettings

    s = LLMSettings.from_env()
    reason = None
    if s.provider == "template":
        reason = "Template only (no LLM configured)"
    elif s.provider == "groq" and not s.groq_api_key:
        reason = "Groq API key missing"
    elif s.provider == "groq" and not s.groq_approved:
        reason = "Groq not an approved data destination"
    return {"provider": s.provider, "model": s.model, "ready": reason is None, "reason": reason}
