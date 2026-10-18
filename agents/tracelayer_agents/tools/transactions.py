"""transaction_lookup and time_window_aggregate."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Literal

from pydantic import Field

from tracelayer_agents.chat.session import SCOPE_HOPS, CaseScope
from tracelayer_agents.schemas import ComputedValue, ToolRefs, UtcDatetime
from tracelayer_agents.tools.base import (
    MAX_ROWS,
    ToolInput,
    ToolOutput,
    amount_value,
    cap,
    iso,
    require_accounts,
    require_txs,
    require_window,
    scoped_neighbourhood,
    uniq,
)
from tracelayer_agents.wrappers import NodeContext


class TransactionLookupInput(ToolInput):
    tx_ids: list[str] = Field(min_length=1, max_length=MAX_ROWS)


def check_lookup(args: TransactionLookupInput, scope: CaseScope) -> None:
    require_txs(args.tx_ids, scope)


def transaction_lookup(
    args: TransactionLookupInput, scope: CaseScope, ctx: NodeContext
) -> ToolOutput:
    txs = ctx.services.graph.get_transactions(uniq(args.tx_ids))
    txs = [t for t in txs if t.transaction_id in scope.edges]
    rows = [t.model_dump(mode="json") for t in txs]
    return ToolOutput(
        rows=rows,
        refs=ToolRefs(
            tx_ids=[t.transaction_id for t in txs],
            account_ids=uniq(a for t in txs for a in (t.origin_account_id,
                                                       t.destination_account_id)),
        ),
    )


class TimeWindowAggregateInput(ToolInput):
    start: UtcDatetime | None = None
    end: UtcDatetime | None = None
    account_ids: list[str] = Field(default_factory=list, max_length=MAX_ROWS)
    group_by: Literal["account", "currency"] = "account"


def check_aggregate(args: TimeWindowAggregateInput, scope: CaseScope) -> None:
    require_window(args.start, args.end, scope)
    require_accounts(args.account_ids, scope)


def time_window_aggregate(
    args: TimeWindowAggregateInput, scope: CaseScope, ctx: NodeContext
) -> ToolOutput:
    start: datetime = args.start or scope.window_start
    end: datetime = args.end or scope.window_end
    edges = [
        e for e in scoped_neighbourhood(scope, ctx, scope.evidence_tx_ids, SCOPE_HOPS)
        if start <= e.timestamp <= end
    ]
    focus = set(args.account_ids)
    if focus:
        edges = [e for e in edges if {e.origin_account_id, e.destination_account_id} & focus]

    groups: dict[tuple[str, ...], list] = defaultdict(list)
    for e in edges:
        if args.group_by == "currency":
            groups[(e.currency,)].append(e)
            continue
        for account, direction in ((e.destination_account_id, "inflow"),
                                   (e.origin_account_id, "outflow")):
            if not focus or account in focus:
                groups[(account, direction, e.currency)].append(e)

    rows, computed = [], []
    for n, (key, members) in enumerate(sorted(groups.items()), 1):
        value = amount_value(f"group_{n}_total", members)
        assert value is not None  # grouped by currency
        computed.append(value)
        row = {"total": value.value, "currency": value.currency, "count": len(members),
               "transaction_ids": [e.transaction_id for e in members][:MAX_ROWS]}
        if args.group_by == "account":
            row = {"account_id": key[0], "direction": key[1], **row}
        rows.append(row)
    rows, truncated = cap(rows)
    computed = computed[: len(rows)]
    computed += [
        ComputedValue(key="window_start", kind="timestamp", value=iso(start)),
        ComputedValue(key="window_end", kind="timestamp", value=iso(end)),
    ]
    tx_ids = uniq(i for r in rows for i in r["transaction_ids"])
    accounts = uniq(a for e in edges if e.transaction_id in tx_ids
                    for a in (e.origin_account_id, e.destination_account_id))
    return ToolOutput(rows=rows, computed=computed, truncated=truncated,
                      refs=ToolRefs(tx_ids=tx_ids, account_ids=accounts))
