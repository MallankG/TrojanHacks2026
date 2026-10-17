import logging
import math
import re
from datetime import UTC, datetime

import pandas as pd
import pytest
from pydantic import BaseModel

from tracelayer_agents import interfaces as i
from tracelayer_agents import mocks as m
from tracelayer_agents import schemas as s
from tracelayer_agents.registry import USE_MOCKS_ENV, get_services

T0 = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
README = (m.FIXTURES / "README.md").read_text()


def _planted() -> dict[str, list[str]]:
    rows = re.findall(r"^\| (\w+) \| .*? \| (TX-[\d, TX-]+) \|$", README, re.MULTILINE)
    return {name: [t.strip() for t in ids.split(",")] for name, ids in rows}


def _defects() -> list[tuple[str, list[str], str, str]]:
    """(tx_id, source row refs, rule_id, severity) from the bad_batch defects table."""
    rows = re.findall(
        r"^\| (TX-\d+) \| ([\w, ]+) \| .*? \| `(\w+)` \| (\w+) \| .*\|$", README, re.MULTILINE
    )
    return [(tx, [r.strip() for r in refs.split(",")], rule, sev) for tx, refs, rule, sev in rows]


PLANTED = _planted()


def _clock() -> datetime:
    return T0


@pytest.fixture
def store() -> m.MockDataStore:
    return m.MockDataStore()


def _pipeline(store: m.MockDataStore, scenario: str) -> dict:
    ingestion = m.MockIngestion(store, clock=_clock)
    result = ingestion.ingest(scenario, "amlsim_transactions_v1")
    report = m.MockQuality(store, clock=_clock).run_contract(result.transaction_batch_id)
    snap = m.MockGraphService(store, clock=_clock).build_snapshot(result.transaction_batch_id)
    outputs = [
        m.MockGraphRiskScorer(store).score(snap.graph_snapshot_id),
        *m.MockBehaviorScorer(store).score(snap.graph_snapshot_id),
    ]
    return {"ingestion": result, "quality": report, "snapshot": snap, "outputs": outputs}


def _alert(tx_ids: list[str], snapshot_id: str) -> s.Alert:
    return s.Alert(
        alert_id="alrt_test", tx_ids=tx_ids, composite_score=0.9,
        component_scores={"mock_typology_rules": 0.7}, tier="high", quality_status="pass",
        graph_snapshot_id=snapshot_id, model_versions={"mock_typology_rules": "mock-0"},
    )


def _assert_round_trips(model: BaseModel) -> None:
    assert type(model).model_validate(model.model_dump(mode="json")) == model


# --------------------------------------------------------------------------- fixtures & protocols


def test_readme_lists_every_scenario_and_ids_exist_in_csv() -> None:
    assert set(PLANTED) == set(m.SCENARIOS)
    for scenario, tx_ids in PLANTED.items():
        csv_ids = set(pd.read_csv(m.FIXTURES / f"{scenario}.csv", dtype=str)["transaction_id"])
        assert set(tx_ids) <= csv_ids, scenario
    defects = _defects()
    assert {tx for tx, *_ in defects} == set(PLANTED["bad_batch"])
    raw = pd.read_csv(m.FIXTURES / "bad_batch.csv", dtype=str, keep_default_na=False)
    for tx, refs, *_ in defects:
        assert sorted(raw.loc[raw.transaction_id == tx, "source_row_ref"]) == refs


def test_fixtures_use_canonical_columns_and_synthetic_ids() -> None:
    for scenario in m.SCENARIOS:
        df = pd.read_csv(m.FIXTURES / f"{scenario}.csv", dtype=str, keep_default_na=False)
        assert tuple(df.columns) == m.services.CANONICAL_COLUMNS
        assert 50 <= len(df) <= 60
        assert df.transaction_id.str.fullmatch(r"TX-\d{5}").all()
        for col in ("origin_account_id", "destination_account_id"):
            assert df[col].str.fullmatch(r"(ACC-\d{4})?").all()


MOCKS_BY_PROTOCOL = [
    (i.IngestionService, lambda st: m.MockIngestion(st)),
    (i.QualityService, lambda st: m.MockQuality(st)),
    (i.GraphService, lambda st: m.MockGraphService(st)),
    (i.GraphRiskScorer, lambda st: m.MockGraphRiskScorer(st)),
    (i.BehaviorScorer, lambda st: m.MockBehaviorScorer(st)),
    (i.RankFusion, lambda st: m.MockRankFusion()),
    (i.FlowInvestigator, lambda st: m.MockFlowInvestigator(st)),
    (i.PolicyRetriever, lambda st: m.MockPolicyRetriever()),
    (i.CaseRepository, lambda st: m.InMemoryCaseRepository()),
    (i.AuditRepository, lambda st: m.InMemoryAuditRepository()),
]


