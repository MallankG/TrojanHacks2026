"""PRD 8.2 copilot acceptance, end to end through the facade on the seeded fan_out case (mocks,
the template LLM or a `httpx.MockTransport` Ollama; nothing touches the network).

A) explain the alert, trace backward and forward, cite a policy chunk, surface a missing-data
   caveat and propose a next step; then a synthesis carrying a fabricated transaction id and a
   wrong amount is flagged or dropped by the verifiers before anything is displayed.
B) a question on another case or tenant is denied and audited without revealing a foreign id; an
   unbounded graph question is narrowed to the case bounds and audited.
"""

from __future__ import annotations

import json

import httpx
import pytest

from tests.test_chat import _llm_replies, _open_case, _open_chat
from tests.test_copilot import _use_llm
from tracelayer_agents import api
from tracelayer_agents.llm import client as llm
from tracelayer_agents.llm.client import LLMClient, LLMSettings
from tracelayer_agents.schemas import NARRATIVE_SECTIONS, CaseStatus

FAKE_TX = "TX-99999"
WRONG_AMOUNT = "$12,345.67"


@pytest.fixture(autouse=True)
def fresh_facade() -> None:
    api.configure()


@pytest.fixture(autouse=True)
def template_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "_default_client", LLMClient(LLMSettings(provider="template")))


def _actions() -> list[str]:
    services, _ = api._ensure()
    return [e.action for e in services.audit.list_events()]


def _events(action: str):
    services, _ = api._ensure()
    return [e for e in services.audit.list_events() if e.action == action]


# --------------------------------------------------------------------------- A


async def test_a_fan_out_walkthrough_with_provenance() -> None:
    case_id = await _open_case("fan_out")
    services, _ = api._ensure()
    alert = services.cases.get_alert(services.cases.get_case(case_id).alert_id)
    bundle = services.cases.get_evidence(alert.alert_id)
    latest = max(t.timestamp for t in bundle.transactions)
    chat_id = (await api.start_chat(case_id, "analyst_1", "analyst")).chat.chat_id

    explain = (await api.send_message(chat_id, chip="explain_alert")).turn
    assert explain.status == "ok" and explain.verification_status == "verified"
    inferences = [c for c in explain.claims if c.label == "model_inference"]
    assert inferences and all(c.model_run_id == alert.model_run_id for c in inferences)

    backward = (await api.send_message(chat_id, chip="trace_backward")).turn
    forward = (await api.send_message(chat_id, chip="trace_forward")).turn
    assert [t.tool for t in backward.tool_calls] == ["trace_backward"]
    assert backward.claims and not backward.gaps
    hops = [c for c in forward.claims if c.edge_ids]
    assert len(hops) == 9 and {i for c in hops for i in c.edge_ids} == {
        t.transaction_id for t in bundle.transactions}

    policy = (await api.send_message(chat_id, chip="policy_guidance")).turn
    cited = [c for c in policy.claims if c.label == "policy"]
    assert cited and all(c.chunk_ids for c in cited)
    sources = {s.chunk_id for s in policy.metadata.retrieved_sources}
    assert {i for c in cited for i in c.chunk_ids} <= sources

    missing = (await api.send_message(chat_id, chip="missing_data")).turn
    caveats = [c.text for c in missing.claims if c.label == "missing_evidence"]
    assert any("KYC" in t for t in caveats)
    assert missing.metadata.coverage.unsupported >= len(caveats)

    steps = (await api.send_message(chat_id, chip="next_steps")).turn
    assert steps.next_steps and all("escalat" not in s.lower() for s in steps.next_steps)

    for turn in (explain, backward, forward, policy, missing, steps):
        meta = turn.metadata
        assert meta.as_of == latest and meta.as_of_basis == "latest_evidence_timestamp"
        assert meta.graph_snapshot_id == alert.graph_snapshot_id
        assert meta.context_version == 1
        assert [a.tool for a in meta.tool_actions] == [t.tool for t in turn.tool_calls]
        assert meta.coverage.verified + meta.coverage.partial == len(
            [c for c in turn.claims if c.label != "missing_evidence"])
    assert _actions().count("chat.turn") == 6


