"""GraphBase engine (ml/graph): positive and denial/limit tests per PRD v3.3 22.1."""
import pytest

from ml.graph import (GraphLimits, LimitExceeded, NetworkXGraphAdapter, NotFound, find_cycles, find_motifs,
                      propagation_leads)

T0 = "2026-07-01T"


def tx(tid, o, d, ts, amount=1000.0, **kw):
    return {"transaction_id": tid, "origin_account_id": o, "destination_account_id": d, "amount": amount,
            "currency": "USD", "occurred_at": ts, **kw}


CYCLE = [  # A->B->C->A twice, time-respecting; amounts shrink (fees)
    tx("c1", "A", "B", T0 + "10:00:00+00:00", 10000), tx("c2", "B", "C", T0 + "12:00:00+00:00", 9800),
    tx("c3", "C", "A", T0 + "15:00:00+00:00", 9600),
    tx("c4", "A", "B", "2026-07-02T10:00:00+00:00", 9000), tx("c5", "B", "C", "2026-07-02T11:00:00+00:00", 8900),
    tx("c6", "C", "A", "2026-07-02T13:00:00+00:00", 8800),
]


@pytest.fixture
def g():
    a = NetworkXGraphAdapter()
    a.upsert_transactions("t1", CYCLE + [tx("x1", "C", "D", T0 + "16:00:00+00:00", 500), tx("x0", "D", "E", T0 + "09:00:00+00:00", 50)])
    return a


# --- writes ------------------------------------------------------------------
def test_upsert_is_idempotent_and_rejects_bad_rows(g):
    r = g.upsert_transactions("t1", [tx("c1", "A", "B", T0 + "10:00:00+00:00", 10000),
                                     {"transaction_id": "bad", "amount": "NaN?"}, tx("neg", "A", "B", T0 + "10:00:00", -5)])
    assert r["accepted"] == 1 and {x["transaction_id"] for x in r["rejected"]} == {"bad", "neg"}
    assert g.health_check()["edges"] == 8  # c1 replaced, not duplicated


def test_entities_and_relationships_validated(g):
    r = g.upsert_entities("t1", [{"entity_id": "CU1", "type": "Customer"}, {"entity_id": "X", "type": "Spaceship"}],
                          [{"type": "OWNS", "source_id": "CU1", "target_id": "A"}, {"type": "OWNS", "source_id": "CU1", "target_id": "ZZ"}])
    assert r["accepted_entities"] == 1 and r["accepted_relationships"] == 1 and len(r["rejected"]) == 2
    nb = g.get_neighborhood("t1", "A", hops=1)
    assert nb["relationships"][0]["type"] == "OWNS"


# --- tenant scope and caps -----------------------------------------------------
def test_tenant_isolation(g):
    g.upsert_transactions("t2", [tx("z1", "Q", "R", T0 + "10:00:00+00:00")])
    with pytest.raises(NotFound):
        g.get_neighborhood("t2", "A")
    with pytest.raises(NotFound):
        g.get_neighborhood("nope", "A")


def test_hop_and_range_caps_refuse(g):
    with pytest.raises(LimitExceeded):
        g.get_neighborhood("t1", "A", hops=99)
    with pytest.raises(LimitExceeded):
        g.get_neighborhood("t1", "A", start="2026-01-01T00:00:00+00:00", end="2026-12-31T00:00:00+00:00")


def test_node_cap_truncates_with_reason():
    a = NetworkXGraphAdapter(GraphLimits(max_nodes=3))
    a.upsert_transactions("t", [tx(f"f{i}", f"S{i}", "HUB", T0 + f"1{i}:00:00+00:00") for i in range(6)])
    nb = a.get_neighborhood("t", "HUB", hops=1)
    assert nb["truncated"] and nb["truncation_reason"] == "max_nodes" and len(nb["nodes"]) == 3


# --- neighbourhood, tracing, paths ----------------------------------------------
def test_neighborhood_envelope(g):
    nb = g.get_neighborhood("t1", "C", hops=1)
    assert {n["entity_id"] for n in nb["nodes"]} == {"A", "B", "C", "D"}
    assert nb["status"] == "ok" and nb["graph_version"] >= 1 and "as_of" in nb and nb["limits"]["max_hops"] == 6


