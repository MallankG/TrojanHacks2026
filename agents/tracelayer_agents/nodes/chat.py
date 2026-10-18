"""chat_workflow nodes: classify → tool_query → draft_answer → verify → respond.

Only `chat_classify` (free text) and `chat_draft_answer` call the LLM, through the usual
Ollama → template chain. Tools run read-only inside the case scope; every number and id in an
answer is written by `chat.claims` in Python, and `chat_verify` drops anything it can't trace.
"""

from __future__ import annotations

import html
import json
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from tracelayer_agents import audit
from tracelayer_agents.audit import canonical_json, sha256_json
from tracelayer_agents.chat.claims import benign_support, tool_claims
from tracelayer_agents.chat.plans import chip_plan
from tracelayer_agents.chat.session import CaseScope
from tracelayer_agents.chat.verify_chat import (
    chat_grounding,
    check_arithmetic,
    gap,
    text_errors,
    verify_claims,
)
from tracelayer_agents.llm import client as llm
from tracelayer_agents.schemas import (
    CHAT_CHIPS,
    ChatClaim,
    ChatClassifyOutput,
    ChatDraftOutput,
    ChatPlan,
    ChatPlanDraft,
    ChatRespondOutput,
    ChatSynthesis,
    ChatToolQueryOutput,
    ChatVerifyOutput,
    ToolResult,
)
from tracelayer_agents.tools import REGISTRY, run_tool, validate_call
from tracelayer_agents.wrappers import NodeContext, SkipNode, traced_node

# Chips (and free text) whose answers may carry hypotheses.
HYPOTHESIS_CHIPS = frozenset({"explain_alert", "why_connected", "evidence_per_signal",
                              "benign_explanations", "next_steps"})
CLARIFY = ("I can only answer with read-only lookups inside this case. Please ask a narrower "
           "question (one account, transaction or signal), or use a chip.")
SUGGESTED_CHIPS = ["explain_alert", "trace_forward", "trace_backward", "missing_data"]


def _plans_json(scope: CaseScope, pinned: list[str]) -> dict[str, list[dict[str, Any]]]:
    return {chip: [c.model_dump(mode="json") for c in chip_plan(chip, scope, pinned)]
            for chip in CHAT_CHIPS}


def _case_context(scope: CaseScope, pinned: list[str]) -> dict[str, Any]:
    return {
        "alert_transaction_ids": list(scope.alert.tx_ids),
        "evidence_transaction_ids": scope.evidence_tx_ids,
        "hub_accounts": list(scope.hub_accounts),
        "accounts_in_scope": sorted(scope.account_ids)[:50],
        "pinned_evidence_ids": pinned,
        "case_window": [scope.window_start.isoformat(), scope.window_end.isoformat()],
        "typology_tags": list(scope.alert.typology_tags),
    }


@traced_node("chat_classify", ChatClassifyOutput, llm=True)
async def chat_classify(state: Mapping[str, Any], ctx: NodeContext) -> ChatClassifyOutput:
    scope: CaseScope = state["scope"]
    pinned = list(state.get("pinned") or [])
    chip = state.get("chip")
    if chip:
        return ChatClassifyOutput(intent=chip, plan=chip_plan(chip, scope, pinned))

    variables = {
        "question": html.escape(state.get("question") or ""),
        "tools": "\n".join(
            f"- {s.name}: {s.description}; {json.dumps(s.input_model.model_json_schema())}"
            for s in REGISTRY.values()
        ),
        "case_context": _case_context(scope, pinned),
        "chip_plans": _plans_json(scope, pinned),
    }
    raw, meta = await llm.generate_structured(
        "chat_plan", variables, ChatPlanDraft, trace_id=state.get("trace_id") or "untraced"
    )
    try:
        plan = ChatPlan.model_validate(raw.model_dump())
    except ValidationError:
        return ChatClassifyOutput(
            intent=raw.intent or "invalid", plan=[], llm_meta=[meta],
            clarification=f"{CLARIFY} (The question mapped to an invalid or too large plan; at "
                          "most 3 allowlisted tool calls are allowed.)",
        )
    if plan.intent == "unclear" or not plan.tool_calls:
        return ChatClassifyOutput(intent=plan.intent, plan=[], clarification=CLARIFY,
                                  llm_meta=[meta])
    problems = [p for p in (validate_call(c, scope) for c in plan.tool_calls) if p]
    if problems:
        return ChatClassifyOutput(
            intent=plan.intent, plan=[], llm_meta=[meta],
            clarification=f"{CLARIFY} (Not run: {'; '.join(dict.fromkeys(problems))}.)",
        )
    return ChatClassifyOutput(intent=plan.intent, plan=plan.tool_calls, llm_meta=[meta])


