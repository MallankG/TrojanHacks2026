"""Narrative drafter (node 7): the `narrative` prompt over the evidence bundle, derived facts and
retrieved policy only. `verify_evidence` runs next; on a retry the previous draft's verifier errors
are added to the prompt.
"""

from collections.abc import Mapping
from typing import Any

from tracelayer_agents.llm import client as llm
from tracelayer_agents.nodes.retrieve_policy import format_sources
from tracelayer_agents.schemas import DraftNarrativeOutput, NarrativeDraft, NarrativeOutput
from tracelayer_agents.wrappers import NodeContext, traced_node

PROMPT_NAME = "narrative"


def _feedback(state: Mapping[str, Any]) -> str:
    if not state.get("draft_attempts"):
        return "(none)"
    lines = [
        f"- {e.code} (claim {e.claim_index}): {e.detail}"
        for e in state.get("verifier_errors") or []
    ]
    return "\n".join(lines) or "(none)"


@traced_node("draft_narrative", DraftNarrativeOutput)
async def draft_narrative(state: Mapping[str, Any], ctx: NodeContext) -> DraftNarrativeOutput:
    bundle = state["evidence_bundle"]
    chunks = state.get("retrieved_sources") or []
    variables = {
        "transactions": [t.model_dump(mode="json") for t in bundle.transactions],
        "flow_findings": (
            bundle.flow_findings.model_dump(mode="json") if bundle.flow_findings else {}
        ),
        "derived_facts": [f.model_dump(mode="json") for f in state.get("derived_facts") or []],
        "quality_caveats": [q.model_dump(mode="json") for q in bundle.quality_caveats],
        "sources": format_sources(chunks),
        "verifier_feedback": _feedback(state),
        "chunks": [c.model_dump(mode="json") for c in chunks],  # for the template only
    }
    output, meta = await llm.generate_structured(
        PROMPT_NAME, variables, NarrativeOutput, trace_id=state.get("trace_id") or "untraced"
    )
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
    return DraftNarrativeOutput(
        draft_narrative=draft,
        draft_attempts=(state.get("draft_attempts") or 0) + 1,
        llm_meta=meta,
    )
