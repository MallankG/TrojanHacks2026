"""Deterministic fallbacks, one per prompt name.

`llm.client` uses these when Ollama is not configured, unreachable, over the per-run call cap, or
returns output that fails validation twice. A template must never fail: it builds its output only
from the variables it is given. `llm.client` refuses to import if a prompt file in `prompts/` has no
template registered here.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from tracelayer_agents.guardrails import find_prohibited
from tracelayer_agents.nodes.derive import (
    NOT_AVAILABLE,
    PAYROLL_MAX_SPREAD,
    REFUND_MARKERS,
    REVERSAL_STATUSES,
    benign_patterns,
)
from tracelayer_agents.schemas import (
    Alert,
    ChatHypothesis,
    ChatPlan,
    ChatSynthesis,
    Contract,
    EvidenceBundle,
    NarrativeClaim,
    NarrativeDraft,
    NarrativeHypothesis,
    NarrativeOutput,
    NarrativeSectionItem,
    NarrativeSections,
    NarrativeSynthesis,
    PolicyClaim,
    PolicyQAOutput,
)

TemplateFn = Callable[[dict[str, Any]], BaseModel]

TEMPLATES: dict[str, TemplateFn] = {}


def template(prompt_name: str) -> Callable[[TemplateFn], TemplateFn]:
    """Register `fn` as the deterministic fallback for `prompt_name` (e.g. "narrative")."""

    def register(fn: TemplateFn) -> TemplateFn:
        if prompt_name in TEMPLATES:
            raise ValueError(f"template for {prompt_name!r} already registered")
        TEMPLATES[prompt_name] = fn
        return fn

    return register


# --------------------------------------------------------------------------- echo (tests only)


class EchoOutput(Contract):
    text: str


@template("echo")
def echo(variables: dict[str, Any]) -> EchoOutput:
    return EchoOutput(text=str(variables.get("message", "")))


# --------------------------------------------------------------------------- policy_qa

POLICY_QA_TOP_CHUNKS = 3
POLICY_NO_ANSWER = "The retrieved policy sources do not cover this question."


@template("policy_qa")
def policy_qa(variables: dict[str, Any]) -> PolicyQAOutput:
    """Quote the top retrieved excerpts as claims, each citing its own chunk. Reads the
    `chunks` variable (list of `RetrievedChunk` dicts, best first); `sources` is for the LLM."""
    chunks = [
        c for c in variables.get("chunks") or []
        if isinstance(c, dict) and str(c.get("chunk_id", "")).strip()
        and str(c.get("excerpt", "")).strip()
    ][:POLICY_QA_TOP_CHUNKS]
    if not chunks:
        return PolicyQAOutput(answer=POLICY_NO_ANSWER, no_answer=True)
    claims = [
        PolicyClaim(
            text=f"{c.get('doc_title') or 'Policy source'}: {str(c['excerpt']).strip()}",
            citation_chunk_ids=[str(c["chunk_id"])],
        )
        for c in chunks
    ]
    return PolicyQAOutput(
        answer=(
            f"Deterministic fallback (no model synthesis): the {len(claims)} most relevant "
            "retrieved policy passages are quoted below. Read them against the question."
        ),
        claims=claims,
        unknowns=["Excerpts were not checked for relevance by a model; confirm they apply."],
    )


# --------------------------------------------------------------------------- narrative

NARRATIVE_MAX_TX_CLAIMS = 10
NARRATIVE_MAX_ACCOUNT_CLAIMS = 3
NARRATIVE_MAX_POLICY_CLAIMS = 2
_PATTERN_NAMES = {
    "paths": "onward path",
    "cycles": "circular (cycle)",
    "fan_in": "fan-in",
    "fan_out": "fan-out",
    "pass_through": "pass-through",
}
# Anything the evidence verifier would treat as an id, amount or date. Text taken from outside the
# bundle (policy titles, rule ids) is only quoted when it contains none of these.
_TOKENISH_RE = re.compile(
    r"\$|\b(?:TX|ACC)-|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}"
    r"|\b(?:USD|EUR|GBP|CAD|MXN|JPY|CHF|AUD|CNY|INR|HKD|SGD)\b"
)


def _quotable(text: str | None) -> bool:
    return bool(text) and not _TOKENISH_RE.search(text) and not find_prohibited(text)


def _fmt_money(value: str | Decimal, currency: str | None) -> str:
    amount = Decimal(value)
    return f"${amount:,.2f}" if currency in (None, "USD") else f"{amount:.2f} {currency}"


def _day(timestamp: str) -> str:
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date().isoformat()


def _txs(variables: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(variables.get("transactions") or [],
                  key=lambda t: (t["timestamp"], t["transaction_id"]))


def _fact(facts: list[dict[str, Any]], label: str) -> dict[str, Any] | None:
    return next((f for f in facts if f["label"] == label), None)


def _chunks(variables: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in variables.get("chunks") or [] if c.get("chunk_id")]


def _busiest_accounts(txs: list[dict[str, Any]]) -> list[str]:
    accounts = {a for t in txs for a in (t["origin_account_id"], t["destination_account_id"])}
    return sorted(
        accounts,
        key=lambda a: (-sum(a in (t["origin_account_id"], t["destination_account_id"])
                            for t in txs), a),
    )[:NARRATIVE_MAX_ACCOUNT_CLAIMS]


def _pattern_claims(variables: dict[str, Any]) -> list[tuple[str, NarrativeClaim]]:
    """(pattern name, claim) for each flow pattern with at least one evidence transaction."""
    by_id = {t["transaction_id"]: t for t in _txs(variables)}
    facts = variables.get("derived_facts") or []
    findings = variables.get("flow_findings") or {}
    out: list[tuple[str, NarrativeClaim]] = []
    for kind, name in _PATTERN_NAMES.items():
        for n, pattern in enumerate(findings.get(kind) or [], 1):
            members = [by_id[i] for i in pattern["transaction_ids"] if i in by_id]
            if not members:
                continue
            if pattern.get("total_amount") is not None:
                total, currency = pattern["total_amount"], pattern.get("currency")
            else:
                derived = _fact(facts, f"{kind} pattern {n} total amount")
                total = derived["value"] if derived else None
                currency = derived.get("currency") if derived else None
            days = sorted({_day(t["timestamp"]) for t in members})
            text = (
                f"A {name} pattern links {len(members)} transactions among accounts "
                f"{', '.join(pattern['account_ids'])}"
            )
            if total is not None:
                text += f", totaling {_fmt_money(total, currency)}"
            text += f", dated {days[0]}" + (f" to {days[-1]}." if days[-1] != days[0] else ".")
            out.append((name, NarrativeClaim(
                text=text, claim_type="fact",
                transaction_ids=[t["transaction_id"] for t in members],
                account_ids=list(pattern["account_ids"]),
                amounts=[total] if total is not None else [],
                dates=sorted({days[0], days[-1]}),
            )))
    return out


def narrative_fact_claims(variables: dict[str, Any]) -> list[NarrativeClaim]:
    """Every "fact" claim of a narrative, built in Python from `transactions`, `flow_findings` and
    `derived_facts` (JSON dicts): flow patterns first, then single transactions, then per-account
    sums. Every amount comes from a transaction, a flow pattern or a derived fact and every date
    from a transaction timestamp, so these always pass `nodes.verify.verify_evidence`."""
    txs = _txs(variables)
    facts = variables.get("derived_facts") or []
    claims = [claim for _, claim in _pattern_claims(variables)]

    for t in txs[:NARRATIVE_MAX_TX_CLAIMS]:
        claims.append(NarrativeClaim(
            text=(
                f"Transaction {t['transaction_id']} moved "
                f"{_fmt_money(t['amount'], t['currency'])} from {t['origin_account_id']} to "
                f"{t['destination_account_id']} on {_day(t['timestamp'])}."
            ),
            claim_type="fact",
            transaction_ids=[t["transaction_id"]],
            account_ids=[t["origin_account_id"], t["destination_account_id"]],
            amounts=[t["amount"]],
            dates=[_day(t["timestamp"])],
        ))

    for account in _busiest_accounts(txs):
        parts, amounts, ids = [], [], []
        for direction, verb in (("inflow", "received"), ("outflow", "sent")):
            for f in facts:
                if f["kind"] == "amount" and f["label"].startswith(f"{account} {direction} total"):
                    count = _fact(facts, f"{account} {direction} transaction count")
                    n = count["value"] if count else str(len(f["transaction_ids"]))
                    plural = "" if n == "1" else "s"
                    parts.append(f"{verb} {_fmt_money(f['value'], f.get('currency'))} "
                                 f"across {n} transaction{plural}")
                    amounts.append(f["value"])
                    ids += f["transaction_ids"]
        if parts:
            claims.append(NarrativeClaim(
                text=f"Within the evidence, {account} " + " and ".join(parts) + ".",
                claim_type="fact", transaction_ids=list(dict.fromkeys(ids)),
                account_ids=[account], amounts=amounts,
            ))
    return claims


def narrative_policy_claims(variables: dict[str, Any]) -> list[NarrativeClaim]:
    """One claim per top retrieved chunk (from `chunks`), naming the source by title only (never
    the untrusted excerpt) and citing its chunk_id."""
    claims = []
    for c in _chunks(variables)[:NARRATIVE_MAX_POLICY_CLAIMS]:
        title = c.get("doc_title") if _quotable(c.get("doc_title")) else "A retrieved policy source"
        section = f" ({c['section']})" if _quotable(c.get("section")) else ""
        claims.append(NarrativeClaim(
            text=f"{title}{section} sets out review expectations relevant to this activity.",
            claim_type="policy", citation_chunk_ids=[str(c["chunk_id"])],
        ))
    return claims


def narrative_fixed_uncertainties(variables: dict[str, Any]) -> list[str]:
    """Uncertainties every draft carries whatever the synthesis says: data-quality caveats
    (CLAUDE.md rule 4), un-itemized transactions and missing policy sources."""
    out = []
    for issue in variables.get("quality_caveats") or []:
        rule = issue.get("rule_id") if _quotable(issue.get("rule_id")) else "a data contract rule"
        out.append(
            f"Data-quality issue under {rule} ({issue.get('severity')}) affects "
            f"{issue.get('affected_rows')} rows; the evidence may be incomplete."
        )
    n_txs = len(variables.get("transactions") or [])
    if n_txs > NARRATIVE_MAX_TX_CLAIMS:
        out.append(
            f"{n_txs - NARRATIVE_MAX_TX_CLAIMS} further evidence transactions are in the "
            "bundle but not itemized here."
        )
    if not _chunks(variables):
        out.append("No policy sources were retrieved for this case.")
    return out


@template("narrative")
def narrative(variables: dict[str, Any]) -> NarrativeSynthesis:
    """The synthesis (summary, hypotheses, uncertainties, follow-ups) without a model, from the
    same variables as `narrative_fact_claims`. Hypotheses reference fact claims by number."""
    txs = _txs(variables)
    facts = variables.get("derived_facts") or []
    patterns = _pattern_claims(variables)
    totals = [f for f in facts if f["label"].startswith("total amount of evidence transactions")]
    total_text = " and ".join(_fmt_money(f["value"], f.get("currency")) for f in totals)
    accounts = {a for t in txs for a in (t["origin_account_id"], t["destination_account_id"])}
    first_day = _day(txs[0]["timestamp"]) if txs else ""
    last_day = _day(txs[-1]["timestamp"]) if txs else ""
    span = _fact(facts, "time span from first to last evidence transaction (hours)")

    hypotheses = []
    if patterns:
        name = patterns[0][0]  # the first fact claim is the first pattern
        hypotheses.append(NarrativeHypothesis(
            text=(
                f"Hypothesis: the {name} activity is consistent with a {name} typology. It may "
                "also have a legitimate explanation; this requires reviewer assessment."
            ),
            fact_refs=[1],
        ))
    elif txs:
        n_tx_claims = min(len(txs), NARRATIVE_MAX_TX_CLAIMS)
        hypotheses.append(NarrativeHypothesis(
            text=(
                "Hypothesis: no flow pattern was found around this activity, so the reason it was "
                "ranked for review is unclear and requires reviewer assessment."
            ),
            fact_refs=list(range(1, n_tx_claims + 1)),
        ))

    summary = (
        f"The evidence bundle contains {len(txs)} transactions"
        + (f" totaling {total_text}" if total_text else "")
        + f" across {len(accounts)} accounts"
        + f", dated {first_day}"
        + (f" to {last_day}" if last_day != first_day else "")
        + (f" (a span of {span['value']} hours)" if span else "")
        + ". "
        + (f"{len(patterns)} flow pattern(s) were found around the alert. " if patterns
           else "No flow pattern was found around the alert. ")
        + "Deterministic fallback draft (no model synthesis); a reviewer must assess it."
    )
    return NarrativeSynthesis(
        summary=summary,
        hypotheses=hypotheses,
        uncertainties=[
            "No KYC, customer profile or stated business purpose was provided, so the economic "
            "purpose of these transfers is unknown.",
        ],
        follow_up_questions=[
            f"What is the stated business purpose of the activity involving {a}?"
            for a in _busiest_accounts(txs)
        ],
    )


def build_narrative(variables: dict[str, Any], synthesis: NarrativeSynthesis) -> NarrativeOutput:
    """Merge the deterministic claims with a synthesis: fact claims, then one hypothesis claim per
    synthesis hypothesis (carrying the transaction and account ids of the fact claims it
    references), then policy claims. `fact_refs` outside the fact claims are skipped here; the
    node's `synthesis_model` rejects them before this point."""
    fact_claims = narrative_fact_claims(variables)
    hypotheses = []
    for h in synthesis.hypotheses:
        refs = [fact_claims[r - 1] for r in h.fact_refs if 1 <= r <= len(fact_claims)]
        hypotheses.append(NarrativeClaim(
            text=h.text, claim_type="hypothesis",
            transaction_ids=list(dict.fromkeys(i for c in refs for i in c.transaction_ids)),
            account_ids=list(dict.fromkeys(a for c in refs for a in c.account_ids)),
        ))
    return NarrativeOutput(
        summary=synthesis.summary,
        observed_facts=[*fact_claims, *hypotheses, *narrative_policy_claims(variables)],
        uncertainties=list(dict.fromkeys(
            [*synthesis.uncertainties, *narrative_fixed_uncertainties(variables)]
        )),
        follow_up_questions=list(synthesis.follow_up_questions),
    )


