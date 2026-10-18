"""Point-in-time account behaviour profile (PRD §4 supporting metrics, §13 screen 5).

Every metric is computed only from transactions that occurred at or before ``as_of``, so a profile
"as of" a past moment never sees later activity. Money stays ``Decimal`` end to end. The numbers
are context for a human reviewer: a deviation from baseline is a lead, not a finding.

Windows (all end at ``as_of``, start-exclusive, end-inclusive):
- flow windows: 1 h, 24 h, 7 d, 30 d;
- observed window: the last ``observed_days`` days;
- baseline window: the ``baseline_days`` days immediately before the observed window.
"""

import math
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.core.db import fetch_by_ids, get_db

MAX_TXNS = 5000  # hard cap on transactions read for one profile
MAX_REJECTED_ROWS_SCANNED = 2000
MAX_LISTED_COUNTERPARTIES = 50
FLOW_WINDOWS = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
MAD_SCALE = Decimal("1.4826")  # makes the MAD consistent with a normal standard deviation
MEAN_AD_SCALE = Decimal("1.2533")  # same for the mean absolute deviation (sqrt(pi/2))
RATIO_PLACES = Decimal("0.0001")
MONEY_PLACES = Decimal("0.01")
AMOUNT_PLACES = Decimal("0.0001")  # numeric(20, 4), the stored precision of amounts

TXN_FIELDS = (
    "id, transaction_ref, occurred_at, amount:amount::text, amount_usd:amount_usd::text, currency, channel, "
    "origin_account_id, destination_account_id, source_row_id"
)
ACCOUNT_FIELDS = (
    "id, account_ref, institution_ref, is_internal, customer_ref, account_type, open_date, status, "
    "customer_risk_rating, jurisdiction, risk_context, is_synthetic, created_at"
)


# ---------------------------------------------------------------- pure helpers


def parse_ts(value: str | datetime) -> datetime:
    ts = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def usd_amount(txn: dict) -> Decimal | None:
    """USD value of a transaction; None when it has no USD amount and is not in USD."""
    if txn.get("amount_usd") not in (None, ""):
        return Decimal(str(txn["amount_usd"]))
    if txn.get("currency") == "USD":
        return Decimal(str(txn["amount"]))
    return None


def median(values: list[Decimal]) -> Decimal | None:
    return quantile(values, Decimal("0.5"))


def quantile(values: list[Decimal], q: Decimal) -> Decimal | None:
    """Linear-interpolation quantile (same as numpy's default), exact in Decimal."""
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lower = int(pos)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (pos - lower)


def robust_z(observed: Decimal, baseline: list[Decimal]) -> dict:
    """Robust z-score of ``observed`` against the baseline values.

    Uses the median and the median absolute deviation (scaled by 1.4826). When the MAD is zero
    (common with sparse baselines) it falls back to the mean absolute deviation (scaled by 1.2533);
    when that is zero too the score is not defined and a reason is returned instead.
    """
    center = median(baseline)
    if center is None:
        return {"z": None, "method": None, "baseline_median": None, "scale": None, "reason": "No baseline days"}
    deviations = [abs(v - center) for v in baseline]
    mad = median(deviations)
    scale, method = mad * MAD_SCALE, "median_absolute_deviation"
    if scale == 0:
        scale, method = (sum(deviations, Decimal(0)) / len(deviations)) * MEAN_AD_SCALE, "mean_absolute_deviation_fallback"
    if scale == 0:
        reason = "Baseline has no activity" if all(v == 0 for v in baseline) else "Baseline has no variation"
        return {"z": None, "method": None, "baseline_median": center, "scale": None, "reason": reason}
    z = ((observed - center) / scale).quantize(MONEY_PLACES)
    return {"z": z, "method": method, "baseline_median": center.quantize(AMOUNT_PLACES), "scale": scale.quantize(RATIO_PLACES), "reason": None}


def ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    return (numerator / denominator).quantize(RATIO_PLACES) if denominator else None


