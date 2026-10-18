"""Human decision, record_decision, case persistence and the audit hash chain."""

from __future__ import annotations

import pytest

from tests.test_case_workflow import BAD_HYPOTHESIS, _synthesis
from tests.test_copilot import _reply, _use_llm
from tracelayer_agents.audit import record, verify_chain
from tracelayer_agents.interfaces import Services
from tracelayer_agents.llm import client as llm
from tracelayer_agents.llm.client import LLMClient, LLMSettings
from tracelayer_agents.mocks import InMemoryAuditRepository
from tracelayer_agents.nodes.human_decision import DecisionRejected, validate_decision
from tracelayer_agents.registry import get_services
from tracelayer_agents.schemas import CaseStatus, Decision
from tracelayer_agents.workflows.analysis import run_analysis
from tracelayer_agents.workflows.case import (
    CHECKPOINTER,
    build_case_workflow,
    resume_case,
    start_case,
)


@pytest.fixture
def template_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "_default_client", LLMClient(LLMSettings(provider="template")))


def _decision(**update) -> dict:
    return {"action": "escalate", "actor_role": "analyst", "actor_id": "user_1",
            "reason": "Fan-out to new accounts within hours; escalate for review."} | update


async def _paused_case(services: Services):
    state = await run_analysis("fan_out", "strict", services)
    return await start_case(state["evidence_bundle"].alert_id, services=services)


async def _next_nodes(services: Services, case_id: str) -> tuple[str, ...]:
    snapshot = await build_case_workflow(services).aget_state(
        {"configurable": {"thread_id": case_id}}
    )
    return snapshot.next


# --------------------------------------------------------------------------- full flow


async def test_full_flow_escalate_and_chain_verifies(template_llm: None) -> None:
    services = get_services()
    draft = await _paused_case(services)
    assert draft.case_status == CaseStatus.AWAITING_REVIEW
    assert await _next_nodes(services, draft.case_id) == ("human_decision",)
    case = services.cases.get_case(draft.case_id)
    assert case is not None and case.status == CaseStatus.AWAITING_REVIEW
    assert len(services.cases.list_drafts(draft.case_id)) == 1

    result = await resume_case(draft.case_id, _decision(), services=services)

    assert result.case_status == CaseStatus.ESCALATED
    assert services.cases.get_case(draft.case_id).status == CaseStatus.ESCALATED
    assert [e.node for e in result.trace][-2:] == ["human_decision", "record_decision"]

    event = result.audit_event
    assert event.action == "case.decision" and event.object_id == draft.case_id
    assert (event.actor_id, event.actor_role) == ("user_1", "analyst")
    assert event.reason == _decision()["reason"]
    assert event.before_hash and event.after_hash and event.before_hash != event.after_hash
    assert event.prompt_version == draft.draft.prompt_version
    assert event.model_versions == services.cases.get_alert(draft.alert_id).model_versions
    assert event.metadata["action"] == "escalate"
    assert event.metadata["bundle_hash"] == services.cases.get_evidence(
        draft.alert_id).bundle_hash
    assert event.metadata["graph_snapshot_id"]

    events = services.audit.list_events()
    actions = [e.action for e in events]
    assert actions[0] == "analysis.run.started" and "analysis.run.completed" in actions
    assert "alert.created" in actions
    assert actions[-3:] == ["case.draft_generated", "case.verified", "case.decision"]
    drafted = events[-3]
    assert drafted.metadata["provider"] == "template" and drafted.metadata["fallback_used"]
    assert events[-2].metadata["error_count"] == 0
    assert verify_chain(events).ok and verify_chain(events).checked == len(events)


async def test_no_customer_action_beyond_status(template_llm: None) -> None:
    services = get_services()
    draft = await _paused_case(services)
    result = await resume_case(draft.case_id, _decision(action="request_more_data"),
                               services=services)
    assert result.case_status == CaseStatus.MORE_DATA_REQUESTED
    case = services.cases.get_case(draft.case_id)
    assert case.status == CaseStatus.MORE_DATA_REQUESTED
    assert case.disposition == "request_more_data"


# --------------------------------------------------------------------------- RBAC & validation