def test_trace_funds_respects_time_order(g):
    tr = g.trace_funds("t1", "C", direction="downstream", hops=3)
    used = {e["transaction_id"] for e in tr["edges"]}
    assert "x1" in used and "x0" not in used  # D->E happened before funds reached D
    assert tr["caveats"]


def test_trace_reaches_deep_hops():
    a = NetworkXGraphAdapter()  # a 5-hop chain plus noise at the seed, so the heap counter grows past `hops`
    a.upsert_transactions("t", [tx(f"n{i}", "S", f"N{i}", T0 + f"0{i}:00:00+00:00") for i in range(8)] +
                          [tx(f"h{i}", "S" if i == 0 else f"H{i}", f"H{i + 1}", f"2026-07-0{i + 2}T10:00:00+00:00") for i in range(5)])
    tr = a.trace_funds("t", "S", hops=5)
    assert {"h0", "h1", "h2", "h3", "h4"} <= {e["transaction_id"] for e in tr["edges"]}
    assert {n["account_id"]: n["hop"] for n in tr["nodes"]}["H5"] == 5


def test_trace_funds_rejects_bad_direction(g):
    with pytest.raises(LimitExceeded):
        g.trace_funds("t1", "A", direction="sideways")


def test_time_respecting_paths_via_adapter(g):
    r = g.find_time_respecting_paths("t1", "A", "D", hops=4, max_gap_hours=24)
    assert [h["transaction_id"] for h in r["items"][0]["hops"]] == ["c1", "c2", "x1"]
    with pytest.raises(LimitExceeded):
        g.find_time_respecting_paths("t1", "A", "D", limit=10_000)


# --- cycles and motifs -----------------------------------------------------------
def test_bounded_cycle_with_repetition_and_amount_delta():
    cyc = find_cycles(CYCLE, min_hops=3, max_hops=5)
    assert len(cyc) == 1
    c = cyc[0]
    assert c["accounts"] == ["A", "B", "C"] and c["account_cycle"] == ["A", "B", "C", "A"]
    assert c["edge_ids"] == ["c1", "c2", "c3"] and c["repetition_count"] == 2
    assert c["elapsed_hours"] == 5.0 and c["amount_delta"] == -400.0


def test_cycle_must_be_time_respecting():
    backwards = [tx("b1", "A", "B", T0 + "15:00:00+00:00"), tx("b2", "B", "C", T0 + "12:00:00+00:00"),
                 tx("b3", "C", "A", T0 + "10:00:00+00:00")]
    # each rotation needs strictly increasing times: C->A(10) then A->B(15) then B->C(12) fails, etc.
    assert find_cycles(backwards, min_hops=3, max_hops=5) == []


def test_cycle_limits():
    with pytest.raises(ValueError):
        find_cycles(CYCLE, min_hops=3, max_hops=11)
    stats = {}
    find_cycles(CYCLE, min_hops=3, max_hops=5, timeout_s=0.0, stats=stats)
    assert stats["truncated"] and stats["truncation_reason"] == "timeout" and "not a complete" in stats["caveat"]


def test_adapter_cycles_scoped_and_tenant_wide_cap(g):
    r = g.find_cycles_bounded("t1", account_id="B", min_hops=3, max_hops=4)
    assert r["items"][0]["repetition_count"] == 2 and "not a complete enumeration" in r["caveats"][0]
    small = NetworkXGraphAdapter(GraphLimits(max_edges=3))
    small.upsert_transactions("t1", CYCLE)
    with pytest.raises(LimitExceeded):
        small.find_cycles_bounded("t1")


def test_fan_in_and_fan_out_windows():
    fan = [tx(f"i{i}", f"S{i}", "M", f"2026-07-01T{10 + i}:00:00+00:00", 2000) for i in range(4)]
    fan += [tx("late", "S9", "M", "2026-07-20T10:00:00+00:00", 2000)]  # outside the 72h window
    fan += [tx(f"o{i}", "M", f"R{i}", f"2026-07-01T{16 + i}:00:00+00:00", 1900) for i in range(3)]
    fi = find_motifs("fan_in", fan, min_branches=3, window_hours=72)
    assert fi[0]["accounts"][-1] == "M" and fi[0]["branches"] == 4 and "late" not in fi[0]["edge_ids"]
    fo = find_motifs("fan_out", fan, min_branches=3)
    assert fo[0]["accounts"][0] == "M" and fo[0]["branches"] == 3
    assert find_motifs("fan_in", fan, min_branches=5) == []


