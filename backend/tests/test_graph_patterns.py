"""Bounded graph analytics (cycles, fan-in/out, pass-through) and the workbench routes' caps."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.security import ANY_CALLER, CurrentUser
from app.main import app
from app.services import graph as g


def edge(eid, src, dst, ts, amount, channel="wire"):
    return {"id": eid, "transaction_ref": eid, "origin_account_id": src, "destination_account_id": dst,
            "occurred_at": ts, "amount": amount, "amount_usd": amount, "currency": "USD", "channel": channel}


CYCLE = [
    edge("c1", "X", "Y", "2026-10-06T14:00:00+00:00", 15000),
    edge("c2", "Y", "Z", "2026-10-06T18:30:00+00:00", 14800),
    edge("c3", "Z", "X", "2026-10-07T09:10:00+00:00", 14650),
    edge("c0", "Z", "X", "2026-10-05T09:00:00+00:00", 100),  # before c2: not a time-respecting return
]


def test_finds_seeded_cycle_with_exact_edges_and_delta():
    found, stats = g.find_cycles(CYCLE, "X", 3, 6, True, timedelta(hours=72), 10)
    assert [f["transaction_ids"] for f in found] == [["c1", "c2", "c3"]]
    cyc = found[0]
    assert cyc["account_ids"] == ["X", "Y", "Z", "X"]
    assert cyc["amount_delta_usd"] == "350.00"
    assert cyc["elapsed_hours"] == 19.17
    assert not stats["budget_exhausted"]


def test_structural_cycles_ignore_time_order():
    found, _ = g.find_cycles(CYCLE, "X", 3, 6, False, timedelta(hours=72), 10)
    assert sorted(f["transaction_ids"][-1] for f in found) == ["c0", "c3"]


def test_cycle_hop_limits_are_respected():
    assert g.find_cycles(CYCLE, "X", 4, 6, True, timedelta(hours=72), 10)[0] == []
    loop = [edge(f"l{i}", f"N{i}", f"N{(i + 1) % 8}", f"2026-10-01T{10 + i:02d}:00:00+00:00", 1000) for i in range(8)]
    assert g.find_cycles(loop, "N0", 3, 7, True, timedelta(hours=72), 10)[0] == []
    assert len(g.find_cycles(loop, "N0", 3, 8, True, timedelta(hours=72), 10)[0]) == 1


def test_cycle_search_is_bounded_by_budget():
    dense, n = [], 0
    for a in range(8):
        for b in range(8):
            if a != b:
                n += 1
                dense.append(edge(f"d{n}", f"K{a}", f"K{b}", "2026-10-01T10:00:00+00:00", 10))
    # Millions of simple cycles exist; the expansion budget stops the search, not the result limit.
    found, stats = g.find_cycles(dense, "K0", 3, 10, False, timedelta(hours=72), 10**6, budget=200)
    assert stats["budget_exhausted"] and stats["expansions"] == 200
    assert 0 < len(found) < 10**6
    assert len(g.find_cycles(dense, "K0", 3, 10, False, timedelta(hours=72), 5)[0]) == 5


def test_fan_out_finds_counterparties_in_window():
    edges = [
        edge("f1", "P", "A1", "2026-10-05T09:00:00+00:00", 1200),
        edge("f2", "P", "A2", "2026-10-05T09:00:00+00:00", 1250),
        edge("f3", "P", "A3", "2026-10-05T10:00:00+00:00", 1180),
        edge("f4", "P", "A4", "2026-10-20T10:00:00+00:00", 999),  # outside the span
    ]
    found = g.find_fan(edges, "P", "fan_out", 3, timedelta(hours=72), 5)
    assert len(found) == 1
    assert found[0]["transaction_ids"] == ["f1", "f2", "f3"]
    assert found[0]["counterparty_count"] == 3 and found[0]["total_amount_usd"] == "3630.00"
    assert g.find_fan(edges, "P", "fan_out", 4, timedelta(hours=72), 5) == []
    assert g.find_fan(edges, "P", "fan_in", 2, timedelta(hours=72), 5) == []


def test_fan_in_counts_distinct_senders_not_repeat_deposits():
    deposits = [edge(f"s{i}", "CASH", "R", f"2026-10-03T1{i}:00:00+00:00", 9800, "cash") for i in range(4)]
    assert g.find_fan(deposits, "R", "fan_in", 2, timedelta(hours=72), 5) == []
    deposits.append(edge("s9", "OTHER", "R", "2026-10-03T19:00:00+00:00", 9000, "cash"))
    found = g.find_fan(deposits, "R", "fan_in", 2, timedelta(hours=72), 5)
    assert found[0]["counterparty_count"] == 2 and found[0]["transaction_count"] == 5


def test_pass_through_matches_inbound_with_following_outbound():
    edges = [
        edge("p1", "S", "M", "2026-10-01T09:05:00+00:00", 48500),
        edge("p2", "M", "T", "2026-10-01T11:40:00+00:00", 47900),
        edge("p3", "M", "T", "2026-10-01T08:00:00+00:00", 47900),  # before the inbound transfer
    ]
    found = g.find_pass_through(edges, "M", timedelta(hours=24), 0.8, 5)
    assert [f["transaction_ids"] for f in found] == [["p1", "p2"]]
    assert found[0]["amount_delta_usd"] == "600.00" and found[0]["pass_through_ratio"] == 0.9876
    assert g.find_pass_through(edges, "M", timedelta(hours=1), 0.8, 5) == []


def test_time_respecting_paths_report_delta_and_gap():
    chain = [
        edge("t1", "A", "B", "2026-07-01T10:00:00+00:00", 50000),
        edge("t2", "B", "C", "2026-07-01T14:00:00+00:00", 49000),
        edge("t3", "C", "D", "2026-07-02T09:00:00+00:00", 48000),
    ]
    paths = g.time_respecting_paths(chain, "A", "D", 4, timedelta(hours=24), 0.0, 10)
    assert paths[0]["amount_delta_usd"] == "2000.00" and paths[0]["max_gap_hours"] == 19.0


def test_window_cap():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    g.check_window(start, start + timedelta(days=30), g.MAX_PATTERN_WINDOW)
    for bad_end in (start + timedelta(days=40), start - timedelta(days=1)):
        try:
            g.check_window(start, bad_end, g.MAX_PATTERN_WINDOW)
        except g.GraphLimitError:
            continue
        raise AssertionError("expected GraphLimitError")


# --- Route validation (no database) ----------------------------------------------------------------

TENANT = "11111111-1111-1111-1111-111111111111"
ACCOUNT = "22222222-2222-2222-2222-222222222222"


def _client():
    app.dependency_overrides[ANY_CALLER] = lambda: CurrentUser(id="u1", tenant_id=TENANT, role="analyst")
    return TestClient(app)


def teardown_function():
    app.dependency_overrides.clear()


def test_pattern_search_requires_auth():
    response = TestClient(app).post("/graph/patterns/search", json={"operation": "cycles", "account_id": ACCOUNT})
    assert response.status_code == 401


def test_pattern_search_rejects_over_limit_requests():
    client = _client()
    base = {"operation": "cycles", "account_id": ACCOUNT}
    for bad in (
        {"max_hops": 11},
        {"min_hops": 1},
        {"min_hops": 6, "max_hops": 4},
        {"limit": 500},
        {"start": "2026-01-01T00:00:00Z", "end": "2026-03-01T00:00:00Z"},
        {"start": "2026-01-01T00:00:00"},  # timezone required
        {"operation": "raw_query"},
        {"query": "MATCH (n) RETURN n"},  # no free-text queries
        {"channels": ["carrier pigeon"]},
    ):
        response = client.post("/graph/patterns/search", json={**base, **bad})
        assert response.status_code == 422, bad


def test_neighborhood_and_paths_reject_over_limit_requests():
    client = _client()
    assert client.get(f"/graph/accounts/{ACCOUNT}/neighborhood?hops=4").status_code == 422
    assert client.get(f"/graph/accounts/{ACCOUNT}/neighborhood?direction=sideways").status_code == 422
    assert client.get(f"/graph/accounts/{ACCOUNT}/neighborhood?max_nodes=5000").status_code == 422
    assert client.get(f"/graph/accounts/{ACCOUNT}/neighborhood?channel=pigeon").status_code == 422
    long_window = "start=2024-01-01T00:00:00Z&end=2026-01-01T00:00:00Z"
    assert client.get(f"/graph/accounts/{ACCOUNT}/neighborhood?{long_window}").status_code == 422
    assert client.get(f"/graph/paths?source_account_id={ACCOUNT}&max_hops=7").status_code == 422
    assert client.get("/graph/accounts?limit=1000").status_code == 422


def test_pattern_search_runs_typed_operation():
    client = _client()
    with patch("app.api.routes.graph.get_one", return_value={"id": ACCOUNT}), \
         patch("app.services.graph._edges_touching", return_value=[]) as touching:
        response = client.post("/graph/patterns/search", json={
            "operation": "fan_out", "account_id": ACCOUNT,
            "start": "2026-10-01T00:00:00Z", "end": "2026-10-08T00:00:00Z",
        })
    assert response.status_code == 200
    body = response.json()
    assert body["findings"] == [] and body["complete"] is False
    assert body["limitation"].startswith("Bounded search, not complete enumeration")
    assert touching.call_args.args[4] == "outgoing"
