"""Deterministic verifiers (CLAUDE.md rule 3): nothing an LLM wrote reaches the user unless it
traces back to evidence or retrieved sources."""

from collections.abc import Mapping
from typing import Any

from tracelayer_agents.llm.templates import POLICY_NO_ANSWER
from tracelayer_agents.schemas import (
    PolicyAnswer,
    RetrievedChunk,
    VerifierError,
    VerifyCitationsOutput,
)
from tracelayer_agents.wrappers import NodeContext, traced_node


def check_citations(
    answer: PolicyAnswer, sources: list[RetrievedChunk]
) -> tuple[PolicyAnswer, list[VerifierError]]:
    """Drop every claim with no citation or with a chunk_id that wasn't retrieved, and rebuild
    `citations` from the claims that remain. An answer left with no supported claim becomes
    `no_answer=True`, since its prose can no longer be traced to a source."""
    known = {c.chunk_id for c in sources}
    errors: list[VerifierError] = []
    kept = []
    for i, claim in enumerate(answer.claims):
        if not claim.citation_chunk_ids:
            errors.append(VerifierError(claim_index=i, code="missing_citation",
                                        detail="claim cites no chunk_id"))
            continue
        unknown = [cid for cid in claim.citation_chunk_ids if cid not in known]
        if unknown:
            # TODO(review): drops the whole claim even if it also cites a real chunk; the more
            # conservative choice, since the claim may rest on the fabricated source.
            errors.append(VerifierError(claim_index=i, code="unknown_citation",
                                        detail=f"chunk_ids not retrieved: {unknown}"))
            continue
        kept.append(claim)

    update: dict[str, Any] = {
        "claims": kept,
        "citations": list(dict.fromkeys(cid for c in kept for cid in c.citation_chunk_ids)),
    }
    if not kept and not answer.no_answer:
        errors.append(VerifierError(claim_index=None, code="missing_citation",
                                    detail="answer has no claim supported by a retrieved chunk"))
        update |= {"no_answer": True, "answer": POLICY_NO_ANSWER}
    return answer.model_copy(update=update), errors


@traced_node("verify_citations", VerifyCitationsOutput)
def verify_citations(state: Mapping[str, Any], ctx: NodeContext) -> VerifyCitationsOutput:
    answer, errors = check_citations(state["answer"], state.get("retrieved_sources") or [])
    return VerifyCitationsOutput(answer=answer, verifier_errors=errors)
