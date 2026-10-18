"""Policy retrieval for the case and copilot workflows. Retrieval itself is the RAG teammate's
`PolicyRetriever`; this node only builds the query and stores the chunks.

Chunk text is untrusted (CLAUDE.md rule 3, PRD §15): `format_sources` is the only way chunks reach a
prompt, each escaped and wrapped in its own `<source id="...">` block.
"""

import asyncio
import html
from collections.abc import Mapping
from typing import Any

from tracelayer_agents.schemas import FlowFindings, RetrievedChunk, RetrievePolicyOutput
from tracelayer_agents.wrappers import NodeContext, SkipNode, traced_node

# FlowFindings pattern lists → the words policy text uses for them.
_FLOW_TERMS = {
    "paths": "rapid movement of funds through accounts",
    "cycles": "circular round-trip transfers",
    "fan_in": "fan-in funnel transfers from numerous senders",
    "fan_out": "fan-out transfers to numerous recipients",
    "pass_through": "pass-through in-out activity",
}


def build_query(state: Mapping[str, Any]) -> str:
    """The copilot question if there is one; otherwise the alert's typology tags plus the kinds of
    flow pattern found around it (case workflow)."""
    question = (state.get("question") or "").strip()
    if question:
        return question

    terms: list[str] = []
    alert = state.get("alert")
    if alert is not None:
        terms += [tag.replace("_", "-") for tag in alert.typology_tags]
    bundle = state.get("evidence_bundle")
    findings: FlowFindings | None = bundle.flow_findings if bundle is not None else None
    if findings is not None:
        terms += [phrase for name, phrase in _FLOW_TERMS.items() if getattr(findings, name)]
    if not terms:
        return ""
    return "suspicious activity red flags: " + "; ".join(dict.fromkeys(terms))


def format_sources(chunks: list[RetrievedChunk]) -> str:
    """Chunks as `<source id="CHUNK_ID" ...>excerpt</source>` blocks. Everything is HTML-escaped,
    so a chunk can't close its own block or forge another source."""
    if not chunks:
        return "(no sources were retrieved)"
    blocks = []
    for chunk in chunks:
        attrs = f'id="{html.escape(chunk.chunk_id)}" title="{html.escape(chunk.doc_title)}"'
        if chunk.section:
            attrs += f' section="{html.escape(chunk.section)}"'
        blocks.append(f"<source {attrs}>\n{html.escape(chunk.excerpt)}\n</source>")
    return "\n\n".join(blocks)


@traced_node("retrieve_policy", RetrievePolicyOutput)
async def retrieve_policy(state: Mapping[str, Any], ctx: NodeContext) -> RetrievePolicyOutput:
    query = build_query(state)
    if not query:
        raise SkipNode("no question, typology tags or flow findings to build a query from")
    chunks = await asyncio.to_thread(
        ctx.services.policy.search, query, {}, ctx.config.policy_top_k
    )
    return RetrievePolicyOutput(retrieved_sources=chunks)
