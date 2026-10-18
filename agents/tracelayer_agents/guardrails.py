"""Language no LLM output may contain (CLAUDE.md rule 6): crime or intent attribution and SAR
filing status or readiness. Shared by the case verifier (`verify_evidence`) and the copilot
(`verify_citations`).

Matching is case-insensitive, on word boundaries, and tolerant of extra whitespace or a hyphen
between words ("SAR-ready", "SAR  filed").
"""

from __future__ import annotations

import re

# TODO(review): conservative starter list; extend with compliance before a real deployment.
PROHIBITED_PHRASES: tuple[str, ...] = (
    "committed fraud",
    "committed a crime",
    "is laundering",
    "money launderer",
    "laundered money",
    "SAR filed",
    "filed a SAR",
    "SAR ready",
    "file a SAR on",
    # "guilty" only as an assertion, so "not guilty" passes
    "is guilty",
    "are guilty",
    "found guilty",
)

# Attributing intent to the customer. Checked in case narratives only: policy text (and so a
# copilot answer explaining it) legitimately defines suspicious activity by intent, e.g. "designed
# to evade BSA requirements".
INTENT_PHRASES: tuple[str, ...] = (
    "to avoid detection",
    "to evade",
    "intended to conceal",
    "in order to launder",
    "deliberately structured",
    "to avoid regulatory thresholds",
    "to avoid reporting",
    "to stay below reporting",
    "to circumvent",
)


def _phrase_pattern(phrase: str) -> str:
    return r"[\s\-]+".join(re.escape(word) for word in phrase.split())


def _phrases_re(phrases: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(
        r"\b(?:" + "|".join(_phrase_pattern(p) for p in phrases) + r")\b", re.IGNORECASE
    )


PROHIBITED_RE = _phrases_re(PROHIBITED_PHRASES)
PROHIBITED_WITH_INTENT_RE = _phrases_re(PROHIBITED_PHRASES + INTENT_PHRASES)


def find_prohibited(text: str, *, intent: bool = False) -> list[str]:
    """Every prohibited phrase in `text`, as written, in order of appearance. `intent=True` also
    checks `INTENT_PHRASES` (case narratives)."""
    pattern = PROHIBITED_WITH_INTENT_RE if intent else PROHIBITED_RE
    return [m.group(0) for m in pattern.finditer(text or "")]


# Investigation chat only (`chat.verify_chat`): the chat never recommends a disposition (the
# reviewer decides, CLAUDE.md rule 6) and never treats graph proximity as proof.
# TODO(review): starter lists; extend with compliance.
DISPOSITION_PHRASES: tuple[str, ...] = (
    "should be escalated",
    "should escalate",
    "recommend escalating",
    "recommend escalation",
    "should be dismissed",
    "should dismiss",
    "recommend dismissing",
    "recommend dismissal",
    "close the case",
    "close this case",
    "recommend filing",
    "should be filed",
    "should file",
    "freeze the account",
    "freeze this account",
    "block the account",
    "exit the customer",
    "offboard the customer",
)
PROXIMITY_PHRASES: tuple[str, ...] = (
    "guilt by association",
    "proves",
    "proven to be",
    "therefore suspicious",
    "therefore involved",
    "must be involved",
    "is complicit",
    "are complicit",
    "confirms wrongdoing",
    "confirms laundering",
)

DISPOSITION_RE = _phrases_re(DISPOSITION_PHRASES + PROXIMITY_PHRASES)


def find_disposition(text: str) -> list[str]:
    """Disposition recommendations and proximity-as-proof phrases in `text` (chat only)."""
    return [m.group(0) for m in DISPOSITION_RE.finditer(text or "")]
