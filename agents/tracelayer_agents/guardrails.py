"""Language no LLM output may contain (CLAUDE.md rule 6): crime attribution and SAR filing status
or readiness. Shared by the case verifier (`verify_evidence`) and the copilot (`verify_citations`).

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
    "guilty",
)


def _phrase_pattern(phrase: str) -> str:
    return r"[\s\-]+".join(re.escape(word) for word in phrase.split())


PROHIBITED_RE = re.compile(
    r"\b(?:" + "|".join(_phrase_pattern(p) for p in PROHIBITED_PHRASES) + r")\b",
    re.IGNORECASE,
)


def find_prohibited(text: str) -> list[str]:
    """Every prohibited phrase in `text`, as written, in order of appearance."""
    return [m.group(0) for m in PROHIBITED_RE.finditer(text or "")]
