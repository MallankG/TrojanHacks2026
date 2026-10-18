"""Derived facts (CLAUDE.md rule 3): every total, count and time span the narrative may quote is
computed here, in Python with `Decimal`, before any LLM call. The LLM copies these values; it never
does arithmetic. `verify_evidence` recomputes them to check amounts against.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from decimal import Decimal
from typing import Any, Protocol

from tracelayer_agents.schemas import DerivedFact, EvidenceBundle, EvidenceTransaction

CENT = Decimal("0.01")
# What the agents never receive, so no chat answer or narrative may claim it was checked.
# TODO(review): drop items as the backend starts supplying them.
NOT_AVAILABLE = (
    ("kyc_profile", "No KYC or customer profile is available to the agents."),
    ("business_purpose", "No stated business purpose is available for these transfers."),
    ("account_ownership", "No account ownership or relationship data is available, so "
                          "treasury-sweep or shared-service explanations cannot be checked."),
)
_PATTERN_KINDS = ("paths", "cycles", "fan_in", "fan_out", "pass_through")


def money(value: Decimal) -> str:
    return str(value.quantize(CENT))


def _iso(tx: EvidenceTransaction) -> str:
    return tx.timestamp.isoformat().replace("+00:00", "Z")


def compute_derived_facts(bundle: EvidenceBundle) -> list[DerivedFact]:
    """Facts in a fixed order, so the same bundle always gives the same ids (DF-1, DF-2, ...)."""
    facts: list[dict[str, Any]] = []

    def add(label: str, kind: str, value: str, txs: list[EvidenceTransaction], **extra: Any):
        facts.append({
            "label": label, "kind": kind, "value": value,
            "transaction_ids": [t.transaction_id for t in txs], **extra,
        })

    txs = sorted(bundle.transactions, key=lambda t: (t.timestamp, t.transaction_id))
    by_currency: dict[str, list[EvidenceTransaction]] = defaultdict(list)
    for tx in txs:
        by_currency[tx.currency].append(tx)

    add("evidence transaction count", "count", str(len(txs)), txs)
    for currency, group in sorted(by_currency.items()):
        total = sum((t.amount for t in group), Decimal(0))
        add(f"total amount of evidence transactions ({currency})", "amount", money(total), group,
            currency=currency)
    accounts = sorted({a for t in txs for a in (t.origin_account_id, t.destination_account_id)})
    add("distinct accounts in evidence", "count", str(len(accounts)), txs, account_ids=accounts)

    first, last = txs[0], txs[-1]
    add("first evidence transaction time", "timestamp", _iso(first), [first])
    add("last evidence transaction time", "timestamp", _iso(last), [last])
    hours = Decimal((last.timestamp - first.timestamp).total_seconds()) / Decimal(3600)
    add("time span from first to last evidence transaction (hours)", "duration_hours",
        money(hours), [first, last])

    for account in accounts:
        for direction, side in (("inflow", "destination_account_id"),
                                ("outflow", "origin_account_id")):
            for currency, group in sorted(by_currency.items()):
                moved = [t for t in group if getattr(t, side) == account]
                if not moved:
                    continue
                total = sum((t.amount for t in moved), Decimal(0))
                add(f"{account} {direction} total ({currency})", "amount", money(total), moved,
                    currency=currency, account_ids=[account])
                add(f"{account} {direction} transaction count", "count", str(len(moved)), moved,
                    account_ids=[account])

    findings = bundle.flow_findings
    by_id = {t.transaction_id: t for t in txs}
    if findings is not None:
        for kind in _PATTERN_KINDS:
            for n, pattern in enumerate(getattr(findings, kind), 1):
                members = [by_id[i] for i in pattern.transaction_ids if i in by_id]
                if not members:
                    continue
                label = f"{kind} pattern {n}"
                add(f"{label} transaction count", "count", str(len(members)), members,
                    account_ids=list(pattern.account_ids))
                if pattern.total_amount is None and len({t.currency for t in members}) == 1:
                    total = sum((t.amount for t in members), Decimal(0))
                    add(f"{label} total amount", "amount", money(total), members,
                        currency=members[0].currency, account_ids=list(pattern.account_ids))

    return [DerivedFact(fact_id=f"DF-{i}", **f) for i, f in enumerate(facts, 1)]


# --------------------------------------------------------------------------- benign patterns

PAYROLL_MIN_RECIPIENTS = 3
PAYROLL_MAX_SPREAD = Decimal("1.10")  # largest / smallest amount in the batch
REVERSAL_STATUSES = frozenset({"reversed", "returned", "reversal"})
REFUND_MARKERS = frozenset({"refund", "refunded"})


class _Edge(Protocol):
    transaction_id: str
    origin_account_id: str
    destination_account_id: str
    amount: Decimal
    currency: str
    timestamp: Any


def benign_patterns(edges: Iterable[_Edge]) -> dict[str, list[str]]:
    """Benign kind → transaction ids whose shape is consistent with it (deterministic; works on
    `SubgraphEdge` and `EvidenceTransaction` alike):

    - payroll: one origin pays at least 3 distinct recipients on one day, same currency, with the
      largest amount at most 10% above the smallest.
    - refund / reversal: a later transaction returns the same amount between the same pair.
    """
    ordered = sorted(edges, key=lambda e: (e.timestamp, e.transaction_id))
    support: dict[str, list[str]] = defaultdict(list)
    batches: dict[tuple[str, str, str], list[_Edge]] = defaultdict(list)
    for e in ordered:
        batches[(e.origin_account_id, e.timestamp.date().isoformat(), e.currency)].append(e)
    for batch in batches.values():
        amounts = [e.amount for e in batch]
        if (len({e.destination_account_id for e in batch}) >= PAYROLL_MIN_RECIPIENTS
                and min(amounts) > 0 and max(amounts) / min(amounts) <= PAYROLL_MAX_SPREAD):
            support["payroll"] += [e.transaction_id for e in batch]
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            if (b.origin_account_id == a.destination_account_id
                    and b.destination_account_id == a.origin_account_id
                    and b.amount == a.amount and b.currency == a.currency):
                for kind in ("refund", "reversal"):
                    support[kind] += [a.transaction_id, b.transaction_id]
    return {k: sorted(set(v)) for k, v in sorted(support.items())}