@traced_node("chat_tool_query", ChatToolQueryOutput)
async def chat_tool_query(state: Mapping[str, Any], ctx: NodeContext) -> ChatToolQueryOutput:
    if state.get("clarification"):
        raise SkipNode("no plan to run")
    results = [await run_tool(call, state["scope"], ctx) for call in state.get("plan") or []]
    return ChatToolQueryOutput(tool_results=results)


def _hypothesis_claims(synthesis: ChatSynthesis, facts: list[ChatClaim]) -> list[ChatClaim]:
    out = []
    for h in synthesis.hypotheses:
        refs = [facts[r - 1] for r in h.fact_refs if 1 <= r <= len(facts)]
        out.append(ChatClaim(
            text=h.text, label="hypothesis", benign_kind=h.benign_kind,
            transaction_ids=list(dict.fromkeys(i for c in refs for i in c.transaction_ids)),
            account_ids=list(dict.fromkeys(a for c in refs for a in c.account_ids)),
            evidence_ids=list(dict.fromkeys(e for c in refs for e in c.evidence_ids)),
            source_tools=list(dict.fromkeys(t for c in refs for t in c.source_tools)),
        ))
    return out


def _checked(results: list[ToolResult]) -> tuple[list[str], list[str]]:
    ok = list(dict.fromkeys(r.tool for r in results if r.status == "ok"))
    failed = list(dict.fromkeys(r.tool for r in results if r.status != "ok"))
    return ok, failed


@traced_node("chat_draft_answer", ChatDraftOutput, llm=True)
async def chat_draft_answer(state: Mapping[str, Any], ctx: NodeContext) -> ChatDraftOutput:
    if state.get("clarification"):
        raise SkipNode("no plan was run")
    scope: CaseScope = state["scope"]
    results = state.get("tool_results") or []
    facts = tool_claims(results, scope)
    support = benign_support(results, scope)
    checked, failed = _checked(results)
    chip = state.get("chip")
    variables = {
        "question": html.escape(state.get("question") or f"(chip: {chip})"),
        "chip": chip,
        "facts": [{"n": n, "label": c.label, "text": c.text,
                   "transaction_ids": c.transaction_ids, "account_ids": c.account_ids,
                   "source_tools": c.source_tools} for n, c in enumerate(facts, 1)],
        "benign_support": support,
        "checked_tools": checked,
        "failed_tools": failed,
        "typology_tags": list(scope.alert.typology_tags),
        "hub_account": scope.hub_accounts[0],
        "allow_hypotheses": chip is None or chip in HYPOTHESIS_CHIPS,
    }
    synthesis, meta = await llm.generate_structured(
        "chat_answer", variables, ChatSynthesis, trace_id=state.get("trace_id") or "untraced"
    )
    return ChatDraftOutput(
        draft_claims=[*facts, *_hypothesis_claims(synthesis, facts)],
        synthesis=synthesis, benign_support=support, llm_meta=[meta],
    )


