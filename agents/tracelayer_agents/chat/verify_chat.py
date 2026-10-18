"""The chat verifier: a claim reaches the reviewer only if every id, amount, date, path and
citation in it traces back to this turn's tool results or the case context. Built on the case
verifier's token sweep (`nodes.verify.ungrounded_tokens`) and the shared guardrails.

Unverifiable claims are dropped and reported as `ChatGap`s (hash of the text, the codes and a
narrower question to try), never shown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from tracelayer_agents.audit import sha256_json
from tracelayer_agents.chat.session import CaseScope
from tracelayer_agents.guardrails import find_disposition, find_prohibited
from tracelayer_agents.nodes.verify import CAVEAT_RE, Grounding, ungrounded_tokens
from tracelayer_agents.schemas import ChatClaim, ChatGap, ToolResult, VerifierError

# Named benign explanations; a hypothesis naming one needs `claims.benign_support`.
BENIGN_KIND_RE = {
    "payroll": re.compile(r"\bpayroll|salar(?:y|ies)|wages?\b", re.IGNORECASE),
    "treasury_sweep": re.compile(r"\b(?:treasury|cash)?\s*sweep", re.IGNORECASE),
    "refund": re.compile(r"\brefund", re.IGNORECASE),
    "reversal": re.compile(r"\brevers(?:al|ed)", re.IGNORECASE),
    "shared_service": re.compile(r"\bshared[\s-]+services?\b", re.IGNORECASE),
}
_BENIGN_WORDS = (r"(?:payroll|salar(?:y|ies)|wages?|refunds?|revers(?:al|als|ed)|"
                 r"(?:treasury |cash )?sweeps?|shared[\s-]+services?)")
# Free text may only mention a benign kind hedged ("may be consistent with payroll, pending
# verification"); these assertive forms drop the sentence whatever else it says.
BENIGN_ASSERTIVE_RE = re.compile(
    r"\bsupported by\b|\bconfirm(?:s|ed|ing)\b|\bas (?:indicated|shown) by\b|"
    r"\bbenign support\b|\b(?:is|are|was|were)\s+(?:an?\s+|the\s+)?" + _BENIGN_WORDS,
    re.IGNORECASE,
)
BENIGN_HEDGE_RE = re.compile(r"\b(?:may|might|could)\b.*\bpending verification\b",
                             re.IGNORECASE)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class ChatGround:
    tokens: Grounding
    paths: tuple[frozenset[str], ...]
    chunk_ids: frozenset[str]
    evidence_ids: frozenset[str]
    model_run_id: str | None


def check_arithmetic(
    results: list[ToolResult], scope: CaseScope
) -> tuple[list[ToolResult], list[VerifierError]]:
    """Recompute every amount a tool reported from the scope's transactions; a value that doesn't
    add up is removed from that result."""
    errors: list[VerifierError] = []
    checked = []
    for r in results:
        good = []
        for c in r.computed:
            if c.kind == "amount":
                edges = [scope.edges.get(i) for i in c.transaction_ids]
                ok = (edges and all(edges) and {e.currency for e in edges} == {c.currency}
                      and sum((e.amount for e in edges), Decimal(0)) == Decimal(c.value))
                if not ok:
                    errors.append(VerifierError(code="ARITHMETIC_MISMATCH",
                                                detail=f"{r.tool} {c.key} does not add up"))
                    continue
            good.append(c)
        checked.append(r.model_copy(update={"computed": good}))
    return checked, errors


def chat_grounding(results: list[ToolResult], scope: CaseScope) -> ChatGround:
    ok = [r for r in results if r.status == "ok"]
    tx_ids = set(scope.evidence_tx_ids)
    accounts = set(scope.evidence_account_ids)
    for r in ok:
        tx_ids |= {i for i in (*r.refs.tx_ids, *r.refs.edge_ids) if i in scope.edges}
        accounts |= {a for a in r.refs.account_ids if a in scope.account_ids}
    money = {(scope.edges[i].amount, scope.edges[i].currency) for i in tx_ids}
    dates = {scope.edges[i].timestamp.date() for i in tx_ids}
    for r in ok:
        for c in r.computed:
            if c.kind == "amount":
                money.add((Decimal(c.value), c.currency or ""))
            elif c.kind == "timestamp":
                dates.add(datetime.fromisoformat(c.value.replace("Z", "+00:00")).date())
    evidence = {f.fact_id for f in scope.derived_facts} | {i for r in ok
                                                          for i in r.refs.evidence_ids}
    return ChatGround(
        tokens=Grounding(
            tx_ids=frozenset(tx_ids), account_ids=frozenset(accounts),
            amounts=frozenset(a for a, _ in money), money=frozenset(money),
            dates=frozenset(dates),
        ),
        paths=tuple(frozenset(p) for r in ok for p in r.refs.paths),
        chunk_ids=frozenset(i for r in ok for i in r.refs.chunk_ids),
        evidence_ids=frozenset(evidence),
        model_run_id=scope.alert.model_run_id,
    )


def text_errors(text: str, ground: ChatGround) -> list[tuple[str, str]]:
    """(code, detail) for every ungrounded token and forbidden phrase in free text."""
    out = [("UNGROUNDED_TOKEN", f"{t!r} is not in this turn's tool results")
           for t in ungrounded_tokens(text, ground.tokens)]
    out += [("PROHIBITED_LANGUAGE", f"prohibited phrase: {p!r}")
            for p in find_prohibited(text, intent=True)]
    out += [("DISPOSITION_LANGUAGE", f"disposition or proximity-as-proof phrase: {p!r}")
            for p in find_disposition(text)]
    return out


def benign_text_errors(text: str, require_hedge: bool) -> list[tuple[str, str]]:
    """A benign kind named in free text must not be asserted; with `require_hedge` (the summary)
    it must also be hedged. Next steps only need to avoid asserting it."""
    if not any(p.search(text) for p in BENIGN_KIND_RE.values()):
        return []
    if BENIGN_ASSERTIVE_RE.search(text):
        return [("ASSERTED_BENIGN", "states a benign explanation as established")]
    if require_hedge and not BENIGN_HEDGE_RE.search(text):
        return [("ASSERTED_BENIGN", "names a benign explanation without 'may … pending "
                 "verification'")]
    return []


def filter_summary(text: str, scope: CaseScope) -> tuple[str, list[ChatGap]]:
    """Drops each summary sentence that asserts (or does not hedge) a benign explanation."""
    kept, gaps = [], []
    for sentence in _SENTENCE_RE.split(text.strip()):
        if errs := benign_text_errors(sentence, require_hedge=True):
            gaps.append(gap("summary", sentence, errs, scope))
        else:
            kept.append(sentence)
    return " ".join(kept), gaps


def benign_kinds(claim: ChatClaim) -> set[str]:
    kinds = {k for k, pattern in BENIGN_KIND_RE.items() if pattern.search(claim.text)}
    return kinds | ({claim.benign_kind} if claim.benign_kind else set())


def claim_errors(
    claim: ChatClaim, ground: ChatGround, support: dict[str, list[str]]
) -> list[tuple[str, str]]:
    g = ground.tokens
    errs: list[tuple[str, str]] = []
    cited = (claim.transaction_ids or claim.account_ids or claim.edge_ids or claim.evidence_ids
             or claim.chunk_ids or claim.model_run_id)
    if claim.label != "missing_evidence" and not cited:
        errs.append(("MISSING_EVIDENCE", "claim cites no id"))
    errs += [("UNKNOWN_TX", f"transaction_id {i!r} is not in this turn's results")
             for i in claim.transaction_ids if i not in g.tx_ids]
    errs += [("UNKNOWN_ACCOUNT", f"account_id {a!r} is not in this turn's results")
             for a in claim.account_ids if a not in g.account_ids]
    if claim.edge_ids and not any(set(claim.edge_ids) <= p for p in ground.paths):
        errs.append(("NOT_ON_PATH", "edge_ids do not lie on one returned path or cycle"))
    errs += [("BAD_CITATION", f"chunk_id {c!r} was not retrieved")
             for c in claim.chunk_ids if c not in ground.chunk_ids]
    if claim.label == "policy" and not claim.chunk_ids:
        errs.append(("BAD_CITATION", "policy claim cites no retrieved chunk"))
    errs += [("OUT_OF_SCOPE", f"evidence id {e!r} is not part of this case's results")
             for e in claim.evidence_ids if e not in ground.evidence_ids]
    if claim.model_run_id is not None and claim.model_run_id != ground.model_run_id:
        errs.append(("OUT_OF_SCOPE", "model_run_id is not this alert's model run"))
    errs += [("UNSUPPORTED_AMOUNT", f"amount {a} matches no tool-computed value")
             for a in claim.amounts if a not in g.amounts]
    if claim.label == "hypothesis":
        for kind in sorted(benign_kinds(claim)):
            backing = set(support.get(kind, []))
            if not backing or not backing & set(claim.transaction_ids):
                errs.append(("UNSUPPORTED_BENIGN",
                             f"no tool result this turn supports a {kind} explanation"))
    errs += text_errors(claim.text, ground)
    return list(dict.fromkeys(errs))


def suggested_query(codes: list[str], claim_label: str, scope: CaseScope) -> str:
    hub = scope.hub_accounts[0]
    if "UNSUPPORTED_BENIGN" in codes or "ASSERTED_BENIGN" in codes:
        return ("Use the benign_explanations chip, or ask whether the payments from "
                f"{hub} match a documented payroll or refund.")
    if "NOT_ON_PATH" in codes or claim_label == "observed_fact":
        return f"Ask a narrower question, e.g. 'trace forward from {hub}, 1 hop'."
    if "BAD_CITATION" in codes or claim_label == "policy":
        return "Use the policy_guidance chip or ask about one specific policy requirement."
    if claim_label == "model_inference":
        return "Use the evidence_per_signal chip to see each detector's factors."
    return f"Ask about one transaction or account in this case, e.g. {hub}."


# Gap details describe the problem without repeating the dropped values.
GAP_DETAIL = {
    "MISSING_EVIDENCE": "cites no id",
    "UNKNOWN_TX": "names a transaction not in this turn's results",
    "UNKNOWN_ACCOUNT": "names an account not in this turn's results",
    "NOT_ON_PATH": "cites edges that are not on one returned path",
    "BAD_CITATION": "cites a policy source that was not retrieved",
    "OUT_OF_SCOPE": "cites evidence outside this case's results",
    "UNSUPPORTED_AMOUNT": "states an amount no tool computed",
    "UNSUPPORTED_BENIGN": "offers a benign explanation no tool result supports",
    "ASSERTED_BENIGN": "states a benign explanation as fact instead of a hedged possibility",
    "UNGROUNDED_TOKEN": "mentions an id, amount or date not in this turn's results",
    "PROHIBITED_LANGUAGE": "uses prohibited language (crime, intent or SAR status)",
    "DISPOSITION_LANGUAGE": "recommends a disposition or treats proximity as proof",
    "MISSING_ALTERNATIVE": "is one-sided: no benign reading and no neutral caveat",
}


def gap(label: str, text: str, errs: list[tuple[str, str]], scope: CaseScope) -> ChatGap:
    codes = list(dict.fromkeys(c for c, _ in errs))
    return ChatGap(label=label, text_hash=sha256_json(text), codes=codes,
                   detail="; ".join(GAP_DETAIL.get(c, c) for c in codes),
                   suggested_query=suggested_query(codes, label, scope))


def verify_claims(
    claims: list[ChatClaim], ground: ChatGround, support: dict[str, list[str]], scope: CaseScope
) -> tuple[list[ChatClaim], list[ChatGap], list[VerifierError]]:
    kept: list[ChatClaim] = []
    gaps: list[ChatGap] = []
    errors: list[VerifierError] = []
    for i, claim in enumerate(claims):
        errs = claim_errors(claim, ground, support)
        if errs:
            gaps.append(gap(claim.label, claim.text, errs, scope))
            errors += [VerifierError(claim_index=i, code=c, detail=d) for c, d in errs]
        else:
            kept.append(claim)

    # Hypotheses must not all point one way (as in the case verifier): if none is a supported
    # benign reading, every hypothesis must carry the neutral caveat.
    hyps = [c for c in kept if c.label == "hypothesis"]
    if hyps and not any(benign_kinds(c) for c in hyps):
        for c in hyps:
            if not CAVEAT_RE.search(c.text):
                kept.remove(c)
                errs = [("MISSING_ALTERNATIVE", "one-sided hypothesis without a benign reading "
                         "or the 'may have a legitimate explanation' caveat")]
                gaps.append(gap("hypothesis", c.text, errs, scope))
                errors.append(VerifierError(code="MISSING_ALTERNATIVE", detail=errs[0][1]))
    return kept, gaps, errors
