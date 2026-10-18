from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.entity_profile import compute_profile, hhi, quantile, rejected_rows_mentioning, robust_z

ACC = "acc-focus"
AS_OF = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def txn(tid, ts, amount, origin, dest, currency="USD", amount_usd=None):
    return {"id": tid, "transaction_ref": tid, "occurred_at": ts, "amount": amount, "amount_usd": amount_usd,
            "currency": currency, "channel": "cash", "origin_account_id": origin, "destination_account_id": dest, "source_row_id": f"row-{tid}"}


# Baseline (Sep 3 12:00 – Oct 3 12:00): a known payer and payee. Observed (Oct 3 12:00 – Oct 10 12:00):
# four near-threshold deposits from a new counterparty, one outbound wire. One transaction after as_of.
TXNS = [
    txn("b1", "2026-09-10T10:00:00Z", "1000.00", "known-payer", ACC),
    txn("b2", "2026-09-20T10:00:00Z", "500.00", ACC, "known-payee"),
    txn("o1", "2026-10-05T10:00:00Z", "9800.00", "new-payer", ACC),
    txn("o2", "2026-10-05T13:00:00Z", "9750.00", "new-payer", ACC),
    txn("o3", "2026-10-06T09:00:00Z", "9900.00", "new-payer", ACC),
    txn("o4", "2026-10-06T16:00:00Z", "9600.00", "new-payer", ACC),
    txn("o5", "2026-10-07T10:00:00Z", "38500.00", ACC, "known-payee"),
    txn("f1", "2026-10-11T10:00:00Z", "99999.00", "new-payer", ACC),  # after as_of
]


