"""trace_backward, trace_forward, cycle_search and related_entities: bounded, time-respecting
walks over the case-scoped neighbourhood from `GraphService.subgraph`."""

from __future__ import annotations

from collections import defaultdict, deque

import networkx as nx
from pydantic import Field, model_validator

from tracelayer_agents.chat.session import CaseScope
from tracelayer_agents.schemas import SubgraphEdge, ToolRefs
from tracelayer_agents.tools.base import (
    MAX_HOPS,
    MAX_WALK_STATES,
    ToolInput,
    ToolOutput,
    amount_value,
    cap,
    iso,
    require_accounts,
    require_txs,
    scoped_neighbourhood,
    uniq,
)
from tracelayer_agents.wrappers import NodeContext


class TraceInput(ToolInput):
    """Start from one account or one transaction in the case."""

    account_id: str | None = None
    tx_id: str | None = None
    hops: int = Field(default=2, ge=1, le=MAX_HOPS)

    @model_validator(mode="after")
    def _one_start(self) -> TraceInput:
        if (self.account_id is None) == (self.tx_id is None):
            raise ValueError("give exactly one of account_id or tx_id")
        return self


def check_trace(args: TraceInput, scope: CaseScope) -> None:
    if args.account_id is not None:
        require_accounts([args.account_id], scope)
    if args.tx_id is not None:
        require_txs([args.tx_id], scope)


def _path_row(path: list[SubgraphEdge], forward: bool) -> dict:
    accounts = ([path[0].origin_account_id, *(e.destination_account_id for e in path)]
                if forward else
                [*(e.origin_account_id for e in path), path[-1].destination_account_id])
    return {"account_ids": accounts, "edge_ids": [e.transaction_id for e in path],
            "hops": len(path), "first_timestamp": iso(path[0].timestamp),
            "last_timestamp": iso(path[-1].timestamp)}


def _walk(edges: list[SubgraphEdge], args: TraceInput, scope: CaseScope,
          forward: bool) -> tuple[list[list[SubgraphEdge]], bool]:
    """Maximal time-respecting paths of up to `hops` edges (forward: each hop later; backward:
    each hop earlier), no account visited twice. Paths are in walk order (backward paths are
    returned oldest edge first). The flag is True when MAX_WALK_STATES cut the walk short."""
    out_of: dict[str, list[SubgraphEdge]] = defaultdict(list)
    for e in edges:
        out_of[e.origin_account_id if forward else e.destination_account_id].append(e)

    def nxt(e: SubgraphEdge) -> str:
        return e.destination_account_id if forward else e.origin_account_id

    if args.tx_id is not None:
        starts = [[scope.edges[args.tx_id]]]
    else:
        starts = [[e] for e in out_of.get(args.account_id or "", [])]
    paths: list[list[SubgraphEdge]] = []
    stack = list(reversed(starts))
    states = 0
    while stack and states < MAX_WALK_STATES:
        path = stack.pop()
        states += 1
        last = path[-1]
        seen = {a for e in path for a in (e.origin_account_id, e.destination_account_id)}
        ext = [
            e for e in out_of.get(nxt(last), [])
            if (e.timestamp > last.timestamp if forward else e.timestamp < last.timestamp)
            and nxt(e) not in seen
        ]
        if not ext or len(path) == args.hops:
            paths.append(path if forward else list(reversed(path)))
            continue
        stack.extend([*path, e] for e in reversed(ext))
    return paths, bool(stack)


def _trace(args: TraceInput, scope: CaseScope, ctx: NodeContext, forward: bool) -> ToolOutput:
    edges = scoped_neighbourhood(scope)
    walked, cut_short = _walk(edges, args, scope, forward)
    paths, truncated = cap(walked)
    truncated = truncated or cut_short  # an unexplored branch makes the trace incomplete
    rows, computed = [], []
    for n, path in enumerate(paths, 1):
        row = _path_row(path, forward)
        value = amount_value(f"path_{n}_total", path)
        if value is not None:
            row |= {"total_amount": value.value, "currency": value.currency}
            computed.append(value)
        rows.append(row)
    start_accounts = [args.account_id] if args.account_id else []
    return ToolOutput(
        rows=rows, computed=computed, truncated=truncated,
        refs=ToolRefs(
            tx_ids=uniq(i for r in rows for i in r["edge_ids"]),
            edge_ids=uniq(i for r in rows for i in r["edge_ids"]),
            account_ids=uniq([*start_accounts, *(a for r in rows for a in r["account_ids"])]),
            paths=[r["edge_ids"] for r in rows],
        ),
    )


def trace_forward(args: TraceInput, scope: CaseScope, ctx: NodeContext) -> ToolOutput:
    return _trace(args, scope, ctx, forward=True)


def trace_backward(args: TraceInput, scope: CaseScope, ctx: NodeContext) -> ToolOutput:
    return _trace(args, scope, ctx, forward=False)


# --------------------------------------------------------------------------- cycles


class CycleSearchInput(ToolInput):
    account_id: str | None = None
    max_len: int = Field(default=MAX_HOPS + 1, ge=2, le=MAX_HOPS + 1)


