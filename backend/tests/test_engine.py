"""Engine checks (PRD §5): one positive and one denial/limit case per module."""
import numpy as np
import pandas as pd
import pytest

from ml.engine.baselines import account_baselines
from ml.engine.detectors import run_detectors
from ml.engine.fusion import POLICY, cluster, fuse
from ml.engine.manifest import at_k, run_manifest, timed
from ml.engine.resolution import resolve

T0 = pd.Timestamp("2026-01-01", tz="UTC")


def tx_frame(rows):
    return pd.DataFrame(rows, columns=["transaction_id", "timestamp", "amount", "origin_account_id", "destination_account_id"])


def test_baselines_flag_outlier_and_ignore_same_second_history():
    rows = [(f"t{i}", T0 + pd.Timedelta(hours=i), 100.0, "A", f"B{i}") for i in range(20)]
    rows.append(("big", T0 + pd.Timedelta(hours=21), 50_000.0, "A", "Z"))
    rows.append(("twin", T0 + pd.Timedelta(hours=21), 50_000.0, "A", "Z2"))  # same second as "big"
    b = account_baselines(tx_frame(rows))
    assert b.loc[20, "amount_robust_z"] > 5 or np.isinf(b.loc[20, "amount_robust_z"])
    # the twin must not see "big" as history: identical baseline inputs
    assert b.loc[21, "prior_events"] == b.loc[20, "prior_events"] == 20


def test_baselines_need_history():
    b = account_baselines(tx_frame([(f"t{i}", T0 + pd.Timedelta(hours=i), 100.0, "A", "B") for i in range(3)]))
    assert b["amount_robust_z"].isna().all()  # fewer than MIN_HISTORY events -> no baseline, not a fake 0


def test_detectors_new_beneficiary_campaign_and_missing_fields():
    acc = pd.DataFrame({"account_id": ["A", "B", "C", "D", "E"], "open_date": [T0 - pd.Timedelta(days=400)] * 5})
    rows = [("p0", T0, 5000.0, "A", "B"), ("p1", T0 + pd.Timedelta(days=1), 5000.0, "A", "B")]  # 2nd is repeat, not new
    rows += [(f"c{i}", T0 + pd.Timedelta(hours=30 + i), 990.0, "A", f"N{i}") for i in range(5)]
    fired, notes = run_detectors(tx_frame(rows), acc)
    assert fired.loc[0, "new_beneficiary"] and not fired.loc[1, "new_beneficiary"]
    assert fired["repeated_campaign"].iloc[-1] and not fired["repeated_campaign"].iloc[2]
    # absent inputs are "not evaluated" (NaN), never a silent False
    assert fired["profile_mismatch"].isna().all() and "not evaluated" in notes["profile_mismatch"]
    assert fired["geography_shift"].isna().all()
    # payroll: an employer paying identical salaries to new staff is the benign look-alike, not a campaign
    payroll = acc.assign(account_type=["employer", "individual", "individual", "individual", "individual"])
    assert not run_detectors(tx_frame(rows), payroll)[0]["repeated_campaign"].any()


def test_graph_detectors_cycle_evidence_and_focus_filter():
    pytest.importorskip("ml.graph", reason="graph engine lives on feat/graph-engine until merged")
    from ml.engine.detectors import graph_detectors
    e = lambda tid, o, d, h: {"transaction_id": tid, "origin_account_id": o, "destination_account_id": d,
                              "amount": 1000.0 - h, "occurred_at": T0 + pd.Timedelta(hours=h)}
    edges = [e("c1", "A", "B", 1), e("c2", "B", "C", 2), e("c3", "C", "A", 3),   # time-respecting A->B->C->A
             e("x1", "X", "Y", 1), e("x2", "Y", "Z", 2), e("x3", "Z", "X", 3)]   # someone else's cycle
    by_tx, stats = graph_detectors(edges, cycle_limits={"max_hops": 4}, focus_account="A")
    assert set(by_tx) == {"c1", "c2", "c3"}           # only A's own cycle, with its edge ids as evidence
    assert by_tx["c1"][0]["kind"] == "cycle" and not stats["cycles"].get("truncated")
    reversed_time = [e("r1", "A", "B", 3), e("r2", "B", "C", 2), e("r3", "C", "A", 1)]  # money can't flow back in time
    assert graph_detectors(reversed_time, cycle_limits={"max_hops": 4})[0] == {}


def test_resolution_ranks_variants_and_never_matches_unrelated():
    cands = [{"id": 1, "name": "ACME Holdings LLC", "address": "12 Main Street"},
             {"id": 2, "name": "Acme Holding Inc", "address": "12 Main St"},
             {"id": 3, "name": "Zenith Bakery", "address": "9 Elm Rd"}]
    out = resolve({"name": "Acme Holdings, L.L.C.", "address": "12 Main St."}, cands)
    assert [c["id"] for c in out][:2] == [1, 2] and out[0]["band"] == "high"
    assert out[0]["fields"]["name"]["exact"]  # explainable field-level evidence
    assert resolve({"name": "Zenith Bakeries"}, cands)[0]["id"] == 3
    assert resolve({"name": "Completely Different Co"}, cands) == []


def test_fusion_policy_bands_quality_floor_and_clusters():
    s = pd.DataFrame({"xgb_flag": [True, True, False, False], "gnn_flag": [True, False, False, False],
                      "rule_hits": [2, 0, 0, 0], "anomaly_flag": [False, False, False, False],
                      "xgb_score": [0.9, 0.5, 0.1, 0.1], "gnn_score": [0.9, 0.2, 0.1, 0.1], "quality_issues": [0, 0, 0, 1]})
    a = fuse(s)
    assert list(a["priority_band"]) == ["critical", "medium", "low", "medium"]  # quality issue raises row 4 to review
    assert a.loc[0, "policy_version"] == POLICY.version and "supervised flagged" in a.loc[0, "reasons"]
    tx = pd.DataFrame({"timestamp": [T0, T0 + pd.Timedelta(hours=1), T0, T0 + pd.Timedelta(days=30)],
                       "origin_account_id": ["A", "A", "X", "A"], "destination_account_id": ["B", "C", "Y", "D"]})
    c = cluster(a, tx)
    assert c.loc[0, "cluster_id"] == c.loc[1, "cluster_id"]   # share account A within 72 h
    assert c.loc[3, "cluster_id"] != c.loc[0, "cluster_id"]   # same account but outside the window
    assert np.isnan(c.loc[2, "cluster_id"])                    # low-priority rows are not alerts


def test_manifest_records_checksum_and_at_k(tmp_path):
    p = tmp_path / "model.json"
    p.write_text("{}")
    with timed() as t:
        sum(range(1000))
    m = run_manifest("demo", [p], threshold=0.5, inference_ms=t.ms, n_scored=1000)
    assert m["artifacts"][0]["sha256"] == "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
    assert m["run_id"] and m["inference_ms"] is not None and m["feature_version"] is None  # missing stays missing
    k = at_k([1, 0, 1, 0, 0], [0.9, 0.8, 0.7, 0.1, 0.0], ks=(2,))
    assert k == {"precision@2": 0.5, "recall@2": 0.5}
    with pytest.raises(FileNotFoundError):
        run_manifest("demo", [tmp_path / "missing.json"])