@pytest.mark.parametrize(
    ("protocol", "factory"), MOCKS_BY_PROTOCOL, ids=[p.__name__ for p, _ in MOCKS_BY_PROTOCOL]
)
def test_mock_satisfies_protocol(protocol, factory, store) -> None:
    assert isinstance(factory(store), protocol)


# --------------------------------------------------------------------------- registry


def test_registry_defaults_to_mocks_and_warns_per_service(monkeypatch, caplog) -> None:
    monkeypatch.delenv(USE_MOCKS_ENV, raising=False)
    with caplog.at_level(logging.WARNING, logger="tracelayer_agents.registry"):
        services = get_services()
    for (protocol, _), field in zip(MOCKS_BY_PROTOCOL, vars(i.Services()), strict=True):
        assert isinstance(getattr(services, field), protocol)
    assert len(caplog.records) == len(MOCKS_BY_PROTOCOL)
    # data mocks share one store so a batch ingested by one is visible to the next
    assert services.ingestion.store is services.quality.store is services.flows.store


def test_registry_keeps_overrides(monkeypatch, caplog) -> None:
    monkeypatch.delenv(USE_MOCKS_ENV, raising=False)
    real = m.MockPolicyRetriever()
    with caplog.at_level(logging.WARNING, logger="tracelayer_agents.registry"):
        services = get_services(i.Services(policy=real))
    assert services.policy is real
    assert len(caplog.records) == len(MOCKS_BY_PROTOCOL) - 1
    assert not any("'policy'" in r.getMessage() for r in caplog.records)


def test_env_forces_mocks_over_overrides(monkeypatch) -> None:
    monkeypatch.setenv(USE_MOCKS_ENV, "1")
    real = m.MockPolicyRetriever()
    assert get_services(i.Services(policy=real)).policy is not real


# --------------------------------------------------------------------------- ingestion & quality


@pytest.mark.parametrize("scenario", ["fan_out", "pass_through", "cycle"])
def test_clean_fixtures_ingest_fully_and_pass_quality(store, scenario) -> None:
    out = _pipeline(store, scenario)
    assert out["ingestion"].status == "completed"
    assert out["ingestion"].rejected_count == 0
    assert out["quality"].status == "pass", out["quality"].issues


def test_ingestion_is_idempotent_per_checksum(store) -> None:
    ingestion = m.MockIngestion(store, clock=_clock)
    data = (m.FIXTURES / "fan_out.csv").read_bytes()
    first = ingestion.ingest(data, "amlsim_transactions_v1")
    again = ingestion.ingest(str(m.FIXTURES / "fan_out.csv"), "amlsim_transactions_v1")
    assert first == again
    assert store.batch(first.transaction_batch_id).source_hash == m.services._sha256(data)
    assert len(store.batches) == 1


def test_bad_batch_fails_quality(store) -> None:
    out = _pipeline(store, "bad_batch")
    ingest, report = out["ingestion"], out["quality"]
    assert (ingest.status, ingest.row_count, ingest.accepted_count, ingest.rejected_count) == (
        "partial", 57, 53, 4,
    )
    assert report.status == "fail"
    for tx, refs, rule, severity in _defects():
        matches = [x for x in report.issues if x.rule_id == rule and set(refs) <= set(
            x.sample_record_ids)]
        assert matches, f"{tx}: no {rule} issue citing {refs}"
        assert matches[0].severity == severity
    for issue in report.issues:
        assert issue.owner_role and issue.steward_role
        assert issue.asset_id == "amlsim_transactions_v1"
    _assert_round_trips(report)


def test_quality_flags_missing_columns(store) -> None:
    csv = "transaction_id,amount\nTX-00001,10.00\n"
    result = m.MockIngestion(store).ingest(csv, "a")
    assert result.status == "failed"
    report = m.MockQuality(store).run_contract(result.transaction_batch_id)
    assert report.status == "fail"
    assert report.issues[0].rule_id == "schema_columns_present"


# --------------------------------------------------------------------------- graph


def test_snapshot_id_is_stable_and_outputs_validate(store) -> None:
    out = _pipeline(store, "cycle")
    snap = out["snapshot"]
    again = _pipeline(m.MockDataStore(), "cycle")["snapshot"]
    assert snap.graph_snapshot_id == again.graph_snapshot_id
    assert snap.edge_count == 54
    for model in (out["ingestion"], out["quality"], snap, *out["outputs"]):
        _assert_round_trips(model)