def fallback_summary(results: list[ToolResult], kept: list[ChatClaim]) -> str:
    checked, failed = _checked(results)
    text = (f"Checked {', '.join(checked)}" if checked else "No tool returned results")
    text += ". Each verified statement below cites its source ids."
    if failed:
        text += f" Not checked because the tool failed or was not run: {', '.join(failed)}."
    return text


@traced_node("chat_verify", ChatVerifyOutput)
def chat_verify(state: Mapping[str, Any], ctx: NodeContext) -> ChatVerifyOutput:
    if state.get("clarification"):
        raise SkipNode("nothing to verify")
    scope: CaseScope = state["scope"]
    results, errors = check_arithmetic(state.get("tool_results") or [], scope)
    ground = chat_grounding(results, scope)
    kept, gaps, claim_errors = verify_claims(
        state.get("draft_claims") or [], ground, state.get("benign_support") or {}, scope
    )
    errors += claim_errors
    synthesis: ChatSynthesis = state["synthesis"]
    answer = synthesis.summary
    if errs := text_errors(answer, ground):
        gaps.append(gap("summary", answer, errs, scope))
        answer = fallback_summary(results, kept)
    next_steps = []
    for step in synthesis.next_steps:
        if errs := text_errors(step, ground):
            gaps.append(gap("next_step", step, errs, scope))
        else:
            next_steps.append(step)
    substantive = [c for c in kept if c.label != "missing_evidence"]
    status = ("verified" if not gaps else "partial" if substantive else "unverified")
    return ChatVerifyOutput(claims=kept, gaps=gaps, verifier_errors=errors, answer=answer,
                            next_steps=next_steps, verification_status=status)


def _arg_ref(args: dict[str, Any]) -> dict[str, Any]:
    """Tool args for the audit trail: references only; free text is hashed."""
    return {k: (sha256_json(v) if k == "query" and v else v) for k, v in args.items()}


@traced_node("chat_respond", ChatRespondOutput)
def chat_respond(state: Mapping[str, Any], ctx: NodeContext) -> ChatRespondOutput:
    scope: CaseScope = state["scope"]
    results: list[ToolResult] = state.get("tool_results") or []
    claims: list[ChatClaim] = state.get("claims") or []
    metas = state.get("llm_meta") or []
    answer_meta = metas[-1] if metas else None
    question = state.get("question") or ""
    clarified = bool(state.get("clarification"))
    event = audit.record(
        ctx.services.audit, action="chat.turn", object_type="case", object_id=scope.case_id,
        actor_id=state["actor_id"], actor_role=state["actor_role"],
        model_versions=dict(scope.alert.model_versions),
        prompt_version=answer_meta.prompt_version if answer_meta else None,
        metadata={
            "chat_id": state["chat_id"],
            "turn_id": state["turn_id"],
            "trace_id": state.get("trace_id"),
            "alert_id": scope.alert.alert_id,
            "chip": state.get("chip"),
            "question_hash": sha256_json(question) if question else None,
            "intent": state.get("intent"),
            "status": "needs_clarification" if clarified else "ok",
            "tool_calls": ";".join(f"{r.tool}:{r.status}:{r.row_count}" for r in results),
            "tool_args": canonical_json([{"tool": r.tool, "args": _arg_ref(r.args)}
                                         for r in results]),
            "evidence_refs": ",".join(dict.fromkeys(
                i for c in claims for i in (*c.transaction_ids, *c.account_ids,
                                            *c.evidence_ids))),
            "chunk_ids": ",".join(dict.fromkeys(i for c in claims for i in c.chunk_ids)),
            "model_run_id": scope.alert.model_run_id,
            "llm": ",".join(f"{m.prompt_version}:{m.provider}:{m.model}" for m in metas),
            "fallback_used": any(m.fallback_used for m in metas),
            "verification_status": state.get("verification_status") or "unverified",
            "claims_kept": len(claims),
            "claims_dropped": len(state.get("gaps") or []),
            "bundle_hash": scope.bundle.bundle_hash,
        },
    )
    return ChatRespondOutput(audit_events=[event])
