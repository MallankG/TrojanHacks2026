"""Narrative drafter (node 7), hybrid: every fact claim (patterns, transactions, per-account
sums) and policy claim is built deterministically (`llm.templates.narrative_fact_claims` /
`narrative_policy_claims`). The LLM (`narrative` prompt, v2) sees the fact claims as numbered
one-liners plus derived facts and source titles, and returns only a small `NarrativeSynthesis`:
summary, hypotheses that reference fact claims by number, uncertainties and follow-up questions.
`build_narrative` merges the two; `verify_evidence` runs next on the merged draft, and on a retry
the previous draft's verifier errors are added to the prompt. narrative_v4 adds the reviewer
sections (`llm.templates.narrative_sections`), built in Python on the live and precomputed paths
alike; the synthesis contributes only contradictory observations (by fact number) to them.

If the synthesis call fails or times out, only the synthesis comes from the template; the fact
claims are the same either way. `llm_meta` and `fallback_used` describe the synthesis call.
"""

import html
import re
from collections.abc import Mapping
from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, create_model

from tracelayer_agents import precomputed
from tracelayer_agents.llm import client as llm
from tracelayer_agents.llm.templates import (
    build_narrative,
    map_fact_refs,
    narrative_fact_claims,
    narrative_sections,
)
from tracelayer_agents.schemas import (
    NARRATIVE_MAX_HYPOTHESES,
    DraftNarrativeOutput,
    NarrativeDraft,
    NarrativeHypothesis,
    NarrativeSynthesis,
    RetrievedChunk,
)
from tracelayer_agents.wrappers import NodeContext, traced_node

PROMPT_NAME = "narrative"
# Per-account inflow/outflow facts grow with the bundle and the busiest accounts' sums are already
# fact claims, so the prompt lists only bundle-level and pattern facts. Keeps the prompt inside
# Ollama's default 2048-token context (the v1 prompt overflowed it).
_PER_ACCOUNT_FACT_RE = re.compile(r"^\S+ (?:inflow|outflow) ")


@lru_cache(maxsize=64)
def synthesis_model(n_fact_claims: int) -> type[NarrativeSynthesis]:
    """`NarrativeSynthesis` whose `fact_refs` may only be 1..n_fact_claims. The JSON schema is an
    enum, so Ollama's constrained decoding can't emit another number, and validation rejects one
    from any other source (which takes the client's retry, then template path)."""
    if n_fact_claims < 1:
        hypotheses: Any = (list[NarrativeHypothesis], Field(default_factory=list, max_length=0))
        contradictions: Any = hypotheses
    else:
        ref = Literal[tuple(range(1, n_fact_claims + 1))]  # type: ignore[valid-type]
        hypothesis = create_model(
            "NarrativeHypothesis", __base__=NarrativeHypothesis,
            fact_refs=(list[ref], Field(min_length=1)),
        )
        hypotheses = (
            list[hypothesis],  # type: ignore[valid-type]
            Field(default_factory=list, max_length=NARRATIVE_MAX_HYPOTHESES),
        )
        contradictions = hypotheses
    return create_model("NarrativeSynthesis", __base__=NarrativeSynthesis, hypotheses=hypotheses,
                        contradictions=contradictions)


def _feedback(state: Mapping[str, Any]) -> str:
    if not state.get("draft_attempts"):
        return "(none)"
    claims = getattr(state.get("draft_narrative"), "observed_facts", None) or []
    lines = []
    for e in state.get("verifier_errors") or []:
        index = e.claim_index
        where = (f"a {claims[index].claim_type} claim"
                 if index is not None and 0 <= index < len(claims)
                 else "summary, uncertainties or follow-up questions")
        lines.append(f"- {e.code} in {where}: {e.detail}")
    return "\n".join(lines) or "(none)"