async def test_a_fabricated_id_and_wrong_amount_never_reach_the_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat_id = await _open_chat()
    _llm_replies(monkeypatch, answer={
        "summary": f"Funds moved onward via {FAKE_TX} for a total of {WRONG_AMOUNT}.",
        "hypotheses": [{"text": f"The transfer {FAKE_TX} may also have a legitimate explanation; "
                                "this requires reviewer assessment.", "fact_refs": [1]}],
        "next_steps": [f"Confirm the {WRONG_AMOUNT} total with the bank.",
                       "Obtain the KYC profile for the hub account."],
    })
    result = await api.send_message(chat_id, chip="trace_forward")
    turn = result.turn
    assert result.status == "ok" and turn.verification_status == "partial"
    codes = {c for g in turn.gaps for c in g.codes}
    assert "UNGROUNDED_TOKEN" in codes
    assert {g.label for g in turn.gaps} >= {"summary", "hypothesis", "next_step"}
    shown = result.model_dump_json()
    assert FAKE_TX not in shown and "12,345.67" not in shown
    assert turn.next_steps == ["Obtain the KYC profile for the hub account."]
    assert turn.metadata.coverage.unsupported >= 3


async def test_a_fabricated_id_and_wrong_amount_are_flagged_in_the_narrative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analysis = await api.run_analysis("fan_out")
    top = min(analysis.alerts, key=lambda a: a.rank or 0)

    def chat(request: httpx.Request) -> httpx.Response:
        payload = {
            "summary": f"The hub sent {WRONG_AMOUNT} through {FAKE_TX}.",
            "hypotheses": [{"text": f"Payments including {FAKE_TX} may have a legitimate "
                                    "explanation; this requires reviewer assessment.",
                            "fact_refs": [1]}],
            "contradictions": [{"text": f"A {WRONG_AMOUNT} return may be consistent with a "
                                        "refund, pending verification.", "fact_refs": [1]}],
            "uncertainties": [], "follow_up_questions": [],
        }
        return httpx.Response(200, json={"message": {"content": json.dumps(payload)}})

    _use_llm(monkeypatch, chat)
    case = await api.start_case(top.alert_id)
    assert case.status == "ok" and case.case_status == CaseStatus.NEEDS_REVISION
    assert case.draft.prompt_version == "narrative_v4"
    details = " ".join(e.detail for e in case.verifier_errors if e.code == "UNGROUNDED_TOKEN")
    assert FAKE_TX in details and "12,345.67" in details
    assert set(case.flagged_claims)  # the hypothesis claim naming the fake id is flagged
    assert _events("case.verified")[-1].metadata["error_count"] == len(case.verifier_errors)


async def test_a_narrative_sections_cite_evidence_and_edits_are_versioned() -> None:
    analysis = await api.run_analysis("fan_out")
    top = min(analysis.alerts, key=lambda a: a.rank or 0)
    case = await api.start_case(top.alert_id)
    assert case.status == "ok" and not case.verifier_errors
    sections = case.draft.sections
    for name in NARRATIVE_SECTIONS:
        for item in getattr(sections, name):
            assert not item.factual or item.evidence_ids, (name, item)
    assert sections.observed_facts and sections.model_rule_inferences
    assert sections.relevant_policy and sections.data_gaps and sections.recommended_next_steps
    assert any("payroll" in i.text for i in sections.contradictory_evidence)
    drafted = _events("case.draft_generated")[-1]
    assert drafted.metadata["draft_hash"] == case.draft_hash

    decided = await api.resume_case(case.case_id, {
        "action": "request_more_data", "actor_role": "analyst", "actor_id": "analyst_1",
        "reason": "Need KYC.", "edited_summary": "Reviewer summary of the outgoing transfers.",
    })
    assert decided.status == "ok" and decided.edited_draft is not None
    edited = decided.edited_draft
    assert edited.version == 2 and edited.parent_draft_hash == case.draft_hash
    assert edited.summary == "Reviewer summary of the outgoing transfers."
    event = _events("case.narrative_edited")[-1]
    assert event.metadata["parent_draft_hash"] == case.draft_hash
    assert event.metadata["draft_hash"] == decided.edited_draft_hash
    assert len(event.metadata["diff_hash"]) == 64
    assert "Reviewer summary" not in json.dumps(event.metadata)  # hashes only
    assert decided.audit_event.metadata["draft_hash"] == decided.edited_draft_hash
    services, _ = api._ensure()
    assert [d.version for d in services.cases.list_drafts(case.case_id)][-1] == 2
    assert api.verify_audit_chain().ok


