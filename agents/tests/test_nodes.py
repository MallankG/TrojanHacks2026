"""Unit tests for the analysis nodes, run against the mocks."""

from datetime import UTC, datetime

import pytest

from tracelayer_agents.config import AgentConfig
from tracelayer_agents.nodes.aggregate import aggregate, assign_tiers, build_alerts
from tracelayer_agents.nodes.build_graph import build_graph
from tracelayer_agents.nodes.detect_behavior import detect_behavior
from tracelayer_agents.nodes.detect_graphml import detect_graphml
from tracelayer_agents.nodes.governance import governance, status_from_issues
from tracelayer_agents.nodes.ingest import ingest
from tracelayer_agents.nodes.investigate_flows import investigate_flows, select_targets
from tracelayer_agents.registry import get_services
from tracelayer_agents.schemas import (
    CaseStatus,
    DetectorOutput,
    FusionResult,
    QualityIssue,
    QualityReport,
)
from tracelayer_agents.wrappers import NodeContext, NodeError

CFG = AgentConfig()


def _issue(severity: str) -> QualityIssue:
    return QualityIssue(
        check_id="c", rule_id="r", asset_id="a", severity=severity, affected_rows=1,
        owner_role="o", steward_role="s",
    )


@pytest.fixture
def ctx() -> NodeContext:
    return NodeContext(services=get_services(), config=CFG)


async def _run(node, state, ctx):
    update = await node.bind(ctx)(state)
    return {**state, **{k: v for k, v in update.items() if k != "trace_events"}}


# --------------------------------------------------------------------------- governance


@pytest.mark.parametrize(
    ("severities", "expected"),
    [([], "pass"), (["info"], "qualified"), (["warning", "info"], "qualified"),
     (["warning", "critical"], "fail")],
)
def test_status_from_issues(severities: list[str], expected: str) -> None:
    assert status_from_issues([_issue(s) for s in severities]) == expected


class _Quality:
    def __init__(self, status: str, severities: list[str]) -> None:
        self.report = QualityReport(
            transaction_batch_id="b", contract_version="v", status=status,
            issues=[_issue(s) for s in severities], rows_checked=1,
            checked_at=datetime.now(UTC),
        )

    def run_contract(self, batch_id: str) -> QualityReport:
        return self.report


@pytest.mark.parametrize(
    ("mode", "service_status", "severities", "status", "held"),
    [
        ("strict", "pass", [], "pass", False),
        ("strict", "qualified", ["warning"], "qualified", False),
        ("strict", "fail", ["critical"], "fail", True),
        ("exploratory", "fail", ["critical"], "fail", False),
        ("strict", "fail", ["warning"], "fail", True),  # service stricter than issues: keep it
    ],
)
async def test_governance_gate(mode, service_status, severities, status, held) -> None:
    import dataclasses

    services = dataclasses.replace(get_services(), quality=_Quality(service_status, severities))
    state = {"trace_id": "t", "mode": mode, "transaction_batch_id": "b"}
    update = await governance.bind(NodeContext(services, CFG))(state)
    assert update["governance_status"] == status
    assert update.get("case_status") == (CaseStatus.HELD_FOR_DATA_REVIEW if held else None)
    assert len(update["quality_issues"]) == len(severities)


# --------------------------------------------------------------------------- pipeline nodes


async def test_nodes_in_sequence_with_mocks(ctx: NodeContext) -> None:
    state = {"trace_id": "t", "batch_ref": "fan_out", "mode": "strict"}
    state = await _run(ingest, state, ctx)
    assert state["transaction_batch_id"].startswith("batch_")
    state = await _run(governance, state, ctx)
    assert state["governance_status"] == "pass"
    state = await _run(build_graph, state, ctx)
    assert state["graph_snapshot_id"].startswith("snap_")

    graphml = await detect_graphml.bind(ctx)(state)
    behavior = await detect_behavior.bind(ctx)(state)
    state["model_scores"] = {**graphml["model_scores"], **behavior["model_scores"]}
    state = await _run(aggregate, state, ctx)
    assert state["alerts"] and len(state["audit_events"]) == len(state["alerts"])
    assert ctx.services.cases.get_alert(state["alerts"][0].alert_id) == state["alerts"][0]

    state = await _run(investigate_flows, state, ctx)
    assert state["evidence_bundle"].alert_id == state["alerts"][0].alert_id


