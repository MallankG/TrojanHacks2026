import json
import re
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from tracelayer_agents import schemas as s
from tracelayer_agents.interfaces import (
    AuditRepository,
    BehaviorScorer,
    PolicyRetriever,
    Services,
)
from tracelayer_agents.state import AnalysisState, merge_dicts

H0 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
H1 = "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"
T0 = datetime(2026, 9, 3, 14, 12, tzinfo=UTC)


def _tx(tx_id: str = "T100", amount: str = "9500.00") -> s.EvidenceTransaction:
    return s.EvidenceTransaction(
        transaction_id=tx_id,
        source_row_ref=f"row_{tx_id}",
        timestamp=T0,
        amount=amount,
        currency="USD",
        origin_account_id="A1",
        destination_account_id="A2",
    )


def _issue(severity: str = "warning") -> s.QualityIssue:
    return s.QualityIssue(
        check_id="chk_1",
        rule_id="currency_iso4217",
        asset_id="amlsim_v1",
        severity=severity,
        affected_rows=3,
        sample_record_ids=["row_17"],
        owner_role="data_owner",
        steward_role="data_steward",
    )


def _claim() -> s.NarrativeClaim:
    return s.NarrativeClaim(
        text="A1 sent 9500.00 USD to A2 on 2026-09-03.",
        claim_type="fact",
        transaction_ids=["T100"],
        account_ids=["A1", "A2"],
        amounts=["9500.00"],
        dates=["2026-09-03"],
    )


SAMPLES: list[BaseModel] = [
    _issue(),
    s.IngestionResult(
        ingestion_run_id="run_1", transaction_batch_id="b1", source_asset_id="amlsim_v1",
        status="completed", row_count=10, accepted_count=10, rejected_count=0, started_at=T0,
    ),
    s.QualityReport(
        transaction_batch_id="b1", contract_version="v1", status="fail",
        issues=[_issue("critical")], rows_checked=10, checked_at=T0,
    ),
    s.GraphSnapshot(
        graph_snapshot_id="snap_1", transaction_batch_id="b1", feature_schema_version="fv1",
        node_count=2, edge_count=1, window_start=T0, window_end=T0, built_at=T0,
    ),
    s.SubgraphJSON(
        graph_snapshot_id="snap_1",
        nodes=[s.SubgraphNode(account_id="A1")],
        edges=[s.SubgraphEdge(
            transaction_id="T100", origin_account_id="A1", destination_account_id="A2",
            amount="1.10", currency="USD", timestamp=T0,
        )],
    ),
    s.DetectorOutput(
        detector_name="xgboost", model_version="xgb_v3", scores={"T100": 0.9},
        top_factors={"T100": [s.Factor(name="in_out_ratio", kind="feature", value=0.98)]},
        feature_snapshot_id="snap_1", typology_flags={"T100": ["rapid_in_out"]},
    ),
    s.FusionResult(method="mock_rank_mean", composite={"T100": 0.9}),
    s.Alert(
        alert_id="alrt_1", tx_ids=["T100"], composite_score=0.9,
        component_scores={"xgboost": 0.9}, tier="high", quality_status="fail",
        confidence_caveat="incomplete_data", graph_snapshot_id="snap_1",
        model_versions={"xgboost": "xgb_v3"}, rank=1,
    ),
    s.EvidenceBundle(
        alert_id="alrt_1",
        transactions=[_tx()],
        flow_findings=s.FlowFindings(
            alert_id="alrt_1", graph_snapshot_id="snap_1",
            pass_through=[s.FlowPattern(
                account_ids=["A1", "A2"], transaction_ids=["T100"], total_amount="9500.00",
            )],
            evidence_tx_ids=["T100"],
        ),
        quality_caveats=[_issue()],
        lineage_refs=["run_1"],
        bundle_hash=H0,
    ),
    s.RetrievedChunk(
        chunk_id="c1", doc_title="FFIEC Manual", source_uri="https://example.org",
        excerpt="text", score=0.8,
    ),
    s.NarrativeDraft(
        summary="Draft summary.", observed_facts=[_claim()], evidence_transaction_ids=["T100"],
        provider="template", model="template", prompt_version="narrative_v1", fallback_used=True,
    ),
    s.PolicyAnswer(
        question="What is a CTR?", answer="See cited passage.", citations=["c1"],
        provider="template", model="template", prompt_version="policy_qa_v1",
    ),
    s.VerifierError(claim_index=0, code="unknown_amount", detail="9600.00 not in bundle"),
    s.Decision(action="escalate", actor_role="reviewer", reason="Funnel pattern."),
    s.CaseRecord(
        case_id="case_1", alert_id="alrt_1", title="Funnel into A2",
        status=s.CaseStatus.AWAITING_REVIEW, created_at=T0, updated_at=T0,
    ),
    s.TraceEvent(
        trace_id="tr_1", node="ingest", status="ok", started_at=T0,
        ended_at=T0 + timedelta(seconds=1), duration_ms=1000.0, input_hash=H0, output_hash=H1,
    ),
    s.AuditEvent(
        event_id="evt_1", actor_role="system", action="case.created", object_type="case",
        object_id="case_1", prev_hash=None, hash=H1, timestamp=T0, metadata={"attempts": 1},
    ),
]


