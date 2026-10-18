"""Governance aggregations (pure functions) and the read-only endpoints' auth and validation gates."""

import json
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.core.security import CurrentUser, get_current_user
from app.main import app
from app.services import governance as gov

client = TestClient(app)
TENANT = "11111111-1111-1111-1111-111111111111"
RUN_A, RUN_B, RUN_C = (f"aaaaaaaa-0000-0000-0000-00000000000{i}" for i in (1, 2, 3))


def _run(run_id, name="rules", version="1", started="2026-10-01T00:00:00Z", status="completed", **extra):
    return {"id": run_id, "model_name": name, "model_version": version, "model_kind": "rule_engine", "status": status,
            "started_at": started, "completed_at": extra.pop("completed_at", started), "dataset_version": "demo", **extra}


# -------------------------------------------------------------------------------------- metrics


def test_headline_metrics_normalises_names_and_prefers_test_window():
    metrics = {"pr_auc": 0.2, "P@100": 0.5, "threshold": 0.7, "confusion_matrix": {"tp": 1},
               "test": {"AUPRC": 0.9, "precision_at_100": 0.8, "recall@100": 0.4}}
    assert gov.headline_metrics(metrics) == {"pr_auc": 0.9, "precision@100": 0.8, "recall@100": 0.4}


def test_headline_metrics_ignores_non_numbers():
    assert gov.headline_metrics({"f1": "0.5", "note": "demo", "flag": True, "nan": float("nan")}) == {"f1": 0.5}
    assert gov.headline_metrics(None) == {}


def test_metric_names_ordered_headline_then_at_k():
    rows = [{"recall@50": 1, "precision@10": 1, "pr_auc": 1}, {"precision@100": 1, "zeta": 1, "f1": 1}]
    assert gov.ordered_metric_names(rows) == ["pr_auc", "f1", "precision@10", "precision@100", "recall@50", "zeta"]


# --------------------------------------------------------------------------------- model registry


def test_signal_stats_and_registry_grouping():
    stats = gov.summarize_signal_stats([
        {"model_run_id": RUN_A, "source": "rule", "typology": "structuring", "triggered": True, "signal_count": 3,
         "scored_count": 3, "score_min": "0.7", "score_max": "0.9"},
        {"model_run_id": RUN_A, "source": "rule", "typology": "structuring", "triggered": False, "signal_count": 5,
         "scored_count": 5, "score_min": "0.1", "score_max": "0.5"},
        {"model_run_id": RUN_A, "source": "rule", "typology": None, "triggered": True, "signal_count": 1,
         "scored_count": 0, "score_min": None, "score_max": None},
    ])
    assert stats[RUN_A]["signal_count"] == 9 and stats[RUN_A]["triggered_count"] == 4
    assert stats[RUN_A]["typologies"] == {"structuring": 3, "unspecified": 1}
    assert (stats[RUN_A]["score_min"], stats[RUN_A]["score_max"]) == (0.1, 0.9)

    runs = [
        _run(RUN_A, started="2026-10-01T00:00:00Z", metrics={"pr_auc": 0.5}),
        _run(RUN_B, started="2026-10-02T00:00:00Z", status="failed", error_message="boom"),
        _run(RUN_C, name="xgb", started="2026-09-01T00:00:00Z"),
    ]
    entries = gov.group_registry(runs, stats, {RUN_A: 2})
    rules = next(e for e in entries if e["model_name"] == "rules")
    assert rules["run_count"] == 2 and rules["completed_run_count"] == 1
    assert rules["status"] == "failed"  # latest run
    assert rules["headline_metrics"] == {"pr_auc": 0.5}  # metrics from the latest completed run
    assert [r["id"] for r in rules["runs"]] == [RUN_B, RUN_A]
    assert rules["runs"][1]["alert_count"] == 2 and rules["runs"][1]["signal_count"] == 9
    assert entries[0]["model_name"] == "rules"  # newest activity first