class _DupBehavior:
    def score(self, snapshot_id: str) -> list[DetectorOutput]:
        out = DetectorOutput(detector_name="x", model_version="1", feature_snapshot_id="s")
        return [out, out]


async def test_detect_behavior_rejects_duplicate_names() -> None:
    import dataclasses

    services = dataclasses.replace(get_services(), behavior=_DupBehavior())
    with pytest.raises(NodeError, match="duplicate"):
        await detect_behavior.bind(NodeContext(services, CFG))({"graph_snapshot_id": "s"})


async def test_investigate_skips_without_alerts(ctx: NodeContext) -> None:
    update = await investigate_flows.bind(ctx)({"trace_id": "t", "alerts": []})
    assert update["trace_events"][0].status == "skipped"


# --------------------------------------------------------------------------- aggregate logic


def _det(name: str, scores: dict[str, float], flags=None, status="ok") -> DetectorOutput:
    return DetectorOutput(
        detector_name=name, model_version=f"{name}_v1", status=status, scores=scores,
        feature_snapshot_id="snap", typology_flags=flags or {},
    )


def test_tiers_top_pct_and_ties() -> None:
    composite = {f"T{i:02d}": 1 - i / 100 for i in range(40)}  # T00 best
    composite["T03"] = composite["T01"]  # tie with a high-tier score
    tiers = assign_tiers(composite, CFG)
    # 40 txs: high = positions 1-2, medium = 3-8. Order: T00, T01=T03, T02, T04, ...
    assert [tiers[t] for t in ("T00", "T01", "T03")] == ["high"] * 3  # tie shares the better tier
    assert tiers["T02"] == "medium"  # position 4
    assert tiers["T07"] == "medium" and tiers["T08"] == "low"  # positions 8 and 9


def test_build_alerts_keeps_scores_and_includes_rule_hits() -> None:
    composite = {f"T{i:02d}": 1 - i / 100 for i in range(40)}
    scores = {
        "gnn": _det("gnn", composite),
        "rules": _det("rules", {"T39": 0.7}, {"T39": ["cycle"]}),
        "down": _det("down", {}, status="unavailable"),
    }
    fusion = FusionResult(method="mock_rank_mean", composite=composite, model_run_id="mr1")
    alerts = build_alerts(fusion, scores, snapshot_id="snap", governance_status="pass", config=CFG)

    by_tx = {a.tx_ids[0]: a for a in alerts}
    assert "T39" in by_tx and by_tx["T39"].tier == "low"  # rule hit kept despite low rank
    assert by_tx["T39"].typology_tags == ["cycle"]
    assert by_tx["T39"].component_scores == {"gnn": composite["T39"], "rules": 0.7}
    assert by_tx["T00"].component_scores == {"gnn": 1.0}
    assert by_tx["T00"].composite_score == 1.0  # composite is FusionResult's, untouched
    assert "T20" not in by_tx  # low tier, no rule hit
    assert "down" not in by_tx["T00"].model_versions
    assert [a.rank for a in alerts] == list(range(1, len(alerts) + 1))
    assert all(a.model_run_id == "mr1" and a.confidence_caveat is None for a in alerts)


@pytest.mark.parametrize("status", ["qualified", "fail"])
def test_build_alerts_caveat_when_gate_not_pass(status: str) -> None:
    fusion = FusionResult(method="m", composite={"T1": 0.9})
    alerts = build_alerts(fusion, {"g": _det("g", {"T1": 0.9})}, snapshot_id="s",
                          governance_status=status, config=CFG)
    assert alerts[0].confidence_caveat == "incomplete_data"
    assert alerts[0].quality_status == status


def test_select_targets_top_n_plus_rule_hits_capped() -> None:
    composite = {f"T{i:02d}": 1 - i / 100 for i in range(40)}
    flags = {t: ["fan_out"] for t in ("T01", "T30", "T31", "T32")}
    fusion = FusionResult(method="m", composite=composite)
    alerts = build_alerts(fusion, {"g": _det("g", composite, flags)}, snapshot_id="s",
                          governance_status="pass", config=CFG)
    targets = [a.tx_ids[0] for a in select_targets(alerts, CFG)]
    assert targets == ["T00", "T01", "T02", "T30", "T31", "T32"]
    capped = select_targets(alerts, AgentConfig(max_investigations=4))
    assert [a.tx_ids[0] for a in capped] == ["T00", "T01", "T02", "T30"]
