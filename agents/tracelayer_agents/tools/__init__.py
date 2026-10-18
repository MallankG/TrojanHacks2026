"""The chat's read-only tool allowlist. `REGISTRY` is the only set of tools a chat can run;
`run_tool` never raises (failures come back as a `ToolResult` status)."""

from __future__ import annotations

import asyncio
import time

from pydantic import ValidationError

from tracelayer_agents.chat.session import CaseScope
from tracelayer_agents.schemas import CHAT_TOOLS, ToolCall, ToolResult
from tracelayer_agents.tools import base
from tracelayer_agents.tools.base import ScopeError, ToolSpec, no_check
from tracelayer_agents.tools.case_info import (
    EmptyInput,
    ModelExplanationInput,
    PolicyRetrievalInput,
    check_model,
    model_explanation,
    policy_retrieval,
    quality_lineage,
    similar_cases,
)
from tracelayer_agents.tools.flows import (
    CycleSearchInput,
    RelatedEntitiesInput,
    TraceInput,
    check_cycles,
    check_related,
    check_trace,
    cycle_search,
    related_entities,
    trace_backward,
    trace_forward,
)
from tracelayer_agents.tools.transactions import (
    TimeWindowAggregateInput,
    TransactionLookupInput,
    check_aggregate,
    check_lookup,
    time_window_aggregate,
    transaction_lookup,
)
from tracelayer_agents.wrappers import NodeContext

REGISTRY: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec("transaction_lookup", TransactionLookupInput, transaction_lookup, check_lookup,
                 "Full rows for up to 50 transactions in this case."),
        ToolSpec("trace_backward", TraceInput, trace_backward, check_trace,
                 "Time-respecting paths of funds INTO an account or transaction (hops 1-3)."),
        ToolSpec("trace_forward", TraceInput, trace_forward, check_trace,
                 "Time-respecting paths of funds OUT of an account or transaction (hops 1-3)."),
        ToolSpec("time_window_aggregate", TimeWindowAggregateInput, time_window_aggregate,
                 check_aggregate,
                 "Inflow/outflow totals and counts per account (or per currency) in a window "
                 "inside the case window."),
        ToolSpec("cycle_search", CycleSearchInput, cycle_search, check_cycles,
                 "Time-respecting cycles of up to 4 accounts in the case neighbourhood."),
        ToolSpec("related_entities", RelatedEntitiesInput, related_entities, check_related,
                 "Accounts within 1-3 hops of an account, with the connecting transactions."),
        ToolSpec("model_explanation", ModelExplanationInput, model_explanation, check_model,
                 "Composite and component detector scores and top factors for the alert."),
        ToolSpec("quality_lineage", EmptyInput, quality_lineage, no_check,
                 "Data-quality issues, lineage refs and data the agents do not have."),
        ToolSpec("policy_retrieval", PolicyRetrievalInput, policy_retrieval, no_check,
                 "Curated policy passages (query optional; defaults to the alert typology)."),
        ToolSpec("similar_cases", EmptyInput, similar_cases, no_check,
                 "Mock: reference typology patterns matching this alert (no other cases)."),
    )
}
assert set(REGISTRY) == set(CHAT_TOOLS), "REGISTRY must match schemas.ChatToolName"


def validate_call(call: ToolCall, scope: CaseScope) -> str | None:
    """None if `call` may run in this scope, else a short reason (no ids echoed)."""
    spec = REGISTRY[call.tool]
    try:
        args = spec.input_model.model_validate(call.args)
        spec.check(args, scope)
    except ValidationError as exc:
        return f"invalid_args: {_first_error(exc)}"
    except ScopeError as exc:
        return str(exc)
    return None


def _first_error(exc: ValidationError) -> str:
    err = exc.errors(include_input=False, include_url=False)[0]
    where = ".".join(str(p) for p in err.get("loc", ())) or "args"
    return f"{where}: {err.get('msg', 'invalid')}"[:160]


async def run_tool(
    call: ToolCall, scope: CaseScope, ctx: NodeContext, *, timeout_s: float | None = None
) -> ToolResult:
    """Validate, scope-check and run one tool under `base.TOOL_TIMEOUT_S`. Never raises."""
    timeout = timeout_s if timeout_s is not None else base.TOOL_TIMEOUT_S
    spec = REGISTRY[call.tool]
    t0 = time.perf_counter()

    def result(status: str, **kw) -> ToolResult:
        ms = round((time.perf_counter() - t0) * 1000, 3)
        return ToolResult(tool=call.tool, status=status, duration_ms=ms,
                          **{"args": call.args, **kw})

    try:
        args = spec.input_model.model_validate(call.args)
        spec.check(args, scope)
    except ValidationError as exc:
        return result("rejected", error=f"invalid_args: {_first_error(exc)}")
    except ScopeError as exc:
        return result("rejected", error=str(exc))

    try:
        out = await asyncio.wait_for(asyncio.to_thread(spec.fn, args, scope, ctx), timeout)
    except TimeoutError:
        return result("timeout", error=f"exceeded {timeout:g}s")
    except ScopeError as exc:
        return result("rejected", error=str(exc))
    except Exception as exc:  # the message may carry ids or internals; keep only the type
        return result("error", error=f"{type(exc).__name__}")
    return result(
        "ok", args=args.model_dump(mode="json", exclude_none=True), rows=out.rows,
        row_count=len(out.rows), truncated=out.truncated, refs=out.refs,
        computed=out.computed, warnings=out.warnings,
    )