def test_subgraph_is_bounded(store) -> None:
    _pipeline(store, "cycle")
    graph = m.MockGraphService(store)
    one = graph.subgraph(["TX-53001"], hops=1)
    assert {n.account_id for n in one.nodes} == {"ACC-4001", "ACC-4002"}
    two = graph.subgraph(["TX-53001"], hops=2)
    assert {"TX-53001", "TX-53002", "TX-53003", "TX-53004"} <= {e.transaction_id for e in two.edges}
    assert graph.subgraph(["TX-53001"], hops=3) == two  # capped at 2 hops
    _assert_round_trips(two)
    for bad in (0, 4):
        with pytest.raises(ValueError):
            graph.subgraph(["TX-53001"], hops=bad)
    with pytest.raises(KeyError):
        graph.subgraph(["TX-00000"], hops=1)


# --------------------------------------------------------------------------- detectors


@pytest.mark.parametrize("scenario", m.SCENARIOS)
def test_detector_outputs_are_bounded_and_versioned(store, scenario) -> None:
    out = _pipeline(store, scenario)
    names = [o.detector_name for o in out["outputs"]]
    assert len(names) == len(set(names))
    batch = store.batch(out["ingestion"].transaction_batch_id)
    tx_ids = {t.transaction_id for t in batch.transactions}
    for o in out["outputs"]:
        assert o.model_version == "mock-0"
        assert o.feature_snapshot_id == out["snapshot"].graph_snapshot_id
        assert set(o.scores) == tx_ids
        assert all(0 <= v <= 1 for v in o.scores.values())


def _top_decile(scores: dict[str, float]) -> set[str]:
    """Transactions whose competition rank (ties share the best rank) is within the top 10%."""
    ranks = pd.Series(scores).rank(method="min", ascending=False)
    return set(ranks[ranks <= math.ceil(0.1 * len(scores))].index)


@pytest.mark.parametrize("scenario", ["fan_out", "pass_through"])
def test_planted_ids_rank_in_top_decile(store, scenario) -> None:
    outputs = _pipeline(store, scenario)["outputs"]
    hits = [o.detector_name for o in outputs if set(PLANTED[scenario]) <= _top_decile(o.scores)]
    assert hits, f"no detector puts all of {PLANTED[scenario]} in the top 10%"


@pytest.mark.parametrize(
    ("scenario", "flag"), [("fan_out", "fan_out"), ("pass_through", "pass_through"),
                           ("cycle", "cycle")],
)
def test_typology_rules_flag_planted_pattern_only(store, scenario, flag) -> None:
    rules = next(o for o in _pipeline(store, scenario)["outputs"]
                 if o.detector_name == "mock_typology_rules")
    assert {tx for tx, flags in rules.typology_flags.items() if flag in flags} == set(
        PLANTED[scenario]
    )


def test_rank_fusion_is_rank_mean_and_skips_unavailable(store) -> None:
    outputs = {o.detector_name: o for o in _pipeline(store, "fan_out")["outputs"]}
    outputs["graphsage"] = s.DetectorOutput(
        detector_name="graphsage", model_version="none", status="unavailable",
        feature_snapshot_id="x",
    )
    fused = m.MockRankFusion().fuse(outputs)
    assert fused.method == "mock_rank_mean"
    assert fused.coefficients == {}
    assert set(fused.composite) == set(outputs["mock_velocity"].scores)
    assert set(PLANTED["fan_out"]) <= _top_decile(fused.composite)
    _assert_round_trips(fused)
    assert m.MockRankFusion().fuse({}).composite == {}


# --------------------------------------------------------------------------- flows


def test_cycle_fixture_yields_planted_cycle(store) -> None:
    snap = _pipeline(store, "cycle")["snapshot"]
    findings = m.MockFlowInvestigator(store).investigate(
        _alert(["TX-53002"], snap.graph_snapshot_id), snap.graph_snapshot_id
    )
    _assert_round_trips(findings)
    planted = [c for c in findings.cycles if set(c.transaction_ids) == set(PLANTED["cycle"])]
    assert len(planted) == 1
    cycle = planted[0]
    assert cycle.transaction_ids == ["TX-53001", "TX-53002", "TX-53003", "TX-53004"]
    assert cycle.account_ids == ["ACC-4001", "ACC-4002", "ACC-4003", "ACC-4004", "ACC-4001"]
    assert str(cycle.total_amount) == "97550.00"
    assert cycle.currency == "USD"
    assert set(PLANTED["cycle"]) <= set(findings.evidence_tx_ids)