def map_fact_refs(synthesis_items: list[NarrativeHypothesis],
                  fact_claims: list[NarrativeClaim]) -> list[tuple[str, list[str]]]:
    """(text, transaction ids of the referenced fact claims) for hypotheses or contradictions."""
    out = []
    for h in synthesis_items:
        refs = [fact_claims[r - 1] for r in h.fact_refs if 1 <= r <= len(fact_claims)]
        out.append((h.text, list(dict.fromkeys(i for c in refs for i in c.transaction_ids))))
    return out


def _item(text: str, factual: bool, ids: list[str]) -> NarrativeSectionItem:
    return NarrativeSectionItem(text=text, factual=factual, evidence_ids=list(dict.fromkeys(ids)))


def narrative_sections(
    draft: NarrativeDraft,
    bundle: EvidenceBundle,
    alert: Alert | None,
    chunks: list[dict[str, Any]],
    contradictions: list[tuple[str, list[str]]] | None = None,
) -> NarrativeSections:
    """The narrative_v4 sections, in Python from the draft's claims, the bundle and the alert.
    Every factual item cites evidence ids; the verifier checks them (`SECTION_UNCITED`).
    `contradictions` are the synthesis' contradictory observations mapped to transaction ids."""
    claims = draft.observed_facts
    tx_ids = [t.transaction_id for t in bundle.transactions]

    model_items: list[NarrativeSectionItem] = []
    if alert is not None:
        run = [f"model_run:{alert.model_run_id}"] if alert.model_run_id else []
        model_items.append(_item(
            f"The alert's composite score is {round(alert.composite_score, 4)} (tier {alert.tier}"
            + (f", rank {alert.rank}" if alert.rank else "")
            + "). Scores rank activity for review; they are not findings of wrongdoing.",
            True, [*alert.tx_ids, *run]))
        for name, score in sorted(alert.component_scores.items()):
            version = alert.model_versions.get(name)
            model_items.append(_item(
                f"Detector {name}" + (f" (version {version})" if version else "")
                + f" contributed a component score of {round(score, 4)}.",
                True, [*alert.tx_ids, *run]))
        tags = [t for t in alert.typology_tags if _quotable(t)]
        if tags:
            model_items.append(_item(
                f"Typology rules tagged this alert: {', '.join(tags)}. A typology tag is a rule "
                "match, not a conclusion.", True, list(alert.tx_ids)))

    contra: list[NarrativeSectionItem] = []
    by_id = {t.transaction_id: t for t in bundle.transactions}
    for kind, ids in benign_patterns(bundle.transactions).items():
        members = [by_id[i] for i in ids if i in by_id]
        if kind == "payroll":
            origins = sorted({t.origin_account_id for t in members})
            spread = int((PAYROLL_MAX_SPREAD - 1) * 100)
            contra.append(_item(
                f"{len(members)} evidence transactions from {', '.join(origins)} go to "
                f"{len({t.destination_account_id for t in members})} recipients on one day with "
                f"amounts within {spread}% of each other, which may be consistent with a "
                "payroll-like or vendor batch, pending verification.", True, ids))
        elif kind == "refund":
            contra.append(_item(
                "Some evidence transactions return the same amount between the same accounts, "
                "which may be consistent with a refund or reversal, pending verification.",
                True, ids))
    reversed_ids = [t.transaction_id for t in bundle.transactions
                    if (t.transaction_status or "").lower() in REVERSAL_STATUSES | REFUND_MARKERS]
    if reversed_ids:
        contra.append(_item(
            f"{len(reversed_ids)} evidence transaction(s) have a reversed, returned or refund "
            "status.", True, reversed_ids))
    findings = bundle.flow_findings
    if findings is not None and not any(getattr(findings, k) for k in _PATTERN_NAMES):
        contra.append(_item("No flow pattern (path, cycle, fan-in, fan-out or pass-through) was "
                            "found around the alert.", True,
                            [f"graph_snapshot:{findings.graph_snapshot_id}"]))
    contra += [_item(text, False, ids) for text, ids in contradictions or []]

    gaps = [_item(
        f"Data-quality issue {q.check_id} under "
        + (q.rule_id if _quotable(q.rule_id) else "a data contract rule")
        + f" ({q.severity}) affects {q.affected_rows} rows; the evidence may be incomplete.",
        True, [q.check_id]) for q in bundle.quality_caveats]
    gaps += [_item(note, True, [f"not_available:{item}"]) for item, note in NOT_AVAILABLE]
    if len(tx_ids) > NARRATIVE_MAX_TX_CLAIMS:
        gaps.append(_item(
            f"{len(tx_ids) - NARRATIVE_MAX_TX_CLAIMS} further evidence transactions are in the "
            "bundle but not itemized here.", True, tx_ids[NARRATIVE_MAX_TX_CLAIMS:]))
    if not [c for c in chunks if c.get("chunk_id")]:
        gaps.append(_item("No policy sources were retrieved for this case.", True,
                          ["not_available:policy_sources"]))
    fixed = set(narrative_fixed_uncertainties({
        "quality_caveats": [q.model_dump(mode="json") for q in bundle.quality_caveats],
        "transactions": [{} for _ in tx_ids], "chunks": chunks}))
    gaps += [_item(u, False, []) for u in draft.uncertainties if u.strip() and u not in fixed]

    return NarrativeSections(
        observed_facts=[_item(c.text, True, [*c.transaction_ids])
                        for c in claims if c.claim_type == "fact"],
        model_rule_inferences=model_items,
        analyst_hypotheses=[_item(c.text, False, [*c.transaction_ids])
                            for c in claims if c.claim_type == "hypothesis"],
        contradictory_evidence=contra,
        data_gaps=gaps,
        relevant_policy=[_item(c.text, True, [*c.citation_chunk_ids])
                         for c in claims if c.claim_type == "policy"],
        recommended_next_steps=[_item(q, False, []) for q in draft.follow_up_questions
                                if q.strip()],
    )