async def test_analyst_cannot_dismiss_needs_revision_supervisor_can(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = get_services()
    _use_llm(monkeypatch, _reply(_synthesis(BAD_HYPOTHESIS)))
    draft = await _paused_case(services)
    assert draft.case_status == CaseStatus.NEEDS_REVISION
    assert len(services.cases.list_drafts(draft.case_id)) == 2  # every attempt is kept

    with pytest.raises(DecisionRejected, match="only a supervisor"):
        await resume_case(draft.case_id, _decision(action="dismiss"), services=services)
    assert await _next_nodes(services, draft.case_id) == ("human_decision",)
    assert services.cases.get_case(draft.case_id).status == CaseStatus.NEEDS_REVISION

    result = await resume_case(
        draft.case_id, _decision(action="dismiss", actor_role="supervisor", actor_id="sup_1"),
        services=services,
    )
    assert result.case_status == CaseStatus.DISMISSED
    assert result.audit_event.actor_role == "supervisor"
    assert result.audit_event.metadata["previous_status"] == "needs_revision"
    assert verify_chain(services.audit.list_events()).ok


async def test_analyst_may_escalate_needs_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    services = get_services()
    _use_llm(monkeypatch, _reply(_synthesis(BAD_HYPOTHESIS)))
    draft = await _paused_case(services)
    result = await resume_case(draft.case_id, _decision(), services=services)
    assert result.case_status == CaseStatus.ESCALATED


@pytest.mark.parametrize(
    ("update", "match"),
    [
        ({"reason": ""}, "reason"),
        ({"reason": "   "}, "reason"),
        ({"action": "file_sar"}, "action"),
        ({"actor_role": "investigator"}, "actor_role"),
        ({"actor_id": ""}, "actor_id"),
    ],
)
async def test_invalid_decision_rejected_and_still_paused(
    template_llm: None, update: dict, match: str
) -> None:
    services = get_services()
    draft = await _paused_case(services)
    with pytest.raises(DecisionRejected, match=match):
        await resume_case(draft.case_id, _decision(**update), services=services)
    assert await _next_nodes(services, draft.case_id) == ("human_decision",)
    # a valid decision still goes through afterwards
    result = await resume_case(draft.case_id, _decision(), services=services)
    assert result.case_status == CaseStatus.ESCALATED


def test_validate_decision_rules() -> None:
    assert validate_decision(_decision(), CaseStatus.AWAITING_REVIEW).action == "escalate"
    assert validate_decision(_decision(action="dismiss"), CaseStatus.AWAITING_REVIEW)
    assert validate_decision(Decision(**_decision(action="dismiss", actor_role="supervisor")),
                             CaseStatus.NEEDS_REVISION)
    with pytest.raises(DecisionRejected):
        validate_decision(_decision(action="dismiss"), CaseStatus.NEEDS_REVISION)
    with pytest.raises(DecisionRejected):
        validate_decision({"action": "escalate"}, CaseStatus.AWAITING_REVIEW)


async def test_resume_twice_errors(template_llm: None) -> None:
    services = get_services()
    draft = await _paused_case(services)
    await resume_case(draft.case_id, _decision(), services=services)
    with pytest.raises(ValueError, match="not awaiting a decision"):
        await resume_case(draft.case_id, _decision(), services=services)
    decisions = [e for e in services.audit.list_events() if e.action == "case.decision"]
    assert len(decisions) == 1


async def test_unknown_case_errors() -> None:
    with pytest.raises(KeyError, match="unknown case_id"):
        await resume_case("case_does_not_exist", _decision(), services=get_services())


def test_checkpointer_is_shared() -> None:
    services = get_services()
    assert build_case_workflow(services).checkpointer is CHECKPOINTER


# --------------------------------------------------------------------------- audit chain


def _chain(n: int) -> InMemoryAuditRepository:
    repo = InMemoryAuditRepository()
    for i in range(n):
        record(repo, action=f"test.{i}", object_type="case", object_id="c1", reason=f"r{i}")
    return repo


def test_verify_chain_empty_and_valid() -> None:
    assert verify_chain([]).ok
    result = verify_chain(_chain(4).list_events())
    assert result.ok and result.checked == 4 and result.first_bad_index is None


@pytest.mark.parametrize("index", [0, 2, 4])
def test_tampered_event_is_found(index: int) -> None:
    repo = _chain(5)
    stored = repo._events[index]
    repo._events[index] = stored.model_copy(update={"reason": "edited after the fact"})
    result = verify_chain(repo.list_events())
    assert not result.ok
    assert result.first_bad_index == index and result.checked == index
    assert "hash mismatch" in result.reason


def test_broken_link_is_found() -> None:
    events = _chain(4).list_events()
    del events[1]  # event 2 now points at a hash that isn't its predecessor's
    result = verify_chain(events)
    assert not result.ok and result.first_bad_index == 1
    assert "prev_hash" in result.reason


async def test_tampering_with_stored_case_decision_is_found(template_llm: None) -> None:
    services = get_services()
    draft = await _paused_case(services)
    await resume_case(draft.case_id, _decision(), services=services)
    events = services.audit._events
    index = next(i for i, e in enumerate(events) if e.action == "case.decision")
    events[index] = events[index].model_copy(update={"actor_role": "supervisor"})
    result = verify_chain(services.audit.list_events())
    assert not result.ok and result.first_bad_index == index


def test_record_human_actor() -> None:
    repo = InMemoryAuditRepository()
    event = record(repo, action="case.decision", object_type="case", object_id="c1",
                   actor_id="user_1", actor_role="analyst", reason="r",
                   before_hash="a" * 64, after_hash="b" * 64, prompt_version="narrative_v2")
    assert (event.actor_id, event.actor_role) == ("user_1", "analyst")
    assert event.before_hash == "a" * 64 and event.prompt_version == "narrative_v2"
    assert verify_chain(repo.list_events()).ok


async def test_failed_decision_saves_no_edit(template_llm: None,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    services = get_services()
    draft = await _paused_case(services)

    def fail(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(services.cases, "apply_decision", fail, raising=False)
    monkeypatch.setattr(services.cases, "update_case", fail)
    with pytest.raises(Exception, match="db down"):  # noqa: B017 (wrapped by the node runner)
        await resume_case(draft.case_id, _decision(edited_summary="An analyst edit."),
                          services=services)
    assert len(services.cases.list_drafts(draft.case_id)) == 1
    assert not [e for e in services.audit.list_events() if e.action == "case.narrative_edited"]
