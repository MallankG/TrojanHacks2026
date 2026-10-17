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
from tracelayer_agents.schemas import (
    Contract,
    NarrativeClaim,
    NarrativeOutput,
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


@template("narrative")
def narrative(variables: dict[str, Any]) -> NarrativeOutput:
    """A plain, fully grounded narrative built only from `transactions`, `flow_findings`,
    `derived_facts`, `quality_caveats` and `chunks` (JSON dicts). Every amount comes from a
    transaction, a flow pattern or a derived fact, every date from a transaction timestamp, and
    policy sources are named by title only (never the excerpt), so it always passes
    `nodes.verify.verify_evidence`."""
    txs = sorted(variables.get("transactions") or [],
                 key=lambda t: (t["timestamp"], t["transaction_id"]))
    by_id = {t["transaction_id"]: t for t in txs}
    facts = variables.get("derived_facts") or []
    findings = variables.get("flow_findings") or {}
    chunks = [c for c in variables.get("chunks") or [] if c.get("chunk_id")]
    all_ids = [t["transaction_id"] for t in txs]

    def fact(label: str) -> dict[str, Any] | None:
        return next((f for f in facts if f["label"] == label), None)

    totals = [f for f in facts if f["label"].startswith("total amount of evidence transactions")]
    total_text = " and ".join(_fmt_money(f["value"], f.get("currency")) for f in totals)
    accounts = sorted({a for t in txs
                       for a in (t["origin_account_id"], t["destination_account_id"])})
    first_day = _day(txs[0]["timestamp"]) if txs else ""
    last_day = _day(txs[-1]["timestamp"]) if txs else ""
    span = fact("time span from first to last evidence transaction (hours)")

    claims: list[NarrativeClaim] = []
    patterns: list[tuple[str, dict[str, Any]]] = []
    for kind, name in _PATTERN_NAMES.items():
        for n, pattern in enumerate(findings.get(kind) or [], 1):
            members = [by_id[i] for i in pattern["transaction_ids"] if i in by_id]
            if not members:
                continue
            patterns.append((name, pattern))
            if pattern.get("total_amount") is not None:
                total, currency = pattern["total_amount"], pattern.get("currency")
            else:
                derived = fact(f"{kind} pattern {n} total amount")
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
            claims.append(NarrativeClaim(
                text=text, claim_type="fact",
                transaction_ids=[t["transaction_id"] for t in members],
                account_ids=list(pattern["account_ids"]),
                amounts=[total] if total is not None else [],
                dates=sorted({days[0], days[-1]}),
            ))

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

    busiest = sorted(
        accounts,
        key=lambda a: (-sum(a in (t["origin_account_id"], t["destination_account_id"])
                            for t in txs), a),
    )[:NARRATIVE_MAX_ACCOUNT_CLAIMS]
    for account in busiest:
        parts, amounts, ids = [], [], []
        for direction, verb in (("inflow", "received"), ("outflow", "sent")):
            for f in facts:
                if f["kind"] == "amount" and f["label"].startswith(f"{account} {direction} total"):
                    count = fact(f"{account} {direction} transaction count")
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

    if patterns:
        name, pattern = patterns[0]
        claims.append(NarrativeClaim(
            text=(
                f"Hypothesis: the {name} activity is consistent with a {name} typology. It may "
                "also have a legitimate explanation; this requires reviewer assessment."
            ),
            claim_type="hypothesis",
            transaction_ids=[i for i in pattern["transaction_ids"] if i in by_id],
            account_ids=list(pattern["account_ids"]),
        ))
    elif all_ids:
        claims.append(NarrativeClaim(
            text=(
                "Hypothesis: no flow pattern was found around this activity, so the reason it was "
                "ranked for review is unclear and requires reviewer assessment."
            ),
            claim_type="hypothesis", transaction_ids=all_ids,
        ))

    for c in chunks[:NARRATIVE_MAX_POLICY_CLAIMS]:
        title = c.get("doc_title") if _quotable(c.get("doc_title")) else "A retrieved policy source"
        section = f" ({c['section']})" if _quotable(c.get("section")) else ""
        claims.append(NarrativeClaim(
            text=f"{title}{section} sets out review expectations relevant to this activity.",
            claim_type="policy", citation_chunk_ids=[str(c["chunk_id"])],
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

    uncertainties = [
        "No KYC, customer profile or stated business purpose was provided, so the economic "
        "purpose of these transfers is unknown.",
    ]
    for issue in variables.get("quality_caveats") or []:
        rule = issue.get("rule_id") if _quotable(issue.get("rule_id")) else "a data contract rule"
        uncertainties.append(
            f"Data-quality issue under {rule} ({issue.get('severity')}) affects "
            f"{issue.get('affected_rows')} rows; the evidence may be incomplete."
        )
    if len(txs) > NARRATIVE_MAX_TX_CLAIMS:
        uncertainties.append(
            f"{len(txs) - NARRATIVE_MAX_TX_CLAIMS} further evidence transactions are in the "
            "bundle but not itemized here."
        )
    if not chunks:
        uncertainties.append("No policy sources were retrieved for this case.")

    return NarrativeOutput(
        summary=summary,
        observed_facts=claims,
        uncertainties=uncertainties,
        follow_up_questions=[
            f"What is the stated business purpose of the activity involving {a}?" for a in busiest
        ],
    )
