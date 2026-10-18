"""Deterministic chat claims (CLAUDE.md rule 3): every observed fact, model inference, policy and
missing-evidence claim is written here in Python from tool results, with the ids and amounts it
rests on. The LLM only adds a summary, hypotheses (by fact number) and next steps.

`benign_support` finds deterministic support for benign counter-hypotheses in the rows the tools
returned this turn; a benign hypothesis without it is dropped by the verifier.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from tracelayer_agents.chat.session import CaseScope
from tracelayer_agents.llm.templates import _day as day
from tracelayer_agents.llm.templates import _fmt_money as fmt_money
from tracelayer_agents.llm.templates import _quotable as quotable
from tracelayer_agents.schemas import ChatClaim, SubgraphEdge, ToolResult
from tracelayer_agents.tools import base

MAX_CLAIMS_PER_TOOL = 10
MAX_POLICY_CLAIMS = 3
MAX_EXCERPT_CHARS = 300
PAYROLL_MIN_RECIPIENTS = 3
PAYROLL_MAX_SPREAD = Decimal("1.10")  # largest / smallest amount in the batch
REVERSAL_STATUSES = frozenset({"reversed", "returned", "reversal"})
REFUND_MARKERS = frozenset({"refund", "refunded"})


def _arrow(accounts: list[str]) -> str:
    return " → ".join(accounts)


def _dates(first: str, last: str) -> str:
    a, b = day(first), day(last)
    return f"on {a}" if a == b else f"from {a} to {b}"


def _claim(text: str, label: str, tool: str, **ids: Any) -> ChatClaim:
    return ChatClaim(text=text, label=label, source_tools=[tool], **ids)


def _trace_claims(r: ToolResult) -> list[ChatClaim]:
    forward = r.tool == "trace_forward"
    start = r.args.get("account_id") or r.args.get("tx_id")
    hops = r.args.get("hops", 2)
    if not r.rows:
        direction = "outgoing" if forward else "incoming"
        ids = ({"account_ids": [start]} if r.args.get("account_id")
               else {"transaction_ids": [start]})
        prep = "from" if forward else "into"
        return [_claim(f"No {direction} time-respecting transactions {prep} {start} were found "
                       f"within {hops} hop(s) inside the case window.", "observed_fact", r.tool,
                       **ids)]
    totals = {c.key: c for c in r.computed}
    out = []
    for n, row in enumerate(r.rows[:MAX_CLAIMS_PER_TOOL], 1):
        total = totals.get(f"path_{n}_total")
        text = (f"{'Forward' if forward else 'Backward'} trace from {start}: "
                f"{_arrow(row['account_ids'])} via {', '.join(row['edge_ids'])}")
        if total is not None:
            text += f", totaling {fmt_money(total.value, total.currency)}"
        text += f", {_dates(row['first_timestamp'], row['last_timestamp'])}."
        out.append(_claim(text, "observed_fact", r.tool, transaction_ids=row["edge_ids"],
                          edge_ids=row["edge_ids"], account_ids=row["account_ids"],
                          amounts=[total.value] if total else []))
    return out


def _lookup_claims(r: ToolResult) -> list[ChatClaim]:
    return [
        _claim(f"Transaction {t['transaction_id']} moved {fmt_money(t['amount'], t['currency'])} "
               f"from {t['origin_account_id']} to {t['destination_account_id']} on "
               f"{day(t['timestamp'])}.", "observed_fact", r.tool,
               transaction_ids=[t["transaction_id"]],
               account_ids=[t["origin_account_id"], t["destination_account_id"]],
               amounts=[t["amount"]])
        for t in r.rows[:MAX_CLAIMS_PER_TOOL]
    ]


def _aggregate_claims(r: ToolResult) -> list[ChatClaim]:
    stamps = {c.key: c.value for c in r.computed if c.kind == "timestamp"}
    window = _dates(stamps["window_start"], stamps["window_end"])
    if not r.rows:
        return [_claim(f"No transactions matched {window} in the case scope.", "observed_fact",
                       r.tool, account_ids=list(r.args.get("account_ids") or []) or
                       list(r.refs.account_ids), evidence_ids=[])]
    out = []
    for row in r.rows[:MAX_CLAIMS_PER_TOOL]:
        plural = "" if row["count"] == 1 else "s"
        money = fmt_money(row["total"], row["currency"])
        if "account_id" in row:
            verb = "received" if row["direction"] == "inflow" else "sent"
            text = (f"{window[0].upper()}{window[1:]}, {row['account_id']} {verb} {money} across "
                    f"{row['count']} transaction{plural}.")
            accounts = [row["account_id"]]
        else:
            text = (f"{window[0].upper()}{window[1:]}, {row['count']} transaction{plural} "
                    f"totaled {money}.")
            accounts = []
        out.append(_claim(text, "observed_fact", r.tool, transaction_ids=row["transaction_ids"],
                          account_ids=accounts, amounts=[row["total"]]))
    return out


def _cycle_claims(r: ToolResult) -> list[ChatClaim]:
    if not r.rows:
        return [_claim(f"No time-respecting cycle of up to {r.args.get('max_len', 4)} accounts "
                       "was found in the case neighbourhood.", "observed_fact", r.tool,
                       account_ids=list(r.refs.account_ids))]
    totals = {c.key: c for c in r.computed}
    out = []
    for n, row in enumerate(r.rows[:MAX_CLAIMS_PER_TOOL], 1):
        total = totals.get(f"cycle_{n}_total")
        text = (f"A time-respecting cycle {_arrow(row['account_ids'])} uses "
                f"{', '.join(row['edge_ids'])}")
        text += f", totaling {fmt_money(total.value, total.currency)}." if total else "."
        out.append(_claim(text, "observed_fact", r.tool, transaction_ids=row["edge_ids"],
                          edge_ids=row["edge_ids"], account_ids=row["account_ids"],
                          amounts=[total.value] if total else []))
    return out


def _related_claims(r: ToolResult) -> list[ChatClaim]:
    start = r.args["account_id"]
    if not r.rows:
        return [_claim(f"No other account is within {r.args.get('hops', 1)} hop(s) of {start} "
                       "inside the case window.", "observed_fact", r.tool, account_ids=[start])]
    totals = {c.key: c for c in r.computed}
    out = []
    for row in r.rows[:MAX_CLAIMS_PER_TOOL]:
        acc = row["account_id"]
        text = (f"{acc} is {row['distance']} hop(s) from {start} via "
                f"{', '.join(row['via_edge_ids'])}")
        total = totals.get(f"direct_{acc}_total")
        if row["direct_transaction_count"]:
            text += f"; they share {row['direct_transaction_count']} direct transaction(s)"
            if total is not None:
                text += f" totaling {fmt_money(total.value, total.currency)}"
        out.append(_claim(text + ".", "observed_fact", r.tool,
                          transaction_ids=list(row["via_edge_ids"]),
                          edge_ids=list(row["via_edge_ids"]), account_ids=[start, acc],
                          amounts=[total.value] if total else []))
    return out


def _model_claims(r: ToolResult, scope: CaseScope) -> list[ChatClaim]:
    run = scope.alert.model_run_id
    alert_txs = list(scope.alert.tx_ids)
    out = []
    for row in r.rows:
        if row["kind"] == "composite":
            tags = ", ".join(row["typology_tags"]) or "none"
            text = (f"The alert's composite score is {row['composite_score']} (tier {row['tier']}"
                    + (f", rank {row['rank']}" if row["rank"] else "")
                    + f"); typology tags: {tags}. Scores rank activity for review; they are not "
                    "findings of wrongdoing.")
            ids = alert_txs
        elif row["kind"] == "component":
            text = (f"Detector {row['detector']} (version {row['model_version']}) contributed a "
                    f"component score of {row['component_score']}.")
            ids = alert_txs
        else:
            factors = "; ".join(f"{f['name']}={f['value']}" for f in row["factors"]) or "none"
            text = (f"For {row['tx_id']}, detector {row['detector']} scored {row['score']}; "
                    f"top factors: {factors}.")
            ids = [row["tx_id"]]
        out.append(_claim(text, "model_inference", r.tool, transaction_ids=ids, model_run_id=run))
    return out[: MAX_CLAIMS_PER_TOOL + 5]


def _quality_claims(r: ToolResult, scope: CaseScope) -> list[ChatClaim]:
    out = []
    lineage = [row["ref"] for row in r.rows if row["kind"] == "lineage"]
    for row in r.rows:
        if row["kind"] == "governance":
            caveat = (f"; confidence caveat: {row['confidence_caveat']}"
                      if row["confidence_caveat"] else "")
            out.append(_claim(f"The governance gate status for this alert's data is "
                              f"{row['quality_status']}{caveat}.", "observed_fact", r.tool,
                              evidence_ids=[f"graph_snapshot:{row['graph_snapshot_id']}"]))
        elif row["kind"] == "quality_issue":
            rule = row["rule_id"] if quotable(row["rule_id"]) else "a data contract rule"
            out.append(_claim(f"Data-quality issue {row['check_id']} under {rule} "
                              f"({row['severity']}) affects {row['affected_rows']} rows; the "
                              "evidence may be incomplete.", "observed_fact", r.tool,
                              evidence_ids=[row["check_id"]]))
        elif row["kind"] == "not_available":
            out.append(_claim(row["note"], "missing_evidence", r.tool))
    if lineage:
        out.append(_claim("Evidence lineage: " + ", ".join(lineage) + ".", "observed_fact",
                          r.tool, evidence_ids=lineage))
    if not any(row["kind"] == "quality_issue" for row in r.rows):
        out.append(_claim("No data-quality issues were recorded on this alert's evidence bundle.",
                          "observed_fact", r.tool, evidence_ids=lineage or
                          [f"graph_snapshot:{scope.graph_snapshot_id}"]))
    return out


def _policy_claims(r: ToolResult) -> list[ChatClaim]:
    if not r.rows:
        return [_claim("Policy retrieval returned no sources for this question, so no policy "
                       "guidance was checked.", "missing_evidence", r.tool)]
    out = []
    for c in r.rows[:MAX_POLICY_CLAIMS]:
        title = c["doc_title"] if quotable(c.get("doc_title")) else "A retrieved policy source"
        section = f" ({c['section']})" if quotable(c.get("section")) else ""
        excerpt = str(c.get("excerpt") or "").strip()[:MAX_EXCERPT_CHARS]
        text = f"{title}{section} is a retrieved policy source for this question"
        text += f": \"{excerpt}\"" if quotable(excerpt) else "."
        out.append(_claim(text, "policy", r.tool, chunk_ids=[c["chunk_id"]]))
    return out


def _similar_claims(r: ToolResult) -> list[ChatClaim]:
    return [
        _claim(f"Mock reference pattern {row['reference_id']} ({row['typology']}): "
               f"{row['description']} Benign explanations to rule in or out: "
               f"{', '.join(row['common_benign_explanations'])}. This is a reference pattern, "
               "not another case.", "model_inference", r.tool,
               evidence_ids=[row["reference_id"]])
        for row in r.rows[:MAX_CLAIMS_PER_TOOL]
    ]


_BUILDERS = {
    "transaction_lookup": lambda r, s: _lookup_claims(r),
    "trace_forward": lambda r, s: _trace_claims(r),
    "trace_backward": lambda r, s: _trace_claims(r),
    "time_window_aggregate": lambda r, s: _aggregate_claims(r),
    "cycle_search": lambda r, s: _cycle_claims(r),
    "related_entities": lambda r, s: _related_claims(r),
    "model_explanation": _model_claims,
    "quality_lineage": _quality_claims,
    "policy_retrieval": lambda r, s: _policy_claims(r),
    "similar_cases": lambda r, s: _similar_claims(r),
}


def failure_text(r: ToolResult) -> str:
    if r.status == "timeout":
        return f"The {r.tool} tool timed out ({r.error}); its data was not checked."
    if r.status == "rejected":
        return f"The {r.tool} request was not run ({r.error}); nothing was checked."
    return f"The {r.tool} tool failed ({r.error}); its data was not checked."


def tool_claims(results: list[ToolResult], scope: CaseScope) -> list[ChatClaim]:
    """Claims for every tool result, in plan order. A failed tool contributes only a
    `missing_evidence` claim saying so."""
    claims: list[ChatClaim] = []
    for r in results:
        if r.status != "ok":
            claims.append(_claim(failure_text(r), "missing_evidence", r.tool))
            continue
        claims += _BUILDERS[r.tool](r, scope)
        claims += [_claim(f"{r.tool}: {w}.", "missing_evidence", r.tool) for w in r.warnings]
        if r.truncated:
            claims.append(_claim(f"{r.tool} returned more than {base.MAX_ROWS} rows; only the "
                                 "first ones were checked.", "missing_evidence", r.tool))
    return claims


# --------------------------------------------------------------------------- benign support


def _seen_edges(results: list[ToolResult], scope: CaseScope) -> list[SubgraphEdge]:
    ids = {i for r in results if r.status == "ok" for i in (*r.refs.tx_ids, *r.refs.edge_ids)}
    return sorted((scope.edges[i] for i in ids if i in scope.edges),
                  key=lambda e: (e.timestamp, e.transaction_id))


def benign_support(results: list[ToolResult], scope: CaseScope) -> dict[str, list[str]]:
    """Benign kind → transaction ids that support it, from this turn's tool results only.

    - payroll: one origin pays at least 3 distinct recipients on one day, same currency, with the
      largest amount at most 10% above the smallest.
    - refund / reversal: a later transaction returns the same amount between the same pair, or a
      looked-up row's status/channel says so.
    - treasury_sweep / shared_service: need account ownership data the agents don't have, so they
      are never supported here. # TODO(review): add once the backend supplies ownership.
    """
    edges = _seen_edges(results, scope)
    support: dict[str, list[str]] = defaultdict(list)

    batches: dict[tuple[str, str, str], list[SubgraphEdge]] = defaultdict(list)
    for e in edges:
        batches[(e.origin_account_id, e.timestamp.date().isoformat(), e.currency)].append(e)
    for batch in batches.values():
        amounts = [e.amount for e in batch]
        if (len({e.destination_account_id for e in batch}) >= PAYROLL_MIN_RECIPIENTS
                and min(amounts) > 0 and max(amounts) / min(amounts) <= PAYROLL_MAX_SPREAD):
            support["payroll"] += [e.transaction_id for e in batch]

    for i, a in enumerate(edges):
        for b in edges[i + 1:]:
            if (b.origin_account_id == a.destination_account_id
                    and b.destination_account_id == a.origin_account_id
                    and b.amount == a.amount and b.currency == a.currency):
                for kind in ("refund", "reversal"):
                    support[kind] += [a.transaction_id, b.transaction_id]

    for r in results:
        if r.status != "ok" or r.tool != "transaction_lookup":
            continue
        for row in r.rows:
            status = str(row.get("transaction_status") or "").lower()
            channel = str(row.get("channel") or "").lower()
            if status in REVERSAL_STATUSES:
                support["reversal"].append(row["transaction_id"])
            if status in REFUND_MARKERS or channel in REFUND_MARKERS:
                support["refund"].append(row["transaction_id"])
    return {k: sorted(set(v)) for k, v in sorted(support.items())}
