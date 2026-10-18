from datetime import timedelta

from app.services.graph import time_respecting_paths


def edge(eid, src, dst, ts, amount):
    return {"id": eid, "transaction_ref": eid, "origin_account_id": src, "destination_account_id": dst,
            "occurred_at": ts, "amount": amount, "amount_usd": amount, "currency": "USD"}


EDGES = [
    edge("t1", "A", "B", "2026-07-01T10:00:00+00:00", 50000),
    edge("t2", "B", "C", "2026-07-01T14:00:00+00:00", 49000),
    edge("t3", "C", "D", "2026-07-02T09:00:00+00:00", 48000),
    edge("t0", "B", "C", "2026-06-30T09:00:00+00:00", 49000),  # before t1: must not be chained
    edge("t4", "C", "D", "2026-07-20T09:00:00+00:00", 48000),  # gap too large
]


def test_finds_time_respecting_path():
    paths = time_respecting_paths(EDGES, "A", "D", 4, timedelta(hours=24), 0.0, 10)
    assert [[h["transaction_id"] for h in p["hops"]] for p in paths] == [["t1", "t2", "t3"]]
    assert paths[0]["hops"][1]["gap_hours"] == 4.0


def test_amount_continuity_filter():
    edges = EDGES[:2] + [edge("t3", "C", "D", "2026-07-02T09:00:00+00:00", 100)]
    assert time_respecting_paths(edges, "A", "D", 4, timedelta(hours=24), 0.8, 10) == []


def test_open_ended_search_respects_max_hops():
    paths = time_respecting_paths(EDGES, "A", None, 2, timedelta(hours=24), 0.0, 10)
    assert all(p["length"] <= 2 for p in paths)
    assert any(p["length"] == 2 for p in paths)


def test_unreachable_target_is_bounded(monkeypatch):
    import app.services.graph as g

    monkeypatch.setattr(g, "MAX_EXPANSIONS", 500)
    # Dense layered graph with no path to the target.
    edges, n = [], 0
    for layer in range(5):
        for a in range(6):
            for b in range(6):
                n += 1
                edges.append(edge(f"e{n}", f"L{layer}_{a}", f"L{layer + 1}_{b}", f"2026-07-0{layer + 1}T10:00:00+00:00", 1000))
    edges = [dict(e, origin_account_id="SRC") if e["origin_account_id"].startswith("L0_") else e for e in edges]
    assert g.time_respecting_paths(edges, "SRC", "NOWHERE", 6, timedelta(days=30), 0.0, 10) == []