# --------------------------------------------------------------------------- investigation chat

_BENIGN_TEXT = {
    "payroll": "a payroll-like disbursement (one payer, several recipients, one day, similar "
               "amounts)",
    "refund": "a refund (the same amount returned between the same accounts)",
    "reversal": "a reversal (the same amount returned between the same accounts)",
    "treasury_sweep": "a treasury sweep",
    "shared_service": "a shared-service arrangement",
}


@template("chat_plan")
def chat_plan(variables: dict[str, Any]) -> ChatPlan:
    """Keyword match of the question to a chip, then that chip's precomputed plan; "unclear"
    (no tools) when nothing matches."""
    from tracelayer_agents.chat.plans import keyword_chip

    plans = variables.get("chip_plans") or {}
    chip = keyword_chip(str(variables.get("question") or ""))
    if chip is None or chip not in plans:
        return ChatPlan(intent="unclear")
    return ChatPlan(intent=chip, tool_calls=plans[chip])


@template("chat_answer")
def chat_answer(variables: dict[str, Any]) -> ChatSynthesis:
    """Summary of what was and wasn't checked, one hypothesis per supported benign kind plus a
    caveated typology hypothesis, and neutral next steps; all from the variables."""
    facts = variables.get("facts") or []
    support = variables.get("benign_support") or {}
    checked = variables.get("checked_tools") or []
    failed = variables.get("failed_tools") or []
    hub = variables.get("hub_account")
    observed = [f for f in facts if f["label"] == "observed_fact"]
    grounded = [f for f in facts
                if f["label"] in ("observed_fact", "model_inference") and f["transaction_ids"]]

    summary = f"Checked {', '.join(checked)}" if checked else "No tool returned results"
    summary += ". Each statement below cites its source ids. "
    if failed:
        summary += f"Not checked because the tool failed or was not run: {', '.join(failed)}. "
    if any(f["source_tools"][0] in ("related_entities", "trace_forward", "trace_backward")
           for f in observed):
        summary += "Being connected to an account in the graph is not evidence of wrongdoing. "
    summary += "Deterministic answer (no model synthesis); a reviewer must assess it."

    hypotheses: list[ChatHypothesis] = []
    if variables.get("allow_hypotheses"):
        for kind, tx_ids in support.items():
            refs = [f["n"] for f in observed if set(f["transaction_ids"]) & set(tx_ids)]
            if refs and kind in _BENIGN_TEXT:
                hypotheses.append(ChatHypothesis(
                    text=(f"Benign hypothesis: the activity is consistent with "
                          f"{_BENIGN_TEXT[kind]}. This needs confirmation and reviewer "
                          "assessment."),
                    fact_refs=refs[:5], benign_kind=kind,
                ))
        tags = [t for t in variables.get("typology_tags") or [] if _quotable(t)]
        if tags and grounded:
            hypotheses.append(ChatHypothesis(
                text=(f"Hypothesis: the activity is consistent with a {tags[0].replace('_', '-')}"
                      " pattern. It may also have a legitimate explanation; this requires "
                      "reviewer assessment."),
                fact_refs=[f["n"] for f in grounded][:3],
            ))
    next_steps = []
    if hub:
        next_steps.append(f"Obtain the KYC profile and stated business purpose for {hub}.")
        next_steps.append(f"Confirm whether the counterparties of {hub} are employees, vendors "
                          "or related parties.")
        if "payroll" in support:
            next_steps.append(f"Check whether the payments from {hub} match a documented payroll "
                              "schedule.")
    if failed:
        next_steps.append("Re-run the tools that failed before relying on this answer.")
    return ChatSynthesis(summary=summary, hypotheses=hypotheses[:3], next_steps=next_steps[:5])