def test_split_merge():
    sm = []
    for i, m in enumerate(["M1", "M2", "M3"]):
        sm += [tx(f"s{i}", "SRC", m, f"2026-07-01T1{i}:00:00+00:00", 3000), tx(f"m{i}", m, "SINK", f"2026-07-01T2{i}:00:00+00:00", 2950)]
    sm += [tx("slow", "M3", "SINK", "2026-07-30T10:00:00+00:00", 10)]  # merge leg too late to count
    out = find_motifs("split_merge", sm, min_branches=3, window_hours=24)
    assert len(out) == 1 and out[0]["accounts"][0] == "SRC" and out[0]["accounts"][-1] == "SINK"
    assert out[0]["branches"] == 3 and out[0]["start_amount"] == 9000 and out[0]["end_amount"] == 8850
    with pytest.raises(ValueError):
        find_motifs("triangle", sm)


# --- metrics and snapshots -------------------------------------------------------
def test_graph_metrics(g):
    m = g.get_graph_metrics("t1", account_ids=["A"])
    a = m["items"][0]
    assert a["out_degree"] == 2 and a["in_degree"] == 2 and a["weighted_out"] == 19000.0
    assert a["k_core"] >= 2 and 0 < a["pagerank"] < 1 and m["summary"]["components"] == 1
    small = NetworkXGraphAdapter(GraphLimits(max_edges=2))
    small.upsert_transactions("t", CYCLE)
    with pytest.raises(LimitExceeded):
        small.get_graph_metrics("t")


def test_snapshot_is_content_addressed_and_comparable(g):
    s1 = g.get_graph_snapshot("t1", account_id="A", hops=1)
    s1b = g.get_graph_snapshot("t1", account_id="A", hops=1)
    assert s1["snapshot_id"] == s1b["snapshot_id"] and len(s1["content_hash"]) == 64
    g.upsert_transactions("t1", [tx("new", "A", "Z", "2026-07-03T10:00:00+00:00")])
    s2 = g.get_graph_snapshot("t1", account_id="A", hops=1)
    assert s2["snapshot_id"] != s1["snapshot_id"]
    diff = g.compare_snapshots("t1", s1["snapshot_id"], s2["snapshot_id"])
    assert diff["added_edges"] == ["new"] and not diff["same_content"]
    assert g.get_graph_snapshot("t1", snapshot_id=s1["snapshot_id"])["content_hash"] == s1["content_hash"]
    with pytest.raises(NotFound):
        g.get_graph_snapshot("t1", snapshot_id="gs_missing")
    g.upsert_transactions("t2", [tx("q", "Q", "R", T0 + "10:00:00+00:00")])
    with pytest.raises(NotFound):  # snapshots are tenant-scoped
        g.get_graph_snapshot("t2", snapshot_id=s1["snapshot_id"])


# --- propagation ------------------------------------------------------------------
def test_propagation_creates_review_leads_not_labels(g):
    leads = propagation_leads(g, "t1", "C", "escalated", case_id="case-1", hops=1)
    by = {l["account_id"]: l for l in leads}
    assert set(by) == {"A", "B", "D"} and all(l["lead_type"] == "review_lead" for l in leads)
    assert "case-1" in by["D"]["reason"] and "not a finding" in by["D"]["reason"]
    assert by["D"]["via_transaction_ids"] == ["x1"] and by["D"]["seed"]["disposition"] == "escalated"


def test_propagation_denials(g):
    assert propagation_leads(g, "t1", "C", "false_positive") == []
    with pytest.raises(LimitExceeded):
        propagation_leads(g, "t1", "C", "escalated", hops=3)
    with pytest.raises(NotFound):
        propagation_leads(g, "t1", "NOPE", "escalated")


def test_health_check(g):
    h = g.health_check()
    assert h["status"] == "ok" and h["provider"] == "networkx-inmemory" and h["edges"] == 8
