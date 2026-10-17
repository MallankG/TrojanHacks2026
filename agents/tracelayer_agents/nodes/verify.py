"""Deterministic verifiers (CLAUDE.md rule 3): nothing an LLM wrote reaches the user unless it
traces back to evidence or retrieved sources."""

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from tracelayer_agents.guardrails import find_prohibited
from tracelayer_agents.llm.templates import POLICY_NO_ANSWER
from tracelayer_agents.nodes.derive import compute_derived_facts
from tracelayer_agents.schemas import (
    CaseStatus,
    EvidenceBundle,
    NarrativeDraft,
    PolicyAnswer,
    RetrievedChunk,
    VerifierError,
    VerifyCitationsOutput,
    VerifyEvidenceOutput,
)
from tracelayer_agents.wrappers import NodeContext, traced_node

# --------------------------------------------------------------------------- copilot


def check_citations(
    answer: PolicyAnswer, sources: list[RetrievedChunk]
) -> tuple[PolicyAnswer, list[VerifierError]]:
    """Drop every claim with no citation or with a chunk_id that wasn't retrieved, and rebuild
    `citations` from the claims that remain. An answer left with no supported claim becomes
    `no_answer=True`, since its prose can no longer be traced to a source."""
    known = {c.chunk_id for c in sources}
    errors: list[VerifierError] = []
    kept = []
    for i, claim in enumerate(answer.claims):
        if not claim.citation_chunk_ids:
            errors.append(VerifierError(claim_index=i, code="missing_citation",
                                        detail="claim cites no chunk_id"))
            continue
        unknown = [cid for cid in claim.citation_chunk_ids if cid not in known]
        if unknown:
            # TODO(review): drops the whole claim even if it also cites a real chunk; the more
            # conservative choice, since the claim may rest on the fabricated source.
            errors.append(VerifierError(claim_index=i, code="unknown_citation",
                                        detail=f"chunk_ids not retrieved: {unknown}"))
            continue
        kept.append(claim)

    update: dict[str, Any] = {
        "claims": kept,
        "citations": list(dict.fromkeys(cid for c in kept for cid in c.citation_chunk_ids)),
    }
    if not kept and not answer.no_answer:
        errors.append(VerifierError(claim_index=None, code="missing_citation",
                                    detail="answer has no claim supported by a retrieved chunk"))
        update |= {"no_answer": True, "answer": POLICY_NO_ANSWER}
    return answer.model_copy(update=update), errors


def check_prohibited(answer: PolicyAnswer) -> list[VerifierError]:
    """Flag (never drop) prohibited language in the answer text, each claim (by its index as the
    LLM returned it) and the unknowns."""
    texts = [(None, answer.answer)]
    texts += [(i, c.text) for i, c in enumerate(answer.claims)]
    texts += [(None, u) for u in answer.unknowns]
    return [
        VerifierError(claim_index=index, code="PROHIBITED_LANGUAGE",
                      detail=f"prohibited phrase: {phrase!r}")
        for index, text in texts
        for phrase in find_prohibited(text)
    ]


@traced_node("verify_citations", VerifyCitationsOutput)
def verify_citations(state: Mapping[str, Any], ctx: NodeContext) -> VerifyCitationsOutput:
    answer = state["answer"]
    prohibited = check_prohibited(answer)
    answer, errors = check_citations(answer, state.get("retrieved_sources") or [])
    return VerifyCitationsOutput(answer=answer, verifier_errors=errors + prohibited)


# --------------------------------------------------------------------------- case narrative

FREE_TEXT = -1  # claim_index for text outside observed_facts

_CURRENCIES = r"(?:USD|EUR|GBP|CAD|MXN|JPY|CHF|AUD|CNY|INR|HKD|SGD)"
_NUMBER = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
ID_RE = re.compile(r"\b(?:TX|ACC)-[A-Za-z0-9]+\b")
MONEY_RE = re.compile(
    rf"\$\s?(?P<a>{_NUMBER})"
    rf"|\b(?P<b>{_NUMBER})\s?(?P<bcur>{_CURRENCIES})\b"
    rf"|\b(?P<ccur>{_CURRENCIES})\s?(?P<c>{_NUMBER})"
)
ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})(?!\d)")
US_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")


@dataclass(frozen=True)
class Grounding:
    """Everything a narrative may mention: the bundle's ids, amounts and dates, plus derived
    amounts. `money` pairs each amount with its currency, for amounts written in text."""

    tx_ids: frozenset[str]
    account_ids: frozenset[str]
    amounts: frozenset[Decimal]
    money: frozenset[tuple[Decimal, str]]
    dates: frozenset[date]


def grounding(bundle: EvidenceBundle) -> Grounding:
    txs = bundle.transactions
    accounts = {a for t in txs for a in (t.origin_account_id, t.destination_account_id)}
    money = {(t.amount, t.currency) for t in txs}
    dates = {t.timestamp.date() for t in txs}
    findings = bundle.flow_findings
    if findings is not None:
        for kind in ("paths", "cycles", "fan_in", "fan_out", "pass_through"):
            for p in getattr(findings, kind):
                accounts.update(p.account_ids)
                if p.total_amount is not None:
                    currency = p.currency or (txs[0].currency if len({t.currency for t in txs}) == 1
                                              else "")
                    money.add((p.total_amount, currency))
                dates.update(d.date() for d in (p.window_start, p.window_end) if d is not None)
    money.update((Decimal(f.value), f.currency or "")
                 for f in compute_derived_facts(bundle) if f.kind == "amount")
    return Grounding(
        tx_ids=frozenset(t.transaction_id for t in txs),
        account_ids=frozenset(accounts),
        amounts=frozenset(a for a, _ in money),
        money=frozenset(money),
        dates=frozenset(dates),
    )