def _source_titles(chunks: list[RetrievedChunk]) -> str:
    """Titles only (never excerpts), HTML-escaped so a title can't forge another source."""
    if not chunks:
        return "(no sources were retrieved)"
    lines = []
    for c in chunks:
        attrs = f'id="{html.escape(c.chunk_id)}" title="{html.escape(c.doc_title)}"'
        if c.section:
            attrs += f' section="{html.escape(c.section)}"'
        lines.append(f"<source {attrs} />")
    return "\n".join(lines)


@traced_node("draft_narrative", DraftNarrativeOutput, llm=True)
async def draft_narrative(state: Mapping[str, Any], ctx: NodeContext) -> DraftNarrativeOutput:
    bundle = state["evidence_bundle"]
    chunks = state.get("retrieved_sources") or []
    derived = state.get("derived_facts") or []
    attempts = (state.get("draft_attempts") or 0) + 1
    if ctx.config.use_precomputed:
        served = precomputed.golden_draft(
            state.get("alert_id"),
            {t.transaction_id for t in bundle.transactions},
            {c.chunk_id for c in chunks},
        )
        if served is not None:
            draft, meta = served
            # the stored synthesis' contradictions are the non-factual contradictory items
            contradictions = [(i.text, i.evidence_ids) for i in
                              (draft.sections.contradictory_evidence if draft.sections else [])
                              if not i.factual]
            draft = draft.model_copy(update={"sections": narrative_sections(
                draft, bundle, state.get("alert"), [c.model_dump(mode="json") for c in chunks],
                contradictions)})
            return DraftNarrativeOutput(
                draft_narrative=draft, draft_attempts=attempts, llm_meta=meta, precomputed=True
            )
    # Raw evidence for the deterministic builders and the template (not in the prompt).
    variables: dict[str, Any] = {
        "transactions": [t.model_dump(mode="json") for t in bundle.transactions],
        "flow_findings": (
            bundle.flow_findings.model_dump(mode="json") if bundle.flow_findings else {}
        ),
        "derived_facts": [f.model_dump(mode="json") for f in derived],
        "quality_caveats": [q.model_dump(mode="json") for q in bundle.quality_caveats],
        "chunks": [c.model_dump(mode="json") for c in chunks],
        "limitations": bundle.limitations,
    }
    fact_claims = narrative_fact_claims(variables)
    # Compact prompt inputs.
    variables |= {
        "fact_claims": "\n".join(f"[{i}] {c.text}" for i, c in enumerate(fact_claims, 1))
        or "(none)",
        "derived_facts_list": "\n".join(
            f"{f.fact_id} {f.label}: {f.value}" + (f" {f.currency}" if f.currency else "")
            for f in derived if not _PER_ACCOUNT_FACT_RE.match(f.label)
        ) or "(none)",
        "caveats": "\n".join(
            f"- {q.rule_id} ({q.severity}): {q.affected_rows} rows"
            for q in bundle.quality_caveats
        ) + "\n" + "\n".join(html.escape(x) for x in bundle.limitations) or "(none)",
        "source_titles": _source_titles(chunks),
        "verifier_feedback": _feedback(state),
    }
    synthesis, meta = await llm.generate_structured(
        PROMPT_NAME, variables, synthesis_model(len(fact_claims)),
        trace_id=state.get("trace_id") or "untraced",
    )
    output = build_narrative(variables, synthesis)
    claims = output.observed_facts
    draft = NarrativeDraft(
        **output.model_dump(),
        citations=list(dict.fromkeys(cid for c in claims for cid in c.citation_chunk_ids)),
        evidence_transaction_ids=list(dict.fromkeys(i for c in claims for i in c.transaction_ids)),
        provider=meta.provider,
        model=meta.model,
        prompt_version=meta.prompt_version,
        fallback_used=meta.fallback_used,
    )
    draft = draft.model_copy(update={"sections": narrative_sections(
        draft, bundle, state.get("alert"), variables["chunks"],
        map_fact_refs(synthesis.contradictions, fact_claims))})
    return DraftNarrativeOutput(
        draft_narrative=draft,
        draft_attempts=attempts,
        llm_meta=meta,
    )