def test_read_model_cards_reads_card_and_results(tmp_path):
    (tmp_path / "set_a").mkdir()
    (tmp_path / "set_a" / "model_card.json").write_text(json.dumps({
        "model_version": "m-1", "feature_columns": ["a", "b"], "thresholds": {"xgb": {"operating": 0.8}},
        "metrics": {"xgb": {"val": {"pr_auc": 0.4}, "test": {"pr_auc": 0.6, "precision@100": 1.0,
                                                             "recall_by_typology": {"cycle": 0.5, "bad": "x"}}}},
        "limitations": ["synthetic"]}))
    (tmp_path / "set_b").mkdir()
    (tmp_path / "set_b" / "RESULTS.md").write_text("# results")
    (tmp_path / "set_c").mkdir()  # no card, no results: skipped
    (tmp_path / "set_d").mkdir()
    (tmp_path / "set_d" / "model_card.json").write_text("{not json")
    cards = {c["artifact_set"]: c for c in gov.read_model_cards(tmp_path)}
    assert set(cards) == {"set_a", "set_b", "set_d"}
    card = cards["set_a"]["card"]
    assert card["feature_count"] == 2 and card["limitations"] == ["synthetic"]
    assert card["detectors"][0] == {"name": "xgb", "test": {"pr_auc": 0.6, "precision@100": 1.0}, "validation": {"pr_auc": 0.4},
                                    "recall_by_typology": {"cycle": 0.5}, "thresholds": {"operating": 0.8}}
    assert cards["set_b"]["results_markdown"] == "# results" and cards["set_b"]["card"] is None
    assert cards["set_d"]["card_error"]
    assert all(c["metrics_are_demonstration"] for c in cards.values())
    assert gov.read_model_cards(tmp_path / "missing") == []


def test_real_artifacts_are_labelled_offline():
    for card in gov.read_model_cards():
        assert card["source"] == "offline_training" and card["metrics_are_demonstration"] is True


# ------------------------------------------------------------------------------------- pipelines


def test_pipeline_timeline_merges_and_orders_newest_first():
    ingestion = [{"id": "i1", "status": "completed", "started_at": "2026-10-01T00:00:00Z", "completed_at": "2026-10-01T00:00:30Z",
                  "row_count": 10, "accepted_count": 9, "warning_count": 1, "rejected_count": 1, "source_files": {"filename": "a.csv"}}]
    models = [_run("m1", started="2026-10-02T00:00:00Z", completed_at=None, status="running")]
    agents = [{"id": "g1", "graph_name": "investigate", "status": "completed", "created_at": "2026-09-30T00:00:00Z",
               "started_at": None, "completed_at": "2026-09-30T00:01:00Z", "step_count": 4}]
    items = gov.pipeline_timeline(ingestion, models, agents)
    assert [i["id"] for i in items] == ["m1", "i1", "g1"]
    assert items[1]["duration_seconds"] == 30 and items[1]["name"] == "a.csv" and items[1]["rows"]["rejected_count"] == 1
    assert items[0]["duration_seconds"] is None  # still running
    assert items[2]["duration_seconds"] == 60  # falls back to created_at
    assert gov.pipeline_summary(items) == {"model": {"running": 1}, "ingestion": {"completed": 1}, "agent": {"completed": 1}}


def test_duration_handles_bad_input():
    assert gov.duration_seconds("not a date", "2026-10-01T00:00:00Z") is None
    assert gov.duration_seconds("2026-10-01T00:01:00Z", "2026-10-01T00:00:00Z") == 0.0


# ------------------------------------------------------------------------------- drift and health


def _ingestion(run_id, started, rows, rejected):
    return {"id": run_id, "started_at": started, "row_count": rows, "rejected_count": rejected, "warning_count": 0, "status": "completed"}


def test_run_health_needs_three_runs_for_a_trend():
    runs = [_ingestion("r1", "2026-10-01", 10, 1), _ingestion("r2", "2026-10-02", 10, 3)]
    mix = [{"run_id": "r1", "issue_type": "invalid_amount", "severity": "error", "issue_count": 1},
           {"run_id": "r2", "issue_type": "invalid_amount", "severity": "error", "issue_count": 2},
           {"run_id": "r2", "issue_type": "duplicate_record", "severity": "warning", "issue_count": 1}]
    health = gov.run_health(runs, mix)
    assert health["rejected_rate_trend"] == {"status": "not_enough_runs", "runs_needed": 3, "runs_available": 2}
    assert health["runs"][1]["issue_mix"] == {"invalid_amount": 2, "duplicate_record": 1}
    assert health["issue_totals"] == {"invalid_amount": 3, "duplicate_record": 1}

    health = gov.run_health([*runs, _ingestion("r3", "2026-10-03", 20, 10)], mix)
    trend = health["rejected_rate_trend"]
    assert trend["status"] == "ok" and abs(trend["baseline_rejected_rate"] - 0.2) < 1e-9 and trend["latest_rejected_rate"] == 0.5


