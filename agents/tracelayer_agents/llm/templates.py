"""Deterministic fallbacks, one per prompt name.

`llm.client` uses these when Ollama is not configured, unreachable, over the per-run call cap, or
returns output that fails validation twice. A template must never fail: it builds its output only
from the variables it is given. `llm.client` refuses to import if a prompt file in `prompts/` has no
template registered here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from tracelayer_agents.schemas import Contract, PolicyClaim, PolicyQAOutput

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