def hhi(amounts: list[Decimal]) -> Decimal | None:
    """Herfindahl-Hirschman index of counterparty shares (1/n = evenly spread, 1 = one counterparty)."""
    total = sum(amounts, Decimal(0))
    if not total:
        return None
    return sum(((a / total) ** 2 for a in amounts), Decimal(0)).quantize(RATIO_PLACES)


def _q4(value: Decimal | None) -> Decimal | None:
    return value.quantize(AMOUNT_PLACES) if value is not None else None


def ticket_stats(amounts: list[Decimal]) -> dict:
    return {
        "count": len(amounts),
        "median": _q4(median(amounts)),
        "p25": _q4(quantile(amounts, Decimal("0.25"))),
        "p75": _q4(quantile(amounts, Decimal("0.75"))),
        "p90": _q4(quantile(amounts, Decimal("0.9"))),
        "max": _q4(max(amounts)) if amounts else None,
    }


def _flow(rows: list[dict]) -> dict:
    inflow = [r for r in rows if r["_dir"] == "in"]
    outflow = [r for r in rows if r["_dir"] == "out"]
    in_amt = sum((r["_usd"] for r in inflow), Decimal(0))
    out_amt = sum((r["_usd"] for r in outflow), Decimal(0))
    return {
        "inflow_count": len(inflow),
        "inflow_amount": in_amt,
        "outflow_count": len(outflow),
        "outflow_amount": out_amt,
        "net_flow": in_amt - out_amt,
        "unique_counterparties": len({r["_cp"] for r in rows}),
    }