def test_run_health_skips_empty_runs_in_rates():
    health = gov.run_health([_ingestion("r1", "2026-10-01", 0, 0)], [])
    assert health["runs"][0]["rejected_rate"] is None and health["rejected_rate_trend"]["runs_available"] == 0


def test_histograms_psi_and_score_drift():
    hists = gov.histograms([{"model_run_id": RUN_A, "bin": 1, "signal_count": 5}, {"model_run_id": RUN_A, "bin": 12, "signal_count": 5},
                            {"model_run_id": RUN_B, "bin": 1, "signal_count": 5}, {"model_run_id": RUN_B, "bin": 10, "signal_count": 5}])
    assert hists[RUN_A] == [5, 0, 0, 0, 0, 0, 0, 0, 0, 5]
    assert gov.psi(hists[RUN_A], hists[RUN_B]) == 0
    assert gov.psi([0] * 10, hists[RUN_A]) is None
    shifted = gov.psi([10, 0, 0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0, 0, 10])
    assert gov.psi_status(shifted) == "drift" and gov.psi_status(0.15) == "watch" and gov.psi_status(0.01) == "stable"

    runs = [_run(RUN_A, started="2026-10-01"), _run(RUN_B, started="2026-10-02"), _run(RUN_C, name="xgb", started="2026-10-03")]
    drift = gov.score_drift(runs, hists)  # RUN_C has no scored signals and is left out
    assert len(drift) == 1 and drift[0]["comparison"] == "ok"
    assert drift[0]["runs"][0]["psi_vs_previous"] is None and drift[0]["runs"][1]["psi_status"] == "stable"


# --------------------------------------------------------------------------------------- exports


def test_exports_from_audit_reads_hashes_when_present():
    events = [
        {"id": 1, "event_id": "e1", "object_id": "c1", "occurred_at": "2026-10-01T00:00:00Z", "actor_type": "user",
         "metadata": {"path": "t/cases/c1/a.json"}},
        {"id": 2, "event_id": "e2", "object_id": "c1", "occurred_at": "2026-10-02T00:00:00Z", "actor_type": "user",
         "metadata": {"path": "t/cases/c1/b.zip", "sha256": "ab" * 32, "manifest_sha256": "cd" * 32}},
    ]
    items = gov.exports_from_audit(events, {"c1": {"case_number": 7, "title": "Layering"}})
    assert [i["event_id"] for i in items] == ["e2", "e1"]
    assert items[0]["sha256"] == "ab" * 32 and items[0]["case_number"] == 7
    assert items[1]["sha256"] is None and items[1]["manifest_sha256"] is None


# --------------------------------------------------------------------------- detector comparison


def test_detector_comparison_uses_latest_completed_run_per_model():
    runs = [
        _run(RUN_A, completed_at="2026-10-01", metrics={"precision@10": 0.4}),
        _run(RUN_B, completed_at="2026-10-02", metrics={"test": {"precision@10": 0.6, "pr_auc": 0.3}}),
        _run(RUN_C, name="xgb", status="failed", completed_at="2026-10-03"),
    ]
    stats = {RUN_B: {**gov.EMPTY_SIGNALS, "signal_count": 4, "triggered_count": 2, "typologies": {"cycle": 2}}}
    result = gov.detector_comparison(runs, stats, {RUN_B: 1})
    assert [d["model_run_id"] for d in result["detectors"]] == [RUN_B]
    assert result["detectors"][0]["metrics"] == {"precision@10": 0.6, "pr_auc": 0.3}
    assert result["metric_names"] == ["pr_auc", "precision@10"] and result["typologies"] == ["cycle"]
    assert result["detectors"][0]["alert_count"] == 1