# --------------------------------------------------------------------------- B


async def test_b_other_case_or_tenant_is_denied_without_foreign_ids() -> None:
    case_a = await _open_case("fan_out")
    case_b = await _open_case("cycle")
    services, _ = api._ensure()
    bundle_b = services.cases.get_evidence(services.cases.get_case(case_b).alert_id)
    foreign = [t.transaction_id for t in bundle_b.transactions]
    chat_id = (await api.start_chat(case_a, "analyst_1", "analyst", tenant_id="t1")).chat.chat_id

    # A question naming another case's records: denied, nothing looked up, no id echoed.
    asked = await api.send_message(chat_id, text=f"Trace {foreign[0]} in case {case_b}.")
    assert asked.status == "denied" and asked.turn.status == "denied"
    assert not asked.turn.tool_calls and not asked.turn.claims and asked.turn.question is None
    shown = asked.model_dump_json()
    assert not any(i in shown for i in [*foreign, case_b])
    denied = _events("chat.request_denied")[-1]
    assert denied.metadata["reasons"] == "out_of_scope_ids"
    assert not any(i in json.dumps(denied.metadata) for i in [*foreign, case_b])

    other_tenant = await api.send_message(chat_id, text="Show the same activity at another bank.")
    assert other_tenant.status == "denied"
    assert _events("chat.request_denied")[-1].metadata["reasons"] == "other_tenant"

    # The access policy refuses a case or a tenant: nothing about the case comes back.
    services.access.denied_case_ids.add(case_b)
    refused = await api.start_chat(case_b, "analyst_1", "analyst", tenant_id="t1")
    assert refused.status == "denied" and refused.chat is None
    services.access.denied_tenant_ids.add("t2")
    assert (await api.start_chat(case_a, "analyst_2", "analyst", tenant_id="t2")).status == "denied"
    assert not any(i in refused.model_dump_json() for i in foreign)

    # Revoked mid-chat: the next message, context change and every tool call are refused.
    services.access.denied_case_ids.add(case_a)
    revoked = await api.send_message(chat_id, chip="trace_forward")
    assert revoked.status == "denied" and revoked.turn is None and revoked.context is None
    assert api.update_chat_context(chat_id, ["TX-51001"], confirm=True).status == "denied"
    assert api.get_chat(chat_id).status == "denied"
    reasons = [e.metadata["reasons"] for e in _events("chat.request_denied")]
    assert reasons.count("access_denied") >= 5
    assert api.verify_audit_chain().ok


async def test_b_unbounded_graph_question_is_narrowed() -> None:
    chat_id = await _open_chat()
    result = await api.send_message(
        chat_id, text="Trace where the money went forward 6 hops across all accounts, all time.")
    turn = result.turn
    assert result.status == "ok" and turn.claims
    assert [(t.tool, t.args["hops"]) for t in turn.tool_calls] == [("trace_forward", 3)]
    assert len(turn.bounds_applied) == 3 and any("3 hops" in b for b in turn.bounds_applied)
    narrowed = _events("chat.request_narrowed")[-1]
    assert narrowed.metadata["bounds"] == "hops_capped,window_limited,entities_limited"
    assert narrowed.metadata["requested_hops"] == 3
    assert "chat.request_denied" not in _actions()