def compute_profile(account_id: str, txns: list[dict], as_of: datetime, observed_days: int = 7, baseline_days: int = 30) -> dict:
    """All behaviour metrics for one account from its transactions (any order, any time).

    Transactions after ``as_of`` are dropped here as well as in the query, so the cut-off holds even
    if a caller passes a wider set.
    """
    as_of = parse_ts(as_of)
    observed_start = as_of - timedelta(days=observed_days)
    baseline_start = observed_start - timedelta(days=baseline_days)
    lookback_days = max(30, observed_days + baseline_days)
    lookback_start = as_of - timedelta(days=lookback_days)

    rows, excluded_future, no_usd, self_transfers = [], 0, 0, 0
    for t in txns:
        ts = parse_ts(t["occurred_at"])
        if ts > as_of:
            excluded_future += 1
            continue
        if ts <= lookback_start:
            continue
        origin, dest = t["origin_account_id"], t["destination_account_id"]
        if origin == dest:
            self_transfers += 1
            continue
        if origin != account_id and dest != account_id:
            continue
        usd = usd_amount(t)
        if usd is None:
            no_usd += 1
            continue
        incoming = dest == account_id
        rows.append({**t, "_ts": ts, "_usd": usd, "_dir": "in" if incoming else "out", "_cp": origin if incoming else dest})
    rows.sort(key=lambda r: (r["_ts"], r["id"]))

    windows = {name: _flow([r for r in rows if r["_ts"] > as_of - span]) for name, span in FLOW_WINDOWS.items()}

    observed = [r for r in rows if r["_ts"] > observed_start]
    baseline = [r for r in rows if baseline_start < r["_ts"] <= observed_start]

    # Daily slots aligned to as_of so the observed and baseline days are measured the same way.
    def daily(selected: list[dict], start: datetime, days: int) -> tuple[list[Decimal], list[Decimal]]:
        volume, count = [Decimal(0)] * days, [Decimal(0)] * days
        for r in selected:
            # Slot k covers (start + k days, start + k+1 days], matching the window boundaries.
            slot = min(days - 1, max(0, math.ceil((r["_ts"] - start) / timedelta(days=1)) - 1))
            volume[slot] += r["_usd"]
            count[slot] += 1
        return volume, count

    base_volume, base_count = daily(baseline, baseline_start, baseline_days)
    obs_volume_mean = sum((r["_usd"] for r in observed), Decimal(0)) / observed_days
    obs_count_mean = Decimal(len(observed)) / observed_days
    baseline_flow, observed_flow = _flow(baseline), _flow(observed)

    known = {r["_cp"] for r in baseline}
    new_cps: dict[str, dict] = {}
    by_cp: dict[str, dict] = {}
    for r in rows:
        cp = by_cp.setdefault(r["_cp"], {"account_id": r["_cp"], "inflow_count": 0, "outflow_count": 0, "inflow_amount": Decimal(0),
                                           "outflow_amount": Decimal(0), "first_seen_at": r["occurred_at"], "last_seen_at": r["occurred_at"]})
        cp[f"{r['_dir']}flow_count"] += 1
        cp[f"{r['_dir']}flow_amount"] += r["_usd"]
        cp["last_seen_at"] = r["occurred_at"]
        if r["_ts"] > observed_start and r["_cp"] not in known and r["_cp"] not in new_cps:
            new_cps[r["_cp"]] = {"account_id": r["_cp"], "first_seen_at": r["occurred_at"], "direction": r["_dir"]}
    for cp in by_cp.values():
        cp["total_amount"] = cp["inflow_amount"] + cp["outflow_amount"]
        if cp["account_id"] in new_cps:
            cp["status"] = "new"
        elif cp["account_id"] in known:
            cp["status"] = "known"
        else:
            cp["status"] = "outside_windows"  # seen in the 30-day flow window but before the baseline window

    last_30 = [r for r in rows if r["_ts"] > as_of - FLOW_WINDOWS["30d"]]
    flow_30 = windows["30d"]
    cp_30: dict[str, Decimal] = defaultdict(Decimal)
    for r in last_30:
        cp_30[r["_cp"]] += r["_usd"]
    gaps = [Decimal(str((b["_ts"] - a["_ts"]).total_seconds())) / 3600 for a, b in zip(last_30, last_30[1:], strict=False)]
    top_share = ratio(max(cp_30.values()), sum(cp_30.values(), Decimal(0))) if cp_30 else None

    # Calendar-day series (UTC) across the whole lookback, for the chart.
    first_day: date = (lookback_start + timedelta(seconds=1)).date()
    series_days = (as_of.date() - first_day).days + 1
    series = {first_day + timedelta(days=i): {"inflow": Decimal(0), "outflow": Decimal(0), "count": 0} for i in range(series_days)}
    for r in rows:
        day = series[r["_ts"].date()]
        day["inflow" if r["_dir"] == "in" else "outflow"] += r["_usd"]
        day["count"] += 1

    return {
        "windows": {
            "as_of": as_of.isoformat(),
            "observed": {"days": observed_days, "start": observed_start.isoformat(), "end": as_of.isoformat()},
            "baseline": {"days": baseline_days, "start": baseline_start.isoformat(), "end": observed_start.isoformat()},
            "lookback_start": lookback_start.isoformat(),
            "flow_windows": list(FLOW_WINDOWS),
            "boundaries": "Each window is start-exclusive and end-inclusive. Amounts are in USD (amount_usd, or amount when the currency is USD).",
        },
        "flows": windows,
        "baseline_vs_observed": {
            "baseline": {**baseline_flow, "transactions": len(baseline), "daily_volume_median": _q4(median(base_volume)),
                         "daily_count_median": _q4(median(base_count)), "ticket": ticket_stats([r["_usd"] for r in baseline])},
            "observed": {**observed_flow, "transactions": len(observed), "daily_volume_mean": obs_volume_mean.quantize(MONEY_PLACES),
                         "daily_count_mean": obs_count_mean.quantize(RATIO_PLACES), "ticket": ticket_stats([r["_usd"] for r in observed])},
            "volume_z": robust_z(obs_volume_mean, base_volume),
            "count_z": robust_z(obs_count_mean, base_count),
            "statistic": "Mean daily USD volume (and count) in the observed window, scored against the baseline's daily values.",
        },
        "ratios": {
            "window": "30d",
            "inflow_outflow_ratio": ratio(flow_30["inflow_amount"], flow_30["outflow_amount"]),
            "pass_through_ratio": ratio(flow_30["outflow_amount"], flow_30["inflow_amount"]),
            "net_flow": flow_30["net_flow"],
            "retained_balance_proxy": ratio(flow_30["net_flow"], flow_30["inflow_amount"]),
            "retained_balance_note": "Share of 30-day inflow not sent back out (net flow / inflow). A proxy: opening balances are not known.",
            "inter_arrival_median_hours": median(gaps).quantize(MONEY_PLACES) if gaps else None,
            "inter_arrival_gaps": len(gaps),
            "counterparty_hhi": hhi(list(cp_30.values())),
            "top_counterparty_share": top_share,
        },
        "counterparties": sorted(by_cp.values(), key=lambda c: c["total_amount"], reverse=True),
        "new_counterparties": list(new_cps.values()),
        "daily_series": [{"date": d.isoformat(), **v} for d, v in series.items()],
        "counts": {
            "transactions_used": len(rows),
            "excluded_after_as_of": excluded_future,
            "excluded_no_usd_amount": no_usd,
            "excluded_self_transfers": self_transfers,
        },
    }


