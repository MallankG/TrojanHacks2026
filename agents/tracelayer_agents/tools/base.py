"""Shared pieces of the chat tools: hard bounds, the input base model, scope checks and the
`ToolOutput` a tool function returns.

A tool is `fn(args, scope, ctx) -> ToolOutput`, synchronous (it calls the sync Protocols); the
runner (`tools.run_tool`) validates the args, checks them against the case scope, runs the
function in a worker thread under `TOOL_TIMEOUT_S`, and turns any failure into a `ToolResult`
with a status instead of raising.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, TypeVar

from pydantic import ConfigDict

from tracelayer_agents.chat.session import CaseScope
from tracelayer_agents.nodes.derive import money
from tracelayer_agents.schemas import ComputedValue, Contract, SubgraphEdge, ToolRefs
from tracelayer_agents.wrappers import NodeContext

MAX_HOPS = 3
MAX_ROWS = 50
TOOL_TIMEOUT_S = 5.0
MAX_WALK_STATES = 5000  # hard stop for path enumeration

T = TypeVar("T")


class ToolInput(Contract):
    """Tool arguments: unknown keys are rejected, so a plan can't smuggle extra parameters."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ScopeError(Exception):
    """An argument names something outside the chat's case (or its time window)."""


@dataclass
class ToolOutput:
    rows: list[dict[str, Any]] = field(default_factory=list)
    refs: ToolRefs = field(default_factory=ToolRefs)
    computed: list[ComputedValue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    truncated: bool = False


ToolFn = Callable[[Any, CaseScope, NodeContext], ToolOutput]
CheckFn = Callable[[Any, CaseScope], None]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    input_model: type[ToolInput]
    fn: ToolFn
    check: CheckFn
    description: str


def no_check(args: Any, scope: CaseScope) -> None:
    return None


# --------------------------------------------------------------------------- scope checks


def require_txs(ids: Iterable[str], scope: CaseScope) -> None:
    outside = [i for i in ids if i not in scope.edges]
    if outside:
        raise ScopeError(
            f"out_of_case_scope: {len(outside)} transaction id(s) are not part of this case"
        )


def require_accounts(ids: Iterable[str], scope: CaseScope) -> None:
    outside = [i for i in ids if i not in scope.account_ids]
    if outside:
        raise ScopeError(
            f"out_of_case_scope: {len(outside)} account id(s) are not part of this case"
        )


def require_window(start: datetime | None, end: datetime | None, scope: CaseScope) -> None:
    for ts in (start, end):
        if ts is not None and not scope.in_window(ts):
            raise ScopeError("out_of_case_window: time window must lie inside the case window")
    if start is not None and end is not None and end < start:
        raise ScopeError("invalid_window: end is before start")


# --------------------------------------------------------------------------- helpers


def cap(items: list[T], limit: int | None = None) -> tuple[list[T], bool]:
    """The first `MAX_ROWS` items (read at call time, so tests can lower it) and whether any
    were cut."""
    limit = MAX_ROWS if limit is None else limit
    return items[:limit], len(items) > limit


def scoped_neighbourhood(scope: CaseScope) -> list[SubgraphEdge]:
    """Every scope edge inside the window, in time order. `scope.edges` already holds the evidence
    plus its bounded neighbourhood, so nothing is re-fetched or cut; walks bound hops themselves."""
    edges = [e for e in scope.edges.values() if scope.in_window(e.timestamp)]
    return sorted(edges, key=lambda e: (e.timestamp, e.transaction_id))


def sum_amounts(edges: list[SubgraphEdge]) -> tuple[Decimal, str] | None:
    """Total and currency when every edge has the same currency, else None."""
    currencies = {e.currency for e in edges}
    if len(currencies) != 1 or not edges:
        return None
    return sum((e.amount for e in edges), Decimal(0)), currencies.pop()


def amount_value(key: str, edges: list[SubgraphEdge]) -> ComputedValue | None:
    total = sum_amounts(edges)
    if total is None:
        return None
    return ComputedValue(key=key, kind="amount", value=money(total[0]), currency=total[1],
                         transaction_ids=[e.transaction_id for e in edges])


def iso(ts: datetime) -> str:
    return ts.isoformat().replace("+00:00", "Z")


def uniq(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))
