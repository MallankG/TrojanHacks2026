"""End-to-end analysis workflow on the mock fixtures."""

import dataclasses

import pytest

from tests.test_mocks import PLANTED
from tracelayer_agents.audit import event_hash
from tracelayer_agents.interfaces import Services
from tracelayer_agents.registry import get_services
from tracelayer_agents.schemas import CaseStatus
from tracelayer_agents.workflows.analysis import (
    NODES,
    build_analysis_graph,
    build_analysis_workflow,
    run_analysis,
)
from tracelayer_agents.wrappers import NodeError

ALL_NODES = [n.name for n in NODES]


@pytest.fixture
def services() -> Services:
    return get_services()


def _evidence_ids(services: Services, alerts) -> list[set[str]]:
    bundles = [services.cases.get_evidence(a.alert_id) for a in alerts]
    return [{t.transaction_id for t in b.transactions} for b in bundles if b is not None]


@pytest.mark.parametrize("scenario", ["fan_out", "pass_through", "cycle"])
async def test_planted_pattern_reaches_evidence(services: Services, scenario: str) -> None:
    state = await run_analysis(scenario, "strict", services)
    planted = set(PLANTED[scenario])
    evidence = _evidence_ids(services, state["alerts"])
    assert evidence, "no evidence bundles were saved"
    assert planted <= set().union(*evidence)
    if scenario in ("fan_out", "cycle"):  # one connected pattern → one bundle covers it
        assert any(planted <= ids for ids in evidence)
    # every planted tx is itself an alert carrying its typology tag
    tagged = {a.tx_ids[0] for a in state["alerts"] if a.typology_tags}
    assert planted <= tagged


async def test_top_bundle_in_state_is_hashed_and_persisted(services: Services) -> None:
    state = await run_analysis("fan_out", "strict", services)
    bundle = state["evidence_bundle"]
    assert bundle.alert_id == state["alerts"][0].alert_id
    assert services.cases.get_evidence(bundle.alert_id) == bundle
    assert state["top_evidence_transaction_ids"] == [t.transaction_id for t in bundle.transactions]
    assert set(PLANTED["fan_out"]) <= set(state["top_evidence_transaction_ids"])


async def test_bad_batch_strict_halts(services: Services) -> None:
    state = await run_analysis("bad_batch", "strict", services)
    assert state["governance_status"] == "fail"
    assert state["case_status"] == CaseStatus.HELD_FOR_DATA_REVIEW
    assert "alerts" not in state
    assert [e.node for e in state["trace_events"]] == ["ingest", "governance"]
    assert state["audit_events"][-1].action == "analysis.run.halted"
    assert services.cases.alerts == {}


async def test_bad_batch_exploratory_caveats_every_alert(services: Services) -> None:
    state = await run_analysis("bad_batch", "exploratory", services)
    assert state["governance_status"] == "fail"
    assert state.get("case_status") is None
    assert state["alerts"]
    for alert in state["alerts"]:
        assert alert.confidence_caveat == "incomplete_data"
        assert alert.quality_status == "fail"
    assert state["evidence_bundle"].quality_caveats  # issues travel with the evidence


async def test_parallel_detectors_both_reach_model_scores(services: Services) -> None:
    state = await run_analysis("fan_out", "strict", services)
    assert set(state["model_scores"]) == {"mock_graph_risk", "mock_velocity", "mock_typology_rules"}
    alert = state["alerts"][0]
    assert set(alert.component_scores) == set(state["model_scores"])


async def test_trace_has_every_node_once(services: Services) -> None:
    state = await run_analysis("cycle", "strict", services)
    events = state["trace_events"]
    assert sorted(e.node for e in events) == sorted(ALL_NODES)
    assert all(e.status == "ok" and e.output_hash and e.duration_ms >= 0 for e in events)
    assert {e.trace_id for e in events} == {state["trace_id"]}


async def test_audit_chain_links(services: Services) -> None:
    state = await run_analysis("fan_out", "strict", services)
    events = services.audit.list_events()
    assert [e.event_id for e in events] == [e.event_id for e in state["audit_events"]]
    actions = [e.action for e in events]
    assert actions[0] == "analysis.run.started"
    assert actions[-1] == "analysis.run.completed"
    assert actions.count("alert.created") == len(state["alerts"])
    prev = None
    for e in events:
        assert e.prev_hash == prev
        assert e.hash == event_hash(e, prev)
        prev = e.hash


class _BrokenGraphRisk:
    def score(self, snapshot_id: str):
        raise RuntimeError("model file missing")


async def test_raising_node_records_error_event(services: Services) -> None:
    broken = dataclasses.replace(services, graph_risk=_BrokenGraphRisk())
    with pytest.raises(NodeError) as info:
        await run_analysis("fan_out", "strict", broken)
    err = info.value
    assert err.node == "detect_graphml"
    assert isinstance(err.__cause__, RuntimeError)
    last = err.state["trace_events"][-1]
    assert (last.node, last.status) == ("detect_graphml", "error")
    assert "model file missing" in last.error
    first = [e.node for e in err.state["trace_events"][:3]]
    assert first == ["ingest", "governance", "build_graph"]
    assert services.audit.list_events()[-1].action == "analysis.run.failed"


def test_workflow_shape(services: Services) -> None:
    drawable = build_analysis_workflow(services).get_graph()
    edges = {(e.source, e.target) for e in drawable.edges}
    assert ("build_graph", "detect_graphml") in edges
    assert ("build_graph", "detect_behavior") in edges
    assert ("detect_graphml", "aggregate") in edges
    assert ("detect_behavior", "aggregate") in edges
    assert ("governance", "__end__") in edges
    assert ("governance", "build_graph") in edges


def test_build_analysis_graph_is_deprecated_alias(services: Services) -> None:
    with pytest.warns(DeprecationWarning, match="build_analysis_workflow"):
        workflow = build_analysis_graph(services)
    assert set(workflow.get_graph().nodes) == set(
        build_analysis_workflow(services).get_graph().nodes
    )