# --------------------------------------------------------------------------------------- lineage


def test_classify_ref():
    assert gov.classify_ref(" AL-42 ") == {"kind": "alert_number", "value": 42}
    assert gov.classify_ref("case-0007") == {"kind": "case_number", "value": 7}
    assert gov.classify_ref("demo.csv#12") == {"kind": "source_row", "scope": "demo.csv", "value": 12}
    assert gov.classify_ref("demo.csv row 3") == {"kind": "source_row", "scope": "demo.csv", "value": 3}
    assert gov.classify_ref("Row 5") == {"kind": "source_row", "scope": None, "value": 5}
    assert gov.classify_ref("TX-90001") == {"kind": "text", "value": "TX-90001"}
    assert gov.classify_ref("ABCDEF12-0000-0000-0000-000000000001")["kind"] == "uuid"


def test_build_chain_order_and_gaps():
    chain = gov.build_chain(
        files=[{"id": "f", "filename": "a.csv", "checksum_sha256": "0" * 64}], runs=[{"id": "r", "schema_version": "v1"}],
        rows=[{"id": "row", "row_number": 4, "status": "accepted_with_warnings"}],
        issues=[{"id": "i", "source_row_id": "row", "issue_type": "invalid_format", "severity": "warning"}],
        transactions=[{"id": "t", "transaction_ref": "TX-1"}], features=None,
        signals=[{"id": "s", "signal_name": "fan_out", "triggered": True, "model_run_id": "m"}],
        model_runs=[{"id": "m", "model_name": "rules", "model_version": "3"}],
        alerts=[{"id": "a", "alert_number": 9, "model_run_id": "m"}], cases=[{"id": "c", "case_number": 3}],
        evidence=[], narratives=[], decisions=[])
    assert [n["key"] for n in chain] == ["source_file", "ingestion_run", "source_row", "transaction", "features", "signals",
                                         "alerts", "cases", "evidence", "narratives", "decisions"]
    nodes = {n["key"]: n for n in chain}
    assert nodes["source_row"]["items"][0]["issues"][0]["issue_type"] == "invalid_format"
    assert nodes["signals"]["items"][0]["detail"] == "rules 3"
    assert nodes["alerts"]["items"][0]["label"] == "AL-9" and nodes["cases"]["items"][0]["label"] == "CASE-0003"
    assert nodes["features"]["linked"] is False and nodes["features"]["note"]
    assert nodes["evidence"]["linked"] is False and nodes["decisions"]["count"] == 0


# ----------------------------------------------------------------------------------- API gates

GOVERNANCE_PATHS = ["/models/registry", "/governance/pipelines", "/governance/health", "/governance/exports",
                    "/governance/detectors", "/lineage/lookup?ref=TX-1"]


def test_governance_endpoints_require_auth():
    for path in GOVERNANCE_PATHS:
        assert client.get(path).status_code == 401, path


def _as(role):
    return lambda: CurrentUser(id="u1", tenant_id=TENANT, role=role)


def test_service_workers_cannot_read_governance_views():
    service_db = MagicMock()
    service_db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [{"id": TENANT}]
    with patch("app.core.security.get_db", return_value=service_db):
        response = client.get("/models/registry", headers={"X-Service-Key": "test-worker-key", "X-Tenant-Id": TENANT})
    assert response.status_code == 403


def test_lookup_and_pipelines_validate_input():
    app.dependency_overrides[get_current_user] = _as("data_steward")
    try:
        assert client.get("/lineage/lookup?ref=").status_code == 422
        assert client.get("/lineage/lookup?ref=" + "x" * 201).status_code == 422
        assert client.get("/governance/pipelines?limit=0").status_code == 422
        assert client.get("/governance/pipelines?limit=1000").status_code == 422
        assert client.get("/governance/exports?limit=501").status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_steward_and_analyst_can_read_registry():
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = []
    for role in ("data_steward", "analyst", "reviewer"):
        app.dependency_overrides[get_current_user] = _as(role)
        try:
            with patch("app.api.routes.governance.get_db", return_value=db):
                response = client.get("/models/registry")
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == 200, role
        assert response.json()["models"] == [] and "offline_model_cards" in response.json()