def test_quantile_and_hhi_are_exact():
    values = [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")]
    assert quantile(values, Decimal("0.5")) == Decimal("2.5")
    assert quantile(values, Decimal("0.25")) == Decimal("1.75")
    assert quantile([], Decimal("0.5")) is None
    assert hhi([Decimal("50"), Decimal("50")]) == Decimal("0.5000")
    assert hhi([Decimal("10")]) == Decimal("1.0000")


def test_robust_z_uses_mad_then_fallback():
    base = [Decimal(v) for v in ("10", "12", "11", "13", "9")]
    z = robust_z(Decimal("20"), base)
    # median 11, |dev| = 1,1,0,2,2 -> MAD 1 -> scale 1.4826 -> (20-11)/1.4826 = 6.07
    assert z["z"] == Decimal("6.07") and z["method"] == "median_absolute_deviation"
    sparse = [Decimal(0)] * 9 + [Decimal(100)]
    fallback = robust_z(Decimal(50), sparse)
    assert fallback["method"] == "mean_absolute_deviation_fallback"
    # mean |dev| = 10 -> scale 12.533 -> 50/12.533 = 3.99
    assert fallback["z"] == Decimal("3.99")
    empty = robust_z(Decimal(5), [Decimal(0)] * 30)
    assert empty["z"] is None and empty["reason"] == "Baseline has no activity"


def test_as_of_excludes_future_transactions():
    p = compute_profile(ACC, TXNS, AS_OF)
    assert p["counts"]["excluded_after_as_of"] == 1
    assert p["counts"]["transactions_used"] == 7
    assert all(cp["inflow_amount"] < Decimal("99999") for cp in p["counterparties"])
    # Moving as_of back before the observed deposits removes them entirely.
    earlier = compute_profile(ACC, TXNS, datetime(2026, 10, 1, tzinfo=UTC))
    assert earlier["counts"]["transactions_used"] == 2
    assert earlier["new_counterparties"] == []


def test_flow_windows_and_ratios():
    p = compute_profile(ACC, TXNS, AS_OF)
    w7 = p["flows"]["7d"]
    assert (w7["inflow_count"], w7["inflow_amount"]) == (4, Decimal("39050.00"))
    assert (w7["outflow_count"], w7["outflow_amount"]) == (1, Decimal("38500.00"))
    assert w7["net_flow"] == Decimal("550.00")
    assert p["flows"]["24h"]["inflow_count"] == 0
    r = p["ratios"]
    # 30-day window (Sep 10 12:00 – Oct 10 12:00) excludes b1: in 39050, out 500 + 38500 = 39000.
    assert r["inflow_outflow_ratio"] == Decimal("1.0013")
    assert r["net_flow"] == Decimal("50.00")
    assert r["retained_balance_proxy"] == Decimal("0.0013")
    # Counterparty shares: new-payer 39050 and known-payee 39000 of 78050.
    assert r["counterparty_hhi"] == Decimal("0.5000")
    assert r["top_counterparty_share"] == Decimal("0.5003")
    assert r["inter_arrival_gaps"] == 5
    # Gaps (h): b2->o1 360, then 3, 20, 7, 18 -> median 18.
    assert r["inter_arrival_median_hours"] == Decimal("18.00")


def test_first_seen_counterparties_relative_to_baseline():
    p = compute_profile(ACC, TXNS, AS_OF)
    assert [n["account_id"] for n in p["new_counterparties"]] == ["new-payer"]
    assert p["new_counterparties"][0]["first_seen_at"] == "2026-10-05T10:00:00Z"
    status = {c["account_id"]: c["status"] for c in p["counterparties"]}
    assert status == {"new-payer": "new", "known-payee": "known", "known-payer": "known"}


def test_baseline_vs_observed_z_score():
    p = compute_profile(ACC, TXNS, AS_OF)
    bvo = p["baseline_vs_observed"]
    assert bvo["observed"]["transactions"] == 5 and bvo["baseline"]["transactions"] == 2
    # Observed mean daily volume = 77550 / 7 = 11078.57; baseline days are 28 zeros, 1000 and 500.
    assert bvo["observed"]["daily_volume_mean"] == Decimal("11078.57")
    z = bvo["volume_z"]
    assert z["method"] == "mean_absolute_deviation_fallback" and z["baseline_median"] == 0
    assert z["z"] > 100
    assert bvo["observed"]["ticket"]["median"] == Decimal("9800.00")


def test_daily_series_covers_lookback_and_skips_other_rows():
    noise = [txn("x", "2026-10-05T10:00:00Z", "5", "a", "b"), txn("self", "2026-10-05T10:00:00Z", "5", ACC, ACC),
             txn("eur", "2026-10-05T10:00:00Z", "5", "a", ACC, currency="EUR")]
    p = compute_profile(ACC, TXNS + noise, AS_OF)
    assert p["counts"]["excluded_self_transfers"] == 1 and p["counts"]["excluded_no_usd_amount"] == 1
    days = {d["date"]: d for d in p["daily_series"]}
    assert days["2026-10-05"]["inflow"] == Decimal("19550.00") and days["2026-10-05"]["count"] == 2
    assert p["daily_series"][-1]["date"] == "2026-10-10"
    assert len(p["daily_series"]) == 38  # 37-day lookback spans 38 calendar days


def test_rejected_rows_matched_through_the_run_mapping():
    rows = [{"id": "r1", "run_id": "run", "row_number": 4, "raw_data": {"from": "ACC-1", "to": "ACC-2"}},
            {"id": "r2", "run_id": "run", "row_number": 5, "raw_data": {"from": "ACC-3", "to": "ACC-4"}}]
    hits = rejected_rows_mentioning("ACC-2", rows, {"run": {"origin_account": "from", "destination_account": "to"}})
    assert hits == [{"source_row_id": "r1", "run_id": "run", "row_number": 4, "side": "destination_account"}]


client = TestClient(app)


def test_profile_requires_auth_and_validates_input():
    assert client.get("/accounts/11111111-1111-1111-1111-111111111111/profile").status_code == 401
    headers = {"X-Service-Key": "test-worker-key", "X-Tenant-Id": "11111111-1111-1111-1111-111111111111"}
    from tests.test_api import _service_db

    with patch("app.core.security.get_db", return_value=_service_db()):
        assert client.get("/accounts/not-a-uuid/profile", headers=headers).status_code == 422
        assert client.get("/accounts/11111111-1111-1111-1111-111111111111/profile?observed_days=90", headers=headers).status_code == 422
        with patch("app.api.routes.entity_profile.get_one", return_value=None):
            assert client.get("/accounts/11111111-1111-1111-1111-111111111111/profile", headers=headers).status_code == 404
