"""Request guard for the investigation chat (PRD 8.2): runs on the question before any plan.

- **Deny** a question that names records outside this chat's case (transaction, account, case or
  alert ids that are not in scope) or asks about another tenant/institution. Nothing runs and the
  explanation repeats no id.
- **Narrow** an unbounded question (more than `MAX_HOPS` hops, "all accounts", "all time") to the
  case bounds: hops are capped, the window is the case window, the entities are the case's
  accounts. The answer says which bounds were applied.

Plans are clamped the same way (`clamp_plan`) whatever produced them, so an LLM plan asking for 6
hops runs with 3 instead of failing validation.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from tracelayer_agents.chat.session import CaseScope
from tracelayer_agents.tools.base import MAX_HOPS

HOP_TOOLS = frozenset({"trace_forward", "trace_backward", "related_entities"})
# Ids the agents use: TX-…, ACC-…, case_…, alrt_…, chat_…
FOREIGN_ID_RE = re.compile(
    r"\b(?:TX|ACC)-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\b|\b(?:case|alrt|chat)_[0-9a-f]{6,}\b"
)
OTHER_TENANT_RE = re.compile(
    r"\b(?:other|another|different|all)\s+(?:tenants?|banks?|institutions?|clients?'?\s+cases?|"
    r"customers?'?\s+cases?)\b|\btenant[_\s-]?(?:id\s*)?[=:]?\s*\w+",
    re.IGNORECASE,
)
HOPS_RE = re.compile(r"\b(\d{1,3})\s*[- ]?\s*hops?\b", re.IGNORECASE)
UNBOUNDED_HOPS_RE = re.compile(
    r"\b(?:all|any number of|unlimited|infinite|every)\s+hops?\b|\b(?:entire|whole|full)\s+"
    r"(?:graph|network|bank)\b", re.IGNORECASE)
ALL_ACCOUNTS_RE = re.compile(
    r"\b(?:all|every|any)\s+(?:the\s+)?(?:accounts?|customers?|entities|counterparties)\b"
    r"|\bevery(?:one|body)\b", re.IGNORECASE)
ALL_TIME_RE = re.compile(
    r"\ball[- ]time\b|\bever\b|\bentire history\b|\bfull history\b|\bsince (?:the )?beginning\b"
    r"|\bno time (?:limit|window)\b|\bwithout (?:a |any )?time (?:limit|window)\b"
    r"|\bany date\b|\ball dates\b", re.IGNORECASE)

DENY_TEXT = {
    "out_of_scope_ids": "it names records that are not part of this case",
    "other_tenant": "it asks about another tenant or institution",
    "access_denied": "you do not have access to this case",
}
NARROW_TEXT = {
    "hops_capped": f"Graph traversal was limited to {MAX_HOPS} hops (the maximum).",
    "window_limited": "The time window was limited to this case's window (evidence span ± 7 "
                      "days).",
    "entities_limited": "The accounts searched were limited to this case's accounts.",
}


def denial_message(reasons: list[str]) -> str:
    why = "; ".join(DENY_TEXT.get(r, r) for r in reasons) or "it is outside this chat's scope"
    return (f"This request was not run because {why}. This chat can only look at the records of "
            "its own case, so nothing was looked up and no records are shown. Ask about this "
            "case's transactions or accounts, or use a chip.")


def assess(question: str | None, scope: CaseScope) -> tuple[list[str], list[str], int | None]:
    """(deny reason codes, narrowing codes, requested hops) for a free-text question."""
    text = question or ""
    denied: list[str] = []
    in_scope = scope.tx_ids | scope.account_ids | {scope.case_id, scope.alert.alert_id}
    if any(m.group(0) not in in_scope for m in FOREIGN_ID_RE.finditer(text)):
        denied.append("out_of_scope_ids")
    if OTHER_TENANT_RE.search(text):
        denied.append("other_tenant")

    narrowed: list[str] = []
    hops = [int(m.group(1)) for m in HOPS_RE.finditer(text)]
    requested = max(hops) if hops else None
    if (requested is not None and requested > MAX_HOPS) or UNBOUNDED_HOPS_RE.search(text):
        narrowed.append("hops_capped")
        requested = MAX_HOPS
    if ALL_TIME_RE.search(text):
        narrowed.append("window_limited")
    if ALL_ACCOUNTS_RE.search(text):
        narrowed.append("entities_limited")
    return denied, narrowed, requested


def clamp_plan(
    calls: list[dict[str, Any]], scope: CaseScope, requested_hops: int | None
) -> tuple[list[dict[str, Any]], list[str]]:
    """Clamp loose plan dicts (before validation) to the case bounds: hops to 1..MAX_HOPS (or the
    capped hops the question asked for), cycle length to MAX_HOPS + 1, and a time window to the
    case window. Ids are not touched: an out-of-scope id still fails the scope check."""
    narrowed: list[str] = []
    out = []
    for call in calls:
        if not isinstance(call, dict) or not isinstance(call.get("args", {}), dict):
            out.append(call)
            continue
        args = dict(call.get("args") or {})
        hops = args.get("hops")
        if requested_hops is not None and call.get("tool") in HOP_TOOLS:
            args["hops"] = requested_hops  # what the question asked for, already capped
        elif isinstance(hops, int) and not isinstance(hops, bool) and hops > MAX_HOPS:
            args["hops"] = MAX_HOPS
            narrowed.append("hops_capped")
        max_len = args.get("max_len")
        if isinstance(max_len, int) and not isinstance(max_len, bool) and max_len > MAX_HOPS + 1:
            args["max_len"] = MAX_HOPS + 1
            narrowed.append("hops_capped")
        for key, bound, later in (("start", scope.window_start, max),
                                  ("end", scope.window_end, min)):
            value = args.get(key)
            if isinstance(value, str):
                try:
                    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if ts.tzinfo is not None and later(ts, bound) != ts:
                    args[key] = bound.isoformat()
                    narrowed.append("window_limited")
        out.append({**call, "args": args})
    return out, list(dict.fromkeys(narrowed))


def bounds_text(codes: list[str]) -> list[str]:
    return [NARROW_TEXT[c] for c in dict.fromkeys(codes) if c in NARROW_TEXT]
