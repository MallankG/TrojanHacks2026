"""Narrative drafter, evidence verifier and the case workflow. The LLM is either the template
provider or a `httpx.MockTransport` Ollama; nothing touches the network."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest

from tests.test_copilot import QUESTION, _reply, _use_llm
from tests.test_mocks import PLANTED
from tracelayer_agents.config import AgentConfig
from tracelayer_agents.guardrails import find_prohibited
from tracelayer_agents.interfaces import Services
from tracelayer_agents.llm import client as llm
from tracelayer_agents.llm.client import LLMClient, LLMSettings
from tracelayer_agents.mocks import MockPolicyRetriever
from tracelayer_agents.nodes.derive import compute_derived_facts
from tracelayer_agents.nodes.draft_narrative import draft_narrative
from tracelayer_agents.nodes.retrieve_policy import build_query
from tracelayer_agents.nodes.verify import verify_evidence
from tracelayer_agents.registry import get_services
from tracelayer_agents.schemas import (
    CaseStatus,
    EvidenceBundle,
    EvidenceTransaction,
    FlowFindings,
    FlowPattern,
    NarrativeClaim,
    NarrativeDraft,
    RetrievedChunk,
)
from tracelayer_agents.workflows.analysis import run_analysis
from tracelayer_agents.workflows.case import NODES, start_case
from tracelayer_agents.workflows.copilot import run_copilot
from tracelayer_agents.wrappers import NodeContext, NodeError


@pytest.fixture
def template_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "_default_client", LLMClient(LLMSettings(provider="template")))


# --------------------------------------------------------------------------- small bundle


def _tx(tx_id: str, amount: str, origin: str, dest: str, day: int, hour: int = 10):
    return EvidenceTransaction(
        transaction_id=tx_id, source_row_ref=f"row_{tx_id}",
        timestamp=datetime(2026, 9, day, hour, tzinfo=UTC), amount=Decimal(amount),
        currency="USD", origin_account_id=origin, destination_account_id=dest,
    )


BUNDLE = EvidenceBundle(
    alert_id="alrt_small",
    transactions=[
        _tx("TX-1", "9950.00", "ACC-1", "ACC-2", 1),
        _tx("TX-2", "4000.00", "ACC-2", "ACC-3", 2, 22),
        _tx("TX-3", "1500.50", "ACC-2", "ACC-4", 2, 23),
    ],
    flow_findings=FlowFindings(
        alert_id="alrt_small", graph_snapshot_id="g",
        pass_through=[FlowPattern(account_ids=["ACC-1", "ACC-2", "ACC-3"],
                                  transaction_ids=["TX-1", "TX-2"])],
        evidence_tx_ids=["TX-1", "TX-2", "TX-3"],
    ),
    bundle_hash="0" * 64,
)
SOURCES = [RetrievedChunk(chunk_id="c1", doc_title="Doc", source_uri="mock://d",
                          excerpt="Pass-through activity is a red flag.", score=1.0)]


def _clean_draft() -> NarrativeDraft:
    return NarrativeDraft(
        summary=(
            "On 2026-09-01 ACC-1 sent $9,950 to ACC-2, which sent 4000.00 USD onward on "
            "9/2/2026; the evidence totals $15,450.50."
        ),
        observed_facts=[
            NarrativeClaim(
                text="TX-1 moved $9,950.00 from ACC-1 to ACC-2 on 2026-09-01.", claim_type="fact",
                transaction_ids=["TX-1"], account_ids=["ACC-1", "ACC-2"],
                amounts=[Decimal("9950.00")], dates=[date(2026, 9, 1)],
            ),
            NarrativeClaim(
                text="ACC-2 sent USD 5,500.50 across two transactions.", claim_type="fact",
                transaction_ids=["TX-2", "TX-3"], account_ids=["ACC-2"],
                amounts=[Decimal("5500.50")],
            ),
            NarrativeClaim(
                text="This may be pass-through activity; it may have a legitimate explanation.",
                claim_type="hypothesis", transaction_ids=["TX-1", "TX-2"],
            ),
            NarrativeClaim(
                text="Policy treats rapid pass-through as a red flag.", claim_type="policy",
                citation_chunk_ids=["c1"],
            ),
        ],
        uncertainties=["The purpose of the transfer from ACC-1 is unknown."],
        provider="ollama", model="m", prompt_version="narrative_v1",
    )


def _with_claim(index: int, **update) -> NarrativeDraft:
    draft = _clean_draft()
    claims = list(draft.observed_facts)
    claims[index] = claims[index].model_copy(update=update)
    return draft.model_copy(update={"observed_facts": claims})


def _codes(draft: NarrativeDraft) -> set[tuple[int | None, str]]:
    return {(e.claim_index, e.code) for e in verify_evidence(draft, BUNDLE, SOURCES)}


# --------------------------------------------------------------------------- derived facts


def test_derived_facts_are_decimal_and_stable() -> None:
    facts = compute_derived_facts(BUNDLE)
    assert facts == compute_derived_facts(BUNDLE)
    assert [f.fact_id for f in facts] == [f"DF-{i}" for i in range(1, len(facts) + 1)]
    by_label = {f.label: f for f in facts}
    total = by_label["total amount of evidence transactions (USD)"]
    assert total.value == "15450.50" and total.transaction_ids == ["TX-1", "TX-2", "TX-3"]
    assert by_label["evidence transaction count"].value == "3"
    assert by_label["distinct accounts in evidence"].value == "4"
    assert by_label["ACC-2 inflow total (USD)"].value == "9950.00"
    out = by_label["ACC-2 outflow total (USD)"]
    assert out.value == "5500.50" and out.transaction_ids == ["TX-2", "TX-3"]
    assert by_label["ACC-2 outflow transaction count"].value == "2"
    assert by_label["time span from first to last evidence transaction (hours)"].value == "37.00"
    assert by_label["pass_through pattern 1 total amount"].value == "13950.00"
    assert all(f.transaction_ids for f in facts)


# --------------------------------------------------------------------------- verifier


def test_clean_draft_passes() -> None:
    assert verify_evidence(_clean_draft(), BUNDLE, SOURCES) == []


@pytest.mark.parametrize(
    ("draft", "expected"),
    [
        (_with_claim(0, transaction_ids=["TX-1", "TX-999"]), (0, "UNKNOWN_TX")),
        (_with_claim(0, account_ids=["ACC-1", "ACC-777"]), (0, "UNKNOWN_ACCOUNT")),
        (_with_claim(0, amounts=[Decimal("10000.00")]), (0, "UNSUPPORTED_AMOUNT")),
        (_with_claim(0, amounts=[Decimal("9950.004")]), (0, "UNSUPPORTED_AMOUNT")),
        (_with_claim(0, dates=[date(2026, 8, 31)]), (0, "UNSUPPORTED_DATE")),
        (_with_claim(0, transaction_ids=[]), (0, "MISSING_EVIDENCE")),
        (_with_claim(3, citation_chunk_ids=[]), (3, "BAD_CITATION")),
        (_with_claim(3, citation_chunk_ids=["made_up_9"]), (3, "BAD_CITATION")),
        (_with_claim(0, text="TX-1 moved $10,000 from ACC-1."), (0, "UNGROUNDED_TOKEN")),
        (_with_claim(0, text="TX-1 went to ACC-9 on 2026-09-01."), (0, "UNGROUNDED_TOKEN")),
        (_with_claim(2, text="ACC-1 committed fraud here."), (2, "PROHIBITED_LANGUAGE")),
    ],
    ids=[
        "fake_tx", "unknown_account", "wrong_amount", "sub_cent_amount", "hallucinated_date",
        "fact_without_tx", "uncited_policy", "fake_citation", "amount_in_text", "account_in_text",
        "banned_phrase",
    ],
)
def test_bad_claim_is_caught(draft: NarrativeDraft, expected: tuple[int, str]) -> None:
    assert expected in _codes(draft)


@pytest.mark.parametrize(
    "summary",
    [
        "In total $12,345.67 moved through ACC-2.",
        "ACC-2 received 9950.00 EUR.",
        "Funds moved on 2026-10-05.",
        "Funds moved on 9/3/2026.",
        "TX-404 is part of the activity.",
        "This case is SAR-ready.",
    ],
)
def test_summary_free_text_is_swept(summary: str) -> None:
    draft = _clean_draft().model_copy(update={"summary": summary})
    codes = _codes(draft)
    assert codes and {i for i, _ in codes} == {-1}
    assert codes <= {(-1, "UNGROUNDED_TOKEN"), (-1, "PROHIBITED_LANGUAGE")}


def test_uncertainties_are_swept() -> None:
    draft = _clean_draft().model_copy(
        update={"uncertainties": ["A SAR filed last year may exist for $250.00."]}
    )
    assert _codes(draft) == {(-1, "PROHIBITED_LANGUAGE"), (-1, "UNGROUNDED_TOKEN")}


def test_wrong_amount_ten_thousand_vs_real_9950() -> None:
    draft = _with_claim(0, amounts=[Decimal("10000")], text="TX-1 moved $10,000.00.")
    errors = verify_evidence(draft, BUNDLE, SOURCES)
    assert {(e.claim_index, e.code) for e in errors} == {
        (0, "UNSUPPORTED_AMOUNT"), (0, "UNGROUNDED_TOKEN")
    }
    assert any("10000" in e.detail for e in errors)


@pytest.mark.parametrize(
    ("text", "hits"),
    [
        ("ACC-1 is laundering funds", ["is laundering"]),
        ("a known MONEY  LAUNDERER", ["MONEY  LAUNDERER"]),
        ("we will file a SAR on ACC-1", ["file a SAR on"]),
        ("SAR-ready for submission", ["SAR-ready"]),
        ("the customer is guilty", ["guilty"]),
        ("unguilty laundering-adjacent typologies and SARS", []),
        ("anti-money laundering program", []),
    ],
)
def test_prohibited_phrases_use_word_boundaries(text: str, hits: list[str]) -> None:
    assert find_prohibited(text) == hits


# --------------------------------------------------------------------------- template


async def _scenario_case(scenario: str, services: Services):
    """The saved bundle covering the most planted transactions, its alert, and retrieval for it."""
    state = await run_analysis(scenario, "strict", services)
    planted = set(PLANTED[scenario])
    alerts = {a.alert_id: a for a in state["alerts"]}
    bundles = [b for b in map(services.cases.get_evidence, alerts) if b is not None]
    bundle = max(bundles, key=lambda b: len(planted & {t.transaction_id for t in b.transactions}))
    alert = alerts[bundle.alert_id]
    chunks = MockPolicyRetriever().search(
        build_query({"alert": alert, "evidence_bundle": bundle}), {}, 5
    )
    return alert, bundle, chunks


@pytest.mark.parametrize("scenario", ["fan_out", "pass_through", "cycle"])
async def test_template_narrative_passes_verifier(scenario: str, template_llm: None) -> None:
    services = get_services()
    alert, bundle, chunks = await _scenario_case(scenario, services)
    ctx = NodeContext(services=services, config=AgentConfig())
    update = await draft_narrative.bind(ctx)({
        "trace_id": "tr_t", "alert": alert, "evidence_bundle": bundle,
        "derived_facts": compute_derived_facts(bundle), "retrieved_sources": chunks,
    })
    draft = update["draft_narrative"]
    assert draft.provider == "template" and draft.fallback_used
    assert verify_evidence(draft, bundle, chunks) == []
    types = {c.claim_type for c in draft.observed_facts}
    assert {"fact", "hypothesis", "policy"} <= types
    assert set(PLANTED[scenario]) & set(draft.evidence_transaction_ids)
    assert set(draft.citations) <= {c.chunk_id for c in chunks}


def test_template_passes_with_quality_caveats_and_no_sources() -> None:
    from tracelayer_agents.llm.templates import narrative
    from tracelayer_agents.schemas import QualityIssue

    caveat = QualityIssue(check_id="q1", rule_id="amount_positive", asset_id="a",
                          severity="warning", affected_rows=3, owner_role="o", steward_role="s")
    bundle = BUNDLE.model_copy(update={"quality_caveats": [caveat]})
    out = narrative({
        "transactions": [t.model_dump(mode="json") for t in bundle.transactions],
        "flow_findings": bundle.flow_findings.model_dump(mode="json"),
        "derived_facts": [f.model_dump(mode="json") for f in compute_derived_facts(bundle)],
        "quality_caveats": [caveat.model_dump(mode="json")],
        "chunks": [],
    })
    draft = NarrativeDraft(**out.model_dump(), provider="template", model="template",
                           prompt_version="narrative_v1")
    assert verify_evidence(draft, bundle, []) == []
    assert any("amount_positive" in u for u in draft.uncertainties)


# --------------------------------------------------------------------------- workflow


async def test_start_case_template_is_drafted(template_llm: None) -> None:
    services = get_services()
    await run_analysis("fan_out", "strict", services)
    state_alert = min(services.cases.alerts.values(), key=lambda a: a.rank or 0)
    result = await start_case(state_alert.alert_id, services=services)
    assert result.case_status == CaseStatus.DRAFTED
    assert result.verifier_errors == [] and result.flagged_claims == []
    assert result.llm_meta.provider == "template" and result.draft.fallback_used
    assert [e.node for e in result.trace] == [n.name for n in NODES]
    assert all(e.status == "ok" and e.trace_id == result.trace_id for e in result.trace)


def _llm_draft(bundle: EvidenceBundle, tx_id: str | None = None) -> dict:
    """A draft as the (mock) model returns it; `tx_id` overrides the real transaction id."""
    tx = bundle.transactions[0]
    return {
        "summary": "One evidence transaction is described below.",
        "observed_facts": [{
            "text": "The first evidence transaction moved funds between two accounts.",
            "claim_type": "fact",
            "transaction_ids": [tx_id or tx.transaction_id],
            "account_ids": [tx.origin_account_id, tx.destination_account_id],
            "amounts": [str(tx.amount)],
            "dates": [tx.timestamp.date().isoformat()],
            "citation_chunk_ids": [],
        }],
        "uncertainties": ["The purpose of the transfer is unknown."],
        "follow_up_questions": [],
    }


async def _fan_out_alert(services: Services):
    state = await run_analysis("fan_out", "strict", services)
    return state["evidence_bundle"]


async def test_retry_bad_then_good_is_drafted(monkeypatch: pytest.MonkeyPatch) -> None:
    services = get_services()
    bundle = await _fan_out_alert(services)
    replies = iter([_llm_draft(bundle, "TX-FAKE-1"), _llm_draft(bundle)])
    seen: list[str] = []

    def chat(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["messages"][-1]["content"])
        return httpx.Response(200, json={"message": {"content": json.dumps(next(replies))}})

    _use_llm(monkeypatch, chat)
    result = await start_case(bundle.alert_id, services=services)

    assert result.case_status == CaseStatus.DRAFTED
    assert result.verifier_errors == [] and result.flagged_claims == []
    assert result.llm_meta.provider == "ollama" and not result.draft.fallback_used
    assert [e.node for e in result.trace].count("draft_narrative") == 2
    assert len(seen) == 2
    assert "(none)" in seen[0].split("previous draft")[1]
    assert "UNKNOWN_TX" in seen[1] and "TX-FAKE-1" in seen[1]
    # sources reach the model wrapped and labelled as untrusted
    assert '<source id="' in seen[0] and "untrusted" in seen[0]


async def test_always_bad_stops_after_two_needs_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    services = get_services()
    bundle = await _fan_out_alert(services)
    _use_llm(monkeypatch, _reply(_llm_draft(bundle, "TX-FAKE-1")))

    result = await start_case(bundle.alert_id, services=services)

    assert result.case_status == CaseStatus.NEEDS_REVISION
    assert result.flagged_claims == [0]
    assert [(e.claim_index, e.code) for e in result.verifier_errors] == [(0, "UNKNOWN_TX")]
    assert result.draft.observed_facts[0].transaction_ids == ["TX-FAKE-1"]  # draft kept
    nodes = [e.node for e in result.trace]
    assert nodes.count("draft_narrative") == 2 and nodes.count("verify_evidence") == 2
    assert nodes[-1] == "verify_evidence"


async def test_unknown_alert_fails_in_load_case(template_llm: None) -> None:
    with pytest.raises(NodeError) as info:
        await start_case("alrt_missing", services=get_services())
    assert info.value.node == "load_case"
    assert info.value.state["trace_events"][-1].status == "error"


# --------------------------------------------------------------------------- copilot guardrail


async def test_copilot_sar_filed_is_flagged_not_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_llm(monkeypatch, _reply({
        "answer": "Document the rationale. A SAR filed earlier does not change this.",
        "claims": [
            {"text": "The SAR filed for this account covers it.",
             "citation_chunk_ids": ["mock_ffiec_sar_program_001"]},
            {"text": "Record the evidence reviewed.",
             "citation_chunk_ids": ["mock_ffiec_sar_program_001"]},
        ],
        "unknowns": [],
        "no_answer": False,
    }))
    result = await run_copilot(QUESTION)
    flagged = {(e.claim_index, e.code) for e in result.verifier_errors}
    assert flagged == {(None, "PROHIBITED_LANGUAGE"), (0, "PROHIBITED_LANGUAGE")}
    assert len(result.answer.claims) == 2  # flagged, not dropped
    assert "SAR filed" in result.answer.answer