def rejected_rows_mentioning(account_ref: str, rows: list[dict], mappings: dict[str, dict]) -> list[dict]:
    """Rejected source rows whose mapped origin/destination column holds this account reference."""
    hits = []
    for row in rows:
        mapping = mappings.get(row["run_id"]) or {}
        raw = row.get("raw_data") or {}
        for side in ("origin_account", "destination_account"):
            column = mapping.get(side, side)
            if str(raw.get(column, "")).strip() == account_ref:
                hits.append({"source_row_id": row["id"], "run_id": row["run_id"], "row_number": row["row_number"], "side": side})
                break
    return hits


# ---------------------------------------------------------------- data access


def _transactions(tenant_id: str, account_id: str, start: datetime, as_of: datetime) -> tuple[list[dict], bool]:
    """Most recent transactions touching the account in (start, as_of], capped at MAX_TXNS."""
    db = get_db()
    rows: list[dict] = []
    page = 1000
    while len(rows) < MAX_TXNS:
        batch = (
            db.table("transactions").select(TXN_FIELDS).eq("tenant_id", tenant_id)
            .or_(f"origin_account_id.eq.{account_id},destination_account_id.eq.{account_id}")
            .gt("occurred_at", start.isoformat()).lte("occurred_at", as_of.isoformat())
            .order("occurred_at", desc=True).order("id", desc=True)
            .range(len(rows), min(len(rows) + page, MAX_TXNS) - 1).execute().data or []
        )
        rows.extend(batch)
        if len(batch) < page:
            return rows, False
    return rows, True


def _edge_activity(tenant_id: str, account_id: str, as_of: datetime, desc: bool) -> str | None:
    rows = (
        get_db().table("transactions").select("occurred_at").eq("tenant_id", tenant_id)
        .or_(f"origin_account_id.eq.{account_id},destination_account_id.eq.{account_id}")
        .lte("occurred_at", as_of.isoformat()).order("occurred_at", desc=desc).limit(1).execute().data
    )
    return rows[0]["occurred_at"] if rows else None


def _data_quality(tenant_id: str, account: dict, source_row_ids: list[str]) -> dict:
    db = get_db()
    issues = fetch_by_ids(
        "data_quality_issues", tenant_id, source_row_ids,
        "id, source_row_id, run_id, field_name, issue_type, severity, rule_id, message, status, source_rows(row_number, status)",
        id_column="source_row_id",
    )
    rejected = (
        db.table("source_rows").select("id, run_id, row_number, raw_data").eq("tenant_id", tenant_id).eq("status", "rejected")
        .order("created_at", desc=True).limit(MAX_REJECTED_ROWS_SCANNED).execute().data or []
    )
    runs = fetch_by_ids("ingestion_runs", tenant_id, {r["run_id"] for r in rejected}, "id, field_mapping")
    hits = rejected_rows_mentioning(account["account_ref"], rejected, {r["id"]: r.get("field_mapping") or {} for r in runs})
    rejected_issues = fetch_by_ids(
        "data_quality_issues", tenant_id, [h["source_row_id"] for h in hits],
        "id, source_row_id, rule_id, issue_type, severity, message", id_column="source_row_id",
    )
    by_row: dict[str, list[dict]] = defaultdict(list)
    for issue in rejected_issues:
        by_row[issue["source_row_id"]].append(issue)
    return {
        "transaction_issues": sorted(issues, key=lambda i: (i.get("source_rows") or {}).get("row_number") or 0),
        "rejected_rows": [{**h, "issues": by_row.get(h["source_row_id"], [])} for h in hits],
        "rejected_rows_scanned": len(rejected),
        "rejected_scan_capped": len(rejected) >= MAX_REJECTED_ROWS_SCANNED,
        "note": "Rejected rows never became transactions, so activity they describe is missing from these metrics.",
    }


