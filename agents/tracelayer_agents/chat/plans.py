"""Prompt chips → deterministic tool plans (at most `MAX_TOOL_CALLS_PER_TURN` calls each).

Seeds come from the reviewer's pins when they are in scope, else from the alert: the alert's
transactions and the account that touches the most evidence transactions (the hub)."""

from __future__ import annotations

import re

from tracelayer_agents.chat.session import CaseScope
from tracelayer_agents.schemas import CHAT_CHIPS, ToolCall
from tracelayer_agents.tools.base import MAX_ROWS as MAX_ROWS_DEFAULT

# Keyword fallback for free text when no model is available (first match wins).
CHIP_KEYWORDS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (chip, re.compile(pattern, re.IGNORECASE)) for chip, pattern in (
        ("benign_explanations", r"\b(?:benign|legitimate|innocent|payroll|refund|reversal|sweep"
                                r"|innocuous|harmless)\b"),
        ("trace_backward", r"\b(?:backward|came from|source of|where .* from|inbound|funded)\b"),
        ("trace_forward", r"\b(?:forward|onward|went|go after|where did .* go|destination|outbound"
                           r"|end up)\b"),
        ("why_connected", r"\b(?:connect|connected|related|relationship|link|linked)\b"),
        ("policy_guidance", r"\b(?:policy|guidance|regulat\w*|ffiec|fincen|rule|requirement)\b"),
        ("missing_data", r"\b(?:missing|quality|gap|incomplete|lineage|data)\b"),
        ("similar_cases", r"\b(?:similar|like this|other cases|comparable)\b"),
        ("next_steps", r"\b(?:next|follow[- ]?up|what should i|to do)\b"),
        ("evidence_per_signal", r"\b(?:signal|detector|component|factor)\b"),
        ("explain_alert", r"\b(?:explain|why|score|flagged|alert)\b"),
    )
)


def keyword_chip(text: str) -> str | None:
    return next((chip for chip, pattern in CHIP_KEYWORDS if pattern.search(text or "")), None)


def _seeds(scope: CaseScope, pinned: list[str]) -> tuple[list[str], str]:
    pinned_txs = [i for i in pinned if i in scope.edges]
    pinned_accounts = [i for i in pinned if i in scope.account_ids]
    txs = pinned_txs or list(scope.alert.tx_ids)
    hub = pinned_accounts[0] if pinned_accounts else scope.hub_accounts[0]
    return [t for t in txs if t in scope.edges][:MAX_ROWS_DEFAULT], hub


def chip_plan(chip: str, scope: CaseScope, pinned: list[str]) -> list[ToolCall]:
    if chip not in CHAT_CHIPS:
        raise ValueError(f"unknown chip {chip!r}")
    txs, hub = _seeds(scope, pinned)
    evidence = scope.evidence_tx_ids[:MAX_ROWS_DEFAULT]

    def call(tool: str, **args) -> ToolCall:
        return ToolCall(tool=tool, args=args)

    plans: dict[str, list[ToolCall]] = {
        "explain_alert": [call("model_explanation", tx_ids=txs), call("quality_lineage"),
                          call("transaction_lookup", tx_ids=txs)],
        "trace_backward": [call("trace_backward", account_id=hub, hops=2)],
        "trace_forward": [call("trace_forward", account_id=hub, hops=2)],
        "why_connected": [call("related_entities", account_id=hub, hops=2),
                          call("trace_forward", account_id=hub, hops=2), call("cycle_search")],
        "evidence_per_signal": [call("model_explanation", tx_ids=evidence),
                                call("transaction_lookup", tx_ids=evidence)],
        "benign_explanations": [call("time_window_aggregate", account_ids=[hub]),
                                call("trace_forward", account_id=hub, hops=1),
                                call("transaction_lookup", tx_ids=evidence)],
        "policy_guidance": [call("policy_retrieval")],
        "missing_data": [call("quality_lineage")],
        "similar_cases": [call("similar_cases")],
        "next_steps": [call("quality_lineage"), call("policy_retrieval"),
                       call("model_explanation", tx_ids=txs)],
    }
    return plans[chip]
