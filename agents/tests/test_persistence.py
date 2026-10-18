"""Persistence mappings on the fan_out golden path, validated against the backend's own Pydantic
models (loaded from `backend/app/schemas.py` by path, in tests only; skipped if unavailable)."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType

import pytest

from tracelayer_agents import api
from tracelayer_agents.adapters import persistence as p
from tracelayer_agents.llm import client as llm
from tracelayer_agents.llm.client import LLMClient, LLMSettings
from tracelayer_agents.schemas import CaseStatus, Decision

BACKEND_SCHEMAS = Path(__file__).resolve().parents[2] / "backend" / "app" / "schemas.py"
DECISION = Decision(action="escalate", actor_role="analyst", actor_id="user_1",
                    reason="Fan-out to new accounts within hours; escalate for review.",
                    edited_summary="Edited by analyst.")


def _backend() -> ModuleType:
    if not BACKEND_SCHEMAS.exists():
        pytest.skip("backend/app/schemas.py not present")
    spec = importlib.util.spec_from_file_location("_backend_schemas", BACKEND_SCHEMAS)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - any import problem means "not importable"
        pytest.skip(f"backend schemas not importable: {exc}")
    return module


@pytest.fixture(scope="module")
def golden():
    import asyncio

    previous = llm._default_client
    llm._default_client = LLMClient(LLMSettings(provider="template"))
    api.configure()

    async def run():
        analysis = await api.run_analysis("fan_out")
        top = min(analysis.alerts, key=lambda a: a.rank or 0)
        draft = await api.start_case(top.alert_id)
        decided = await api.resume_case(draft.case_id, DECISION)
        return draft, decided

    try:
        draft, decided = asyncio.run(run())
    finally:
        llm._default_client = previous
    services, _ = api._ensure()
    bundle = services.cases.get_evidence(draft.alert_id)
    events = services.audit.list_events()
    assert draft.status == "ok" and decided.status == "ok" and bundle is not None
    return draft, decided, bundle, events


def test_uuid_mapping_is_deterministic_and_per_kind() -> None:
    a = p.to_uuid("transaction", "TX-1")
    assert a == p.to_uuid("transaction", "TX-1") != p.to_uuid("account", "TX-1")
    assert p.to_uuid("transaction", "TX-2") != a
    assert p.UNHOLDABLE_ORIGINAL_IDS


def test_agent_run_create_and_update(golden) -> None:
    draft, decided, _, _ = golden
    schemas = _backend()
    create = p.agent_run_create(draft)
    schemas.AgentRunCreate.model_validate(create)
    assert create["config"]["original_alert_id"] == draft.alert_id
    assert create["alert_id"] == p.to_uuid("alert", draft.alert_id)
    assert create["prompt_version"] == draft.llm_meta.prompt_version

    awaiting = p.agent_run_update(draft.trace, draft.case_status)
    schemas.AgentRunUpdate.model_validate(awaiting)
    assert awaiting["status"] == "awaiting_review"
    assert [s["name"] for s in awaiting["append_steps"]] == [e.node for e in draft.trace]

    done = p.agent_run_update(decided.trace, decided.case_status)
    schemas.AgentRunUpdate.model_validate(done)
    assert done["status"] == "completed" and done["error_message"] is None
    assert p.run_status([], None) == "failed"


def test_narrative_and_verification(golden) -> None:
    draft, _, _, _ = golden
    schemas = _backend()
    body = p.narrative_create(draft, agent_run_uuid=p.to_uuid("agent_run", draft.trace_id))
    schemas.NarrativeCreate.model_validate(body)
    facts = draft.draft.observed_facts
    for claim, ours in zip(body["claims"], facts, strict=False):
        assert claim["referenced_values"]["original_transaction_ids"] == ours.transaction_ids
        assert claim["claim_kind"] == p.CLAIM_KIND[ours.claim_type]
    assert {c["claim_kind"] for c in body["claims"][len(facts):]} <= {"unknown"}
    assert len(body["citations"]) == len(set(draft.draft.citations))

    verification = p.narrative_verification(draft)
    schemas.NarrativeVerification.model_validate(verification)
    assert verification["verification_status"] == "passed"
    assert [c["claim_index"] for c in verification["claims"]] == list(range(len(body["claims"])))

    flagged = draft.model_copy(update={"flagged_claims": [0],
                                       "case_status": CaseStatus.NEEDS_REVISION})
    flagged_v = p.narrative_verification(flagged)
    schemas.NarrativeVerification.model_validate(flagged_v)
    assert flagged_v["verification_status"] == "flagged"
    assert flagged_v["claims"][0]["verification"] == "unsupported"


def test_case_evidence_rows(golden) -> None:
    _, _, bundle, _ = golden
    schemas = _backend()
    rows = p.case_evidence_rows(bundle)
    for row in rows:
        schemas.EvidenceIn.model_validate(row)
    tx_rows = [r for r in rows if r["evidence_type"] == "transaction"]
    assert [r["payload"]["original_transaction_id"] for r in tx_rows] == [
        t.transaction_id for t in bundle.transactions]
    assert any(r["evidence_type"] == "transaction_path" for r in rows)
    json.dumps(rows)


def test_case_decision_row(golden) -> None:
    _, decided, _, _ = golden
    schemas = _backend()
    row = p.case_decision_row(DECISION, decided)
    schemas.DecisionIn.model_validate(row)
    assert (row["decision"], row["disposition"]) == ("escalate", "escalate_for_sar_consideration")
    assert "Edited by analyst." in row["notes"]
    for action in p.DECISION:
        schemas.DecisionIn.model_validate(
            p.case_decision_row(DECISION.model_copy(update={"action": action})))


def test_audit_event_rows(golden) -> None:
    _, decided, _, events = golden
    assert decided.audit_event in events
    for event in events:
        row = p.audit_event_row(event)
        assert re.match(r"^[a-z_]+\.[a-z_]+$", row["action"])
        assert row["metadata"]["original_event_id"] == event.event_id
        assert row["metadata"]["hash"] == event.hash
        assert row["actor_type"] in {"user", "system", "agent"}
        json.dumps(row)
    assert p.audit_action("Case.Draft-Generated.v2") == "case.draft_generated_v_"


def test_sections_and_edited_version(golden) -> None:
    draft, decided, _, events = golden
    schemas = _backend()
    body = p.narrative_create(draft)
    assert "## Contradictory evidence" in body["content"]
    assert "## Recommended next steps" in body["content"]
    facts = [c for c in body["claims"] if c["claim_kind"] != "unknown"]
    assert {c["referenced_values"]["draft_hash"] for c in facts} == {draft.draft_hash}
    assert {c["referenced_values"]["section"] for c in facts} >= {"observed_facts",
                                                                   "relevant_policy"}

    assert decided.edited_draft is not None and decided.edited_draft.version == 2
    parent = p.narrative_uuid(draft.draft_hash)
    edit = p.narrative_edit_create(decided, parent)
    schemas.NarrativeCreate.model_validate(edit)
    assert edit["parent_narrative_id"] == parent
    assert edit["content"].startswith("Edited by analyst.")
    assert {c["referenced_values"]["draft_version"] for c in edit["claims"]
            if c["claim_kind"] != "unknown"} == {2}
    edited = next(e for e in events if e.action == "case.narrative_edited")
    assert p.audit_event_row(edited)["action"] == "case.narrative_edited"
    with pytest.raises(ValueError):
        p.narrative_edit_create(decided.model_copy(update={"edited_draft": None}), parent)