def _related(tenant_id: str, account_id: str) -> dict:
    db = get_db()
    links = (
        db.table("alert_accounts").select("role, alerts(id, alert_number, typology, priority_band, status, created_at)")
        .eq("tenant_id", tenant_id).eq("account_id", account_id).limit(200).execute().data or []
    )
    alerts = [{**link["alerts"], "role": link["role"]} for link in links if link.get("alerts")]
    case_links = fetch_by_ids("case_alerts", tenant_id, [a["id"] for a in alerts], "alert_id, cases(id, case_number, title, status, priority_band)", id_column="alert_id")
    cases = {c["cases"]["id"]: c["cases"] for c in case_links if c.get("cases")}
    return {
        "alerts": sorted(alerts, key=lambda a: a["created_at"], reverse=True),
        "cases": sorted(cases.values(), key=lambda c: c["case_number"], reverse=True),
    }


def account_profile(tenant_id: str, account: dict, as_of: datetime, observed_days: int, baseline_days: int) -> dict:
    as_of = parse_ts(as_of)
    lookback_days = max(30, observed_days + baseline_days)
    txns, truncated = _transactions(tenant_id, account["id"], as_of - timedelta(days=lookback_days), as_of)
    metrics = compute_profile(account["id"], txns, as_of, observed_days, baseline_days)

    cp_ids = [c["account_id"] for c in metrics["counterparties"][:MAX_LISTED_COUNTERPARTIES]] + [n["account_id"] for n in metrics["new_counterparties"]]
    refs = {a["id"]: a for a in fetch_by_ids("accounts", tenant_id, cp_ids, "id, account_ref, institution_ref, is_internal")}
    listed = metrics["counterparties"][:MAX_LISTED_COUNTERPARTIES]
    for item in listed + metrics["new_counterparties"]:
        item["account"] = refs.get(item["account_id"])

    first_activity = _edge_activity(tenant_id, account["id"], as_of, desc=False)
    baseline_start = parse_ts(metrics["windows"]["baseline"]["start"])
    history = []
    if truncated:
        history.append(f"Only the most recent {MAX_TXNS} transactions were read; older baseline activity is missing.")
    if first_activity is None:
        history.append("No transactions at or before as_of.")
    elif parse_ts(first_activity) > baseline_start:
        history.append("The account's first transaction falls inside the baseline or observed window, so the baseline is short.")

    return {
        "account": account,
        **metrics,
        "counterparties": listed,
        "counterparties_total": len(metrics["counterparties"]),
        "activity": {"first_at": first_activity, "last_at": _edge_activity(tenant_id, account["id"], as_of, desc=True)},
        "limits": {"max_transactions": MAX_TXNS, "truncated": truncated, "transactions_read": len(txns), "history_notes": history},
        "related": _related(tenant_id, account["id"]),
        "data_quality": _data_quality(tenant_id, account, sorted({t["source_row_id"] for t in txns if t.get("source_row_id")})),
        "not_available": {
            "expected_activity": "No KYC expected-activity profile is stored for accounts.",
            "geography": None if account.get("jurisdiction") else "No jurisdiction recorded for this account.",
            "shared_tokens": "Device, IP and other shared-token data are not ingested, so token links cannot be shown.",
        },
    }
