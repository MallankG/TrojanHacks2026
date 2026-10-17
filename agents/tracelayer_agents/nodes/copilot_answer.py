"""Policy RAG synthesis (node 6): answer the copilot question from the retrieved chunks only, via
the `policy_qa` prompt. `verify_citations` runs next and drops any claim it can't trace to a chunk.
"""

from collections.abc import Mapping
from typing import Any

from tracelayer_agents.llm import client as llm
from tracelayer_agents.llm.templates import TEMPLATES
from tracelayer_agents.nodes.retrieve_policy import format_sources
from tracelayer_agents.schemas import CopilotAnswerOutput, PolicyAnswer, PolicyQAOutput
from tracelayer_agents.wrappers import NodeContext, traced_node

PROMPT_NAME = "policy_qa"


@traced_node("copilot_answer", CopilotAnswerOutput, llm=True)
async def copilot_answer(state: Mapping[str, Any], ctx: NodeContext) -> CopilotAnswerOutput:
    question = state["question"]
    chunks = state.get("retrieved_sources") or []
    variables = {
        "question": question,
        "sources": format_sources(chunks),
        "chunks": [c.model_dump(mode="json") for c in chunks],
    }

    if not chunks:
        # Nothing to ground an answer in: don't ask the model, it could only make one up.
        output = TEMPLATES[PROMPT_NAME](variables)
        assert isinstance(output, PolicyQAOutput)
        provenance = {
            "provider": "template",
            "model": llm.TEMPLATE_MODEL,
            "prompt_version": llm.PROMPTS[PROMPT_NAME].stem,
            "fallback_used": True,
        }
    else:
        output, meta = await llm.generate_structured(
            PROMPT_NAME, variables, PolicyQAOutput, trace_id=state.get("trace_id") or "untraced"
        )
        provenance = {
            "provider": meta.provider,
            "model": meta.model,
            "prompt_version": meta.prompt_version,
            "fallback_used": meta.fallback_used,
        }

    return CopilotAnswerOutput(
        answer=PolicyAnswer(
            question=question,
            answer=output.answer,
            claims=output.claims,
            unknowns=output.unknowns,
            citations=list(dict.fromkeys(i for c in output.claims for i in c.citation_chunk_ids)),
            no_answer=output.no_answer,
            **provenance,
        )
    )