# --------------------------------------------------------------------------- round trips


@pytest.mark.parametrize("model", SAMPLES, ids=lambda m: type(m).__name__)
def test_json_round_trip(model: BaseModel) -> None:
    dumped = model.model_dump(mode="json")
    json.dumps(dumped)  # really JSON-serializable
    assert type(model).model_validate(dumped) == model


def test_money_dumps_as_string_and_comes_back_exact() -> None:
    dumped = _tx(amount="0.10").model_dump(mode="json")
    assert dumped["amount"] == "0.10"
    assert s.EvidenceTransaction.model_validate(dumped).amount == Decimal("0.10")


# --------------------------------------------------------------------------- validators


def test_float_amount_rejected() -> None:
    with pytest.raises(ValidationError, match="not float"):
        _tx(amount=0.1)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["NaN", "Infinity"])
def test_non_finite_amount_rejected(bad: str) -> None:
    with pytest.raises(ValidationError):
        _tx(amount=bad)


def test_claim_amounts_reject_float() -> None:
    with pytest.raises(ValidationError):
        s.NarrativeClaim(text="x", claim_type="fact", amounts=[9500.0])


def test_naive_timestamp_rejected() -> None:
    with pytest.raises(ValidationError):
        s.EvidenceTransaction(**{**_tx().model_dump(), "timestamp": datetime(2026, 9, 3)})


def test_aware_timestamp_normalized_to_utc() -> None:
    pacific = timezone(timedelta(hours=-7))
    tx = s.EvidenceTransaction(
        **{**_tx().model_dump(), "timestamp": datetime(2026, 9, 3, 7, 12, tzinfo=pacific)}
    )
    assert tx.timestamp == T0
    assert tx.timestamp.utcoffset() == timedelta(0)


@pytest.mark.parametrize("reason", ["", "   "])
def test_decision_reason_required(reason: str) -> None:
    with pytest.raises(ValidationError):
        s.Decision(action="escalate", actor_role="reviewer", reason=reason)


def test_decision_actor_role_required() -> None:
    with pytest.raises(ValidationError):
        s.Decision(action="dismiss", actor_role="", reason="Benign payroll.")


def test_decision_actions_map_to_case_status() -> None:
    assert set(s.DECISION_TO_STATUS) == set(s.DecisionAction.__args__)


def test_failed_gate_alert_needs_caveat() -> None:
    data = SAMPLES[7].model_dump()
    data["confidence_caveat"] = None
    with pytest.raises(ValidationError, match="confidence_caveat"):
        s.Alert(**data)


def test_alert_keeps_component_scores() -> None:
    data = SAMPLES[7].model_dump() | {"component_scores": {}}
    with pytest.raises(ValidationError):
        s.Alert(**data)


def test_critical_issue_forces_fail() -> None:
    with pytest.raises(ValidationError, match="critical"):
        s.QualityReport(
            transaction_batch_id="b1", contract_version="v1", status="qualified",
            issues=[_issue("critical")], rows_checked=1, checked_at=T0,
        )


def test_ingestion_counts_must_add_up() -> None:
    with pytest.raises(ValidationError):
        s.IngestionResult(
            ingestion_run_id="r", transaction_batch_id="b", source_asset_id="a",
            status="completed", row_count=10, accepted_count=9, rejected_count=0, started_at=T0,
        )


def test_detector_factors_must_reference_scored_tx() -> None:
    with pytest.raises(ValidationError, match="top_factors"):
        s.DetectorOutput(
            detector_name="x", model_version="v", scores={"T1": 0.1},
            top_factors={"T2": []}, feature_snapshot_id="snap",
        )


