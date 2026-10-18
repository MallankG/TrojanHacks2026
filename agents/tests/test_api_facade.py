"""The public facade (`tracelayer_agents.api`): mocks and the template LLM only, no network."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from tests.test_case_workflow import BAD_HYPOTHESIS, _synthesis
from tests.test_copilot import _reply, _use_llm
from tracelayer_agents import api
from tracelayer_agents.llm import client as llm
from tracelayer_agents.llm.client import LLMClient, LLMSettings
from tracelayer_agents.schemas import (
    AnalysisResult,
    CaseDecisionResult,
    CaseDraftResult,
    CaseStatus,
    CopilotResult,
    CytoscapeGraph,
)


@pytest.fixture(autouse=True)
def fresh_facade() -> None:
    api.configure()  # new mock services per test


@pytest.fixture
def template_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "_default_client", LLMClient(LLMSettings(provider="template")))


def _decision(**update) -> dict:
    return {"action": "escalate", "actor_role": "analyst", "actor_id": "user_1",
            "reason": "Fan-out to new accounts within hours; escalate for review."} | update


def _top_alert(result: AnalysisResult):
    return min(result.alerts, key=lambda a: a.rank or 0)


def _roundtrip(model: BaseModel) -> None:
    dumped = model.model_dump(mode="json")
    json.dumps(dumped)  # plain JSON, no Decimals or datetimes left
    assert type(model).model_validate(dumped) == model


async def test_golden_path(template_llm: None) -> None:
    analysis = await api.run_analysis("fan_out")
    assert analysis.status == "ok" and not analysis.halted and analysis.alerts
    assert analysis.governance_status == "pass"

    draft = await api.start_case(_top_alert(analysis).alert_id)
    assert draft.status == "ok" and draft.error is None
    assert draft.case_status == CaseStatus.AWAITING_REVIEW and draft.draft is not None

    decided = await api.resume_case(draft.case_id, _decision())
    assert decided.status == "ok"
    assert decided.case_status == CaseStatus.ESCALATED
    assert decided.audit_event.actor_role == "analyst"

    assert [e.node for e in api.get_trace(analysis.trace_id)] == [e.node for e in analysis.trace]
    case_nodes = [e.node for e in api.get_trace(draft.trace_id)]
    assert case_nodes[0] == "load_case" and case_nodes[-1] == "record_decision"
    assert api.get_trace("tr_unknown") == []

    check = api.verify_audit_chain()
    assert check.ok and check.checked > 0


async def test_bad_batch_strict_halts() -> None:
    result = await api.run_analysis("bad_batch", "strict")
    assert result.status == "halted" and result.halted
    assert result.governance_status == "fail" and result.alerts == []


async def test_unknown_alert_is_error() -> None:
    result = await api.start_case("alrt_missing")
    assert result.status == "error"
    assert "unknown alert_id" in result.error
    assert result.draft is None


async def test_unknown_case_is_error() -> None:
    result = await api.resume_case("case_missing", _decision())
    assert result.status == "error" and "unknown case_id" in result.error


async def test_analyst_dismiss_needs_revision_rejected_then_supervisor_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analysis = await api.run_analysis("fan_out")
    _use_llm(monkeypatch, _reply(_synthesis(BAD_HYPOTHESIS)))
    draft = await api.start_case(_top_alert(analysis).alert_id)
    assert draft.status == "ok" and draft.case_status == CaseStatus.NEEDS_REVISION

    rejected = await api.resume_case(draft.case_id, _decision(action="dismiss"))
    assert rejected.status == "rejected" and "only a supervisor" in rejected.error
    assert rejected.audit_event is None

    ok = await api.resume_case(
        draft.case_id, _decision(action="dismiss", actor_role="supervisor", actor_id="sup_1")
    )
    assert ok.status == "ok" and ok.case_status == CaseStatus.DISMISSED
    assert api.verify_audit_chain().ok


async def test_alert_graph_highlights_planted_evidence() -> None:
    analysis = await api.run_analysis("fan_out")
    alert = _top_alert(analysis)
    graph = await api.get_alert_graph(alert.alert_id, hops=1)
    assert graph.status == "ok"

    bundle = api._ensure()[0].cases.get_evidence(alert.alert_id)
    evidence = {t.transaction_id for t in bundle.transactions}
    highlighted = {e.data.tx_id for e in graph.edges if e.data.highlighted}
    assert highlighted == evidence
    assert all(isinstance(e.data.amount, str) for e in graph.edges)
    node_ids = {n.data.id for n in graph.nodes}
    assert all({e.data.source, e.data.target} <= node_ids for e in graph.edges)

    dumped = graph.model_dump(mode="json")
    assert set(dumped["edges"][0]["data"]) == {
        "id", "source", "target", "amount", "timestamp", "tx_id", "highlighted"
    }
    assert set(dumped["nodes"][0]["data"]) == {"id", "label", "risk", "quality"}

    assert (await api.get_alert_graph("alrt_missing")).status == "error"
    assert (await api.get_alert_graph(alert.alert_id, hops=9)).status == "error"


async def test_every_result_model_round_trips(template_llm: None) -> None:
    analysis = await api.run_analysis("fan_out")
    alert_id = _top_alert(analysis).alert_id
    draft = await api.start_case(alert_id)
    rejected = await api.resume_case(draft.case_id, _decision(reason=" "))
    assert rejected.status == "rejected"
    results: list[BaseModel] = [
        analysis,
        await api.run_analysis("bad_batch"),
        draft,
        await api.start_case("alrt_missing"),
        rejected,
        await api.resume_case(draft.case_id, _decision()),
        await api.copilot_query("What should be documented when escalating an alert?"),
        await api.copilot_query("  "),
        await api.get_alert_graph(alert_id),
        api.verify_audit_chain(),
        *api.get_trace(analysis.trace_id),
    ]
    for result in results:
        _roundtrip(result)
    for model in (AnalysisResult, CaseDraftResult, CaseDecisionResult, CopilotResult,
                  CytoscapeGraph):
        assert any(isinstance(r, model) for r in results)


def test_ok_result_requires_payload() -> None:
    with pytest.raises(ValueError, match="requires"):
        CaseDraftResult(alert_id="a1")
    with pytest.raises(ValueError, match="requires"):
        CaseDecisionResult(case_id="c1")


async def test_configure_replaces_services() -> None:
    first = api._ensure()[0]
    api.configure()
    assert api._ensure()[0] is not first