def test_flow_investigator_finds_fan_out_and_pass_through(store) -> None:
    snap = _pipeline(store, "fan_out")["snapshot"]
    findings = m.MockFlowInvestigator(store).investigate(
        _alert(["TX-51001"], snap.graph_snapshot_id), snap.graph_snapshot_id
    )
    assert [set(p.transaction_ids) for p in findings.fan_out] == [set(PLANTED["fan_out"])]

    store2 = m.MockDataStore()
    snap = _pipeline(store2, "pass_through")["snapshot"]
    findings = m.MockFlowInvestigator(store2).investigate(
        _alert(["TX-52001"], snap.graph_snapshot_id), snap.graph_snapshot_id
    )
    assert [p.transaction_ids for p in findings.pass_through] == [["TX-52001", "TX-52002"]]
    assert ["TX-52001", "TX-52002"] in [p.transaction_ids for p in findings.paths]


# --------------------------------------------------------------------------- policy retrieval


def test_policy_retriever_keyword_search() -> None:
    retriever = m.MockPolicyRetriever()
    hits = retriever.search("funds moved in and out rapidly, pass-through red flags", {}, 3)
    assert hits[0].chunk_id == "mock_ffiec_red_flags_003"
    assert len(hits) <= 3
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
    for h in hits:
        _assert_round_trips(h)
    assert retriever.search("the of and", {}, 5) == []


def test_policy_retriever_filters() -> None:
    retriever = m.MockPolicyRetriever()
    hits = retriever.search("alert escalation", {"source_type": "fictional_internal_procedure"}, 5)
    assert hits and all("FICTIONAL" in h.doc_title for h in hits)
    with pytest.raises(ValueError):
        retriever.search("alert", {"jurisdiction": "US"}, 5)
    with pytest.raises(ValueError):
        retriever.search("alert", {}, 0)


def test_policy_chunks_are_labeled_and_sourced() -> None:
    chunks = m.MockPolicyRetriever().chunks
    assert 6 <= len(chunks) <= 8
    for c in chunks:
        if c["source_type"] == "fictional_internal_procedure":
            assert "FICTIONAL" in c["doc_title"] and "FICTIONAL" in c["excerpt"]
            assert c["source_uri"].startswith("urn:tracelayer:fictional:")
        else:
            assert c["excerpt"].startswith("Paraphrase")
            assert c["source_uri"].startswith("https://")


# --------------------------------------------------------------------------- persistence


def test_case_repository() -> None:
    repo = m.InMemoryCaseRepository()
    alert = _alert(["TX-1"], "snap")
    repo.save_alerts([alert])
    assert repo.get_alert("alrt_test") == alert
    assert repo.get_alert("missing") is None
    case = s.CaseRecord(case_id="c1", alert_id="alrt_test", title="t", status=s.CaseStatus.OPEN,
                        created_at=T0, updated_at=T0)
    repo.create_case(case)
    with pytest.raises(ValueError):
        repo.create_case(case)
    repo.update_case(case.model_copy(update={"status": s.CaseStatus.DRAFTED}))
    assert repo.get_case("c1").status == s.CaseStatus.DRAFTED
    draft = s.NarrativeDraft(summary="x", provider="template", model="t", prompt_version="v1")
    repo.save_draft("c1", draft)
    repo.save_draft("c1", draft.model_copy(update={"summary": "y"}))
    assert [d.summary for d in repo.list_drafts("c1")] == ["x", "y"]


def test_audit_repository_is_append_only_chain() -> None:
    repo = m.InMemoryAuditRepository()
    h1, h2 = "a" * 64, "b" * 64

    def event(eid: str, prev: str | None, h: str | None) -> s.AuditEvent:
        return s.AuditEvent(event_id=eid, actor_role="system", action="x", object_type="case",
                            object_id="c1", prev_hash=prev, hash=h, timestamp=T0)

    assert repo.last_hash() is None
    repo.append(event("e1", None, h1))
    with pytest.raises(ValueError):
        repo.append(event("e2", None, h2))  # doesn't extend the head
    with pytest.raises(ValueError):
        repo.append(event("e2", h1, None))  # not chained
    repo.append(event("e2", h1, h2))
    assert repo.last_hash() == h2
    assert [e.event_id for e in repo.list_events("c1")] == ["e1", "e2"]
    assert repo.list_events("other") == []