def test_flow_pattern_must_cite_evidence() -> None:
    with pytest.raises(ValidationError, match="evidence_tx_ids"):
        s.FlowFindings(
            alert_id="a", graph_snapshot_id="snap",
            cycles=[s.FlowPattern(account_ids=["A1"], transaction_ids=["T9"])],
            evidence_tx_ids=["T1"],
        )


def test_bundle_findings_must_be_in_transactions() -> None:
    data = SAMPLES[8].model_dump()
    data["flow_findings"]["evidence_tx_ids"] = ["T100", "T999"]
    data["flow_findings"]["pass_through"] = []
    with pytest.raises(ValidationError, match="T999"):
        s.EvidenceBundle(**data)


def test_hash_fields_must_be_sha256_hex() -> None:
    with pytest.raises(ValidationError):
        s.TraceEvent(
            trace_id="t", node="n", status="ok", started_at=T0, ended_at=T0, duration_ms=0,
            input_hash="abc",
        )


def test_trace_event_end_not_before_start() -> None:
    with pytest.raises(ValidationError):
        s.TraceEvent(
            trace_id="t", node="n", status="ok", started_at=T0,
            ended_at=T0 - timedelta(seconds=1), duration_ms=0, input_hash=H0,
        )


def test_audit_metadata_must_be_flat() -> None:
    with pytest.raises(ValidationError):
        s.AuditEvent(
            event_id="e", actor_role="system", action="a", object_type="case", object_id="c",
            timestamp=T0, metadata={"nested": {"x": 1}},
        )


def test_unknown_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        s.Decision(action="escalate", actor_role="r", reason="x", auto_file_sar=True)


def test_retrieved_chunk_accepts_prd_column_names_and_ignores_embedding() -> None:
    row = {
        "chunk_id": "c1", "source_title": "Manual", "source_url": "https://example.org",
        "section": "CTR", "text": "passage", "score": 0.5, "embedding": [0.1, 0.2],
    }
    chunk = s.RetrievedChunk.model_validate(row)
    assert chunk.doc_title == "Manual"
    assert chunk.source_uri == "https://example.org"
    assert chunk.excerpt == "passage"
    assert "embedding" not in chunk.model_dump()


# --------------------------------------------------------------------------- state & interfaces


def test_merge_dicts_reducer() -> None:
    assert merge_dicts({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}
    assert merge_dicts(None, {"b": 2}) == {"b": 2}
    assert merge_dicts({"a": 1}, {"a": 3}) == {"a": 3}


def test_langgraph_picks_up_reducers() -> None:
    from langgraph.graph import START, StateGraph

    def graphml(_: AnalysisState) -> dict:
        return {"model_scores": {"graphsage": SAMPLES[5]}, "trace_events": [SAMPLES[15]]}

    def behavior(_: AnalysisState) -> dict:
        return {"model_scores": {"xgboost": SAMPLES[5]}, "trace_events": [SAMPLES[15]]}

    builder = StateGraph(AnalysisState)
    builder.add_node("graphml", graphml)
    builder.add_node("behavior", behavior)
    builder.add_edge(START, "graphml")
    builder.add_edge(START, "behavior")
    out = builder.compile().invoke({"trace_id": "tr_1"})
    assert set(out["model_scores"]) == {"graphsage", "xgboost"}
    assert len(out["trace_events"]) == 2


def test_protocols_are_runtime_checkable() -> None:
    class Retriever:
        def search(self, query, filters, top_k):
            return []

    assert isinstance(Retriever(), PolicyRetriever)
    assert not isinstance(Retriever(), BehaviorScorer)
    assert not isinstance(Retriever(), AuditRepository)
    assert Services().policy is None


# --------------------------------------------------------------------------- docs stay honest


_EXAMPLE_RE = re.compile(r"<!-- example: (\w+) -->\s*```json\n(.*?)\n```", re.DOTALL)


def test_contracts_md_examples_validate() -> None:
    text = (Path(__file__).parents[1] / "docs" / "CONTRACTS.md").read_text()
    examples = _EXAMPLE_RE.findall(text)
    assert {name for name, _ in examples} == {
        "IngestionResult", "QualityReport", "GraphSnapshot", "SubgraphJSON", "AuditEvent",
        "DetectorOutput", "FusionResult", "FlowFindings", "RetrievedChunk", "EvidenceTransaction",
    }
    for name, body in examples:
        getattr(s, name).model_validate(json.loads(body))