def check_cycles(args: CycleSearchInput, scope: CaseScope) -> None:
    if args.account_id is not None:
        require_accounts([args.account_id], scope)


def _time_respecting(graph: nx.MultiDiGraph, loop: list[str]) -> list[SubgraphEdge] | None:
    chosen: list[SubgraphEdge] = []
    for u, v in zip(loop, loop[1:] + loop[:1], strict=True):
        options = sorted((d["e"] for d in graph.get_edge_data(u, v, default={}).values()),
                         key=lambda e: (e.timestamp, e.transaction_id))
        after = chosen[-1].timestamp if chosen else None
        pick = next((e for e in options if after is None or e.timestamp > after), None)
        if pick is None:
            return None
        chosen.append(pick)
    return chosen


def cycle_search(args: CycleSearchInput, scope: CaseScope, ctx: NodeContext) -> ToolOutput:
    edges = scoped_neighbourhood(scope)
    graph = nx.MultiDiGraph()
    for e in edges:
        graph.add_edge(e.origin_account_id, e.destination_account_id, key=e.transaction_id, e=e)
    found: dict[tuple[str, ...], list[SubgraphEdge]] = {}
    for loop in nx.simple_cycles(nx.DiGraph(graph), length_bound=args.max_len):
        if args.account_id and args.account_id not in loop:
            continue
        for shift in range(len(loop)):
            walk = _time_respecting(graph, loop[shift:] + loop[:shift])
            if walk:
                found.setdefault(tuple(sorted(e.transaction_id for e in walk)), walk)
                break
    cycles, truncated = cap([found[k] for k in sorted(found)])
    rows, computed = [], []
    for n, walk in enumerate(cycles, 1):
        row = {"account_ids": [*(e.origin_account_id for e in walk), walk[0].origin_account_id],
               "edge_ids": [e.transaction_id for e in walk], "hops": len(walk)}
        value = amount_value(f"cycle_{n}_total", walk)
        if value is not None:
            row |= {"total_amount": value.value, "currency": value.currency}
            computed.append(value)
        rows.append(row)
    searched = [args.account_id] if args.account_id else sorted(scope.evidence_account_ids)
    return ToolOutput(
        rows=rows, computed=computed, truncated=truncated,
        refs=ToolRefs(
            tx_ids=uniq(i for r in rows for i in r["edge_ids"]),
            edge_ids=uniq(i for r in rows for i in r["edge_ids"]),
            account_ids=uniq([*searched, *(a for r in rows for a in r["account_ids"])]),
            paths=[r["edge_ids"] for r in rows],
        ),
    )


# --------------------------------------------------------------------------- related entities


class RelatedEntitiesInput(ToolInput):
    account_id: str
    hops: int = Field(default=1, ge=1, le=MAX_HOPS)


def check_related(args: RelatedEntitiesInput, scope: CaseScope) -> None:
    require_accounts([args.account_id], scope)


def related_entities(
    args: RelatedEntitiesInput, scope: CaseScope, ctx: NodeContext
) -> ToolOutput:
    """Accounts within `hops` of the start (ignoring direction), each with the edges of one
    shortest connecting path and the direct transactions it shares with the start."""
    edges = scoped_neighbourhood(scope)
    adjacent: dict[str, list[SubgraphEdge]] = defaultdict(list)
    for e in edges:
        adjacent[e.origin_account_id].append(e)
        adjacent[e.destination_account_id].append(e)
    via: dict[str, list[SubgraphEdge]] = {args.account_id: []}
    queue = deque([args.account_id])
    while queue:
        acc = queue.popleft()
        if len(via[acc]) == args.hops:
            continue
        for e in adjacent[acc]:
            other = e.destination_account_id if e.origin_account_id == acc else e.origin_account_id
            if other not in via:
                via[other] = [*via[acc], e]
                queue.append(other)

    rows, computed = [], []
    for acc in sorted(via, key=lambda a: (len(via[a]), a)):
        if acc == args.account_id:
            continue
        direct = [e for e in adjacent[acc]
                  if args.account_id in (e.origin_account_id, e.destination_account_id)]
        row = {"account_id": acc, "distance": len(via[acc]),
               "via_edge_ids": [e.transaction_id for e in via[acc]],
               "direct_transaction_count": len(direct)}
        value = amount_value(f"direct_{acc}_total", direct)
        if value is not None:
            row |= {"direct_total": value.value, "currency": value.currency}
            computed.append(value)
        rows.append(row)
    rows, truncated = cap(rows)
    kept = {r["account_id"] for r in rows}
    computed = [c for c in computed if c.key.removeprefix("direct_").removesuffix("_total") in kept]
    edge_ids = uniq(i for r in rows for i in r["via_edge_ids"])
    return ToolOutput(
        rows=rows, computed=computed, truncated=truncated,
        refs=ToolRefs(
            tx_ids=uniq([*edge_ids, *(i for c in computed for i in c.transaction_ids)]),
            edge_ids=edge_ids, account_ids=[args.account_id, *sorted(kept)],
            paths=[r["via_edge_ids"] for r in rows],
        ),
    )