def _parse_amount(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None


def _date_tokens(text: str) -> Iterator[tuple[str, date | None]]:
    for m in ISO_DATE_RE.finditer(text):
        y, mo, d = (int(g) for g in m.groups())
        yield m.group(0), _safe_date(y, mo, d)
    for m in US_DATE_RE.finditer(text):
        mo, d, y = (int(g) for g in m.groups())
        yield m.group(0), _safe_date(y, mo, d)


def _safe_date(y: int, mo: int, d: int) -> date | None:
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def ungrounded_tokens(text: str, ground: Grounding) -> list[str]:
    """IDs, money amounts and dates written in `text` that the bundle doesn't contain."""
    bad = [m.group(0) for m in ID_RE.finditer(text)
           if m.group(0) not in ground.tx_ids | ground.account_ids]
    for m in MONEY_RE.finditer(text):
        value = _parse_amount(m.group("a") or m.group("b") or m.group("c"))
        currency = m.group("bcur") or m.group("ccur") or "USD"
        if value is None or (value, currency) not in ground.money:
            bad.append(m.group(0).strip())
    bad += [raw for raw, value in _date_tokens(text) if value not in ground.dates]
    return bad


def verify_evidence(
    draft: NarrativeDraft, bundle: EvidenceBundle, retrieved_sources: list[RetrievedChunk]
) -> list[VerifierError]:
    """Every id, amount, date and policy citation in `draft` must come from `bundle` (or its
    derived facts) or `retrieved_sources`; free text is swept for ungrounded tokens and prohibited
    language. Returns [] for a fully grounded draft."""
    ground = grounding(bundle)
    known_chunks = {c.chunk_id for c in retrieved_sources}
    errors: list[VerifierError] = []

    def err(index: int, code: str, detail: str) -> None:
        error = VerifierError(claim_index=index, code=code, detail=detail)  # type: ignore[arg-type]
        if error not in errors:
            errors.append(error)

    def sweep(index: int, text: str) -> None:
        for token in ungrounded_tokens(text, ground):
            err(index, "UNGROUNDED_TOKEN", f"{token!r} is not in the evidence bundle")
        for phrase in find_prohibited(text):
            err(index, "PROHIBITED_LANGUAGE", f"prohibited phrase: {phrase!r}")

    sweep(FREE_TEXT, draft.summary)
    for i, claim in enumerate(draft.observed_facts):
        for tx_id in claim.transaction_ids:
            if tx_id not in ground.tx_ids:
                err(i, "UNKNOWN_TX", f"transaction_id {tx_id!r} is not in the evidence bundle")
        for acc in claim.account_ids:
            if acc not in ground.account_ids:
                err(i, "UNKNOWN_ACCOUNT", f"account_id {acc!r} is not in the evidence bundle")
        for amount in claim.amounts:
            if amount not in ground.amounts:
                err(i, "UNSUPPORTED_AMOUNT",
                    f"amount {amount} matches no bundle amount or derived fact")
        for day in claim.dates:
            if day not in ground.dates:
                err(i, "UNSUPPORTED_DATE", f"date {day} matches no evidence timestamp")
        if claim.claim_type == "fact" and not claim.transaction_ids:
            err(i, "MISSING_EVIDENCE", "fact claim lists no transaction_ids")
        unknown = [c for c in claim.citation_chunk_ids if c not in known_chunks]
        if unknown:
            err(i, "BAD_CITATION", f"chunk_ids not retrieved: {unknown}")
        elif claim.claim_type == "policy" and not claim.citation_chunk_ids:
            err(i, "BAD_CITATION", "policy claim cites no retrieved chunk")
        # TODO(review): a number quoted from policy text (e.g. a reporting threshold) is flagged
        # here too; the conservative choice, and the prompt says not to restate policy numbers.
        sweep(i, claim.text)
    for text in [*draft.uncertainties, *draft.follow_up_questions]:
        sweep(FREE_TEXT, text)
    return errors


def flagged_claims(errors: list[VerifierError]) -> list[int]:
    return sorted({e.claim_index for e in errors if e.claim_index is not None
                   and e.claim_index >= 0})


@traced_node("verify_evidence", VerifyEvidenceOutput)
def verify_evidence_node(state: Mapping[str, Any], ctx: NodeContext) -> VerifyEvidenceOutput:
    errors = verify_evidence(
        state["draft_narrative"], state["evidence_bundle"], state.get("retrieved_sources") or []
    )
    return VerifyEvidenceOutput(
        verifier_errors=errors,
        flagged_claims=flagged_claims(errors),
        case_status=CaseStatus.NEEDS_REVISION if errors else CaseStatus.DRAFTED,
    )
