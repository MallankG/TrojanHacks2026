"""Deterministic NetworkX pattern finders shared by the mock detectors and flow investigator.

Graphs are `nx.MultiDiGraph`s: nodes are account IDs, each edge is one transaction keyed by its
`transaction_id` with the `EvidenceTransaction` under the `tx` attribute. Every finder is bounded
(window, depth, cycle length) and returns real transaction IDs, sorted for determinism.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import networkx as nx

from tracelayer_agents.schemas import EvidenceTransaction

FAN_WINDOW = timedelta(hours=24)
FAN_MIN_COUNTERPARTIES = 5
PASS_THROUGH_MAX_GAP = timedelta(minutes=60)
PASS_THROUGH_MIN_RATIO = Decimal("0.90")
CYCLE_MIN_LEN = 3
CYCLE_MAX_LEN = 6
CYCLE_MAX_SPAN = timedelta(days=7)
PATH_MAX_EDGES = 3
PATH_MAX_GAP = timedelta(hours=72)
PATH_LIMIT = 20


@dataclass(frozen=True)
class Pattern:
    account_ids: tuple[str, ...]
    transaction_ids: tuple[str, ...]


def transactions(graph: nx.MultiDiGraph) -> list[EvidenceTransaction]:
    """All transactions in the graph, in time order."""
    txs = [data["tx"] for _, _, data in graph.edges(data=True)]
    return sorted(txs, key=lambda t: (t.timestamp, t.transaction_id))


def _out_edges(graph: nx.MultiDiGraph, account: str) -> list[EvidenceTransaction]:
    txs = [data["tx"] for _, _, data in graph.out_edges(account, data=True)]
    return sorted(txs, key=lambda t: (t.timestamp, t.transaction_id))


def _in_edges(graph: nx.MultiDiGraph, account: str) -> list[EvidenceTransaction]:
    txs = [data["tx"] for _, _, data in graph.in_edges(account, data=True)]
    return sorted(txs, key=lambda t: (t.timestamp, t.transaction_id))


def _fan(graph: nx.MultiDiGraph, outgoing: bool) -> list[Pattern]:
    """One account sending to (or receiving from) many distinct counterparties within a window.

    For each hub, keeps the single window with the most distinct counterparties."""
    def party(t: EvidenceTransaction) -> str:
        return t.destination_account_id if outgoing else t.origin_account_id

    found = []
    for hub in sorted(graph.nodes):
        txs = _out_edges(graph, hub) if outgoing else _in_edges(graph, hub)
        best: list[EvidenceTransaction] = []
        for i, first in enumerate(txs):
            window = [t for t in txs[i:] if t.timestamp - first.timestamp <= FAN_WINDOW]
            if len({party(t) for t in window}) > len({party(t) for t in best}):
                best = window
        parties = sorted({party(t) for t in best})
        if len(parties) >= FAN_MIN_COUNTERPARTIES:
            accounts = (hub, *parties) if outgoing else (*parties, hub)
            found.append(Pattern(accounts, tuple(t.transaction_id for t in best)))
    return found


def fan_out(graph: nx.MultiDiGraph) -> list[Pattern]:
    return _fan(graph, outgoing=True)


def fan_in(graph: nx.MultiDiGraph) -> list[Pattern]:
    return _fan(graph, outgoing=False)


def pass_through(graph: nx.MultiDiGraph) -> list[Pattern]:
    """Funds in and back out of one account quickly, in a similar amount (same currency)."""
    found = []
    for mid in sorted(graph.nodes):
        outs = _out_edges(graph, mid)
        for tx_in in _in_edges(graph, mid):
            for tx_out in outs:
                gap = tx_out.timestamp - tx_in.timestamp
                if not timedelta(0) < gap <= PASS_THROUGH_MAX_GAP:
                    continue
                if tx_out.currency != tx_in.currency:
                    continue
                ratio = tx_out.amount / tx_in.amount
                if PASS_THROUGH_MIN_RATIO <= ratio <= 1:
                    found.append(Pattern(
                        (tx_in.origin_account_id, mid, tx_out.destination_account_id),
                        (tx_in.transaction_id, tx_out.transaction_id),
                    ))
    return found


def _time_respecting_walk(
    graph: nx.MultiDiGraph, accounts: list[str], closed: bool
) -> list[EvidenceTransaction] | None:
    """Earliest-first choice of one transaction per hop so each hop is later than the previous.

    Greedy is optimal here: taking the earliest feasible edge never rules out a later hop."""
    targets = accounts[1:] + accounts[:1] if closed else accounts[1:]
    hops = list(zip(accounts, targets, strict=False))
    chosen: list[EvidenceTransaction] = []
    for u, v in hops:
        candidates = sorted(
            (d["tx"] for d in graph.get_edge_data(u, v, default={}).values()),
            key=lambda t: (t.timestamp, t.transaction_id),
        )
        after = chosen[-1].timestamp if chosen else None
        nxt = next((t for t in candidates if after is None or t.timestamp > after), None)
        if nxt is None:
            return None
        chosen.append(nxt)
    return chosen


def cycles(graph: nx.MultiDiGraph) -> list[Pattern]:
    """Time-respecting cycles of 3–6 accounts that complete within `CYCLE_MAX_SPAN`.

    The pattern starts at the account that sends first."""
    simple = nx.DiGraph(graph)
    found: dict[tuple[str, ...], Pattern] = {}
    for loop in nx.simple_cycles(simple, length_bound=CYCLE_MAX_LEN):
        if len(loop) < CYCLE_MIN_LEN:
            continue
        for shift in range(len(loop)):
            accounts = loop[shift:] + loop[:shift]
            walk = _time_respecting_walk(graph, accounts, closed=True)
            if walk and walk[-1].timestamp - walk[0].timestamp <= CYCLE_MAX_SPAN:
                tx_ids = tuple(t.transaction_id for t in walk)
                found.setdefault(tuple(sorted(tx_ids)), Pattern((*accounts, accounts[0]), tx_ids))
    return [found[k] for k in sorted(found)]


def onward_paths(graph: nx.MultiDiGraph, seed_tx_ids: list[str]) -> list[Pattern]:
    """Time-respecting paths that start with a seed transaction and follow the money forward
    (each hop later than the previous, within `PATH_MAX_GAP`), up to `PATH_MAX_EDGES` edges.
    Only paths with at least two edges are returned, capped at `PATH_LIMIT`."""
    by_id = {t.transaction_id: t for t in transactions(graph)}
    paths: list[list[EvidenceTransaction]] = []

    def extend(path: list[EvidenceTransaction]) -> None:
        if len(paths) >= PATH_LIMIT:
            return
        last = path[-1]
        seen = {path[0].origin_account_id, *(t.destination_account_id for t in path)}
        nexts = [
            t for t in _out_edges(graph, last.destination_account_id)
            if timedelta(0) < t.timestamp - last.timestamp <= PATH_MAX_GAP
            and t.destination_account_id not in seen
        ]
        if not nexts or len(path) == PATH_MAX_EDGES:
            if len(path) >= 2:
                paths.append(path)
            return
        for t in nexts:
            extend([*path, t])

    for tx_id in sorted(seed_tx_ids):
        if tx_id in by_id:
            extend([by_id[tx_id]])
    return [
        Pattern(
            (p[0].origin_account_id, *(t.destination_account_id for t in p)),
            tuple(t.transaction_id for t in p),
        )
        for p in paths
    ]


def flags_by_tx(graph: nx.MultiDiGraph) -> dict[str, list[str]]:
    """Typology flags per transaction ID (`fan_out`, `fan_in`, `pass_through`, `cycle`)."""
    flags: dict[str, set[str]] = defaultdict(set)
    for name, finder in (
        ("fan_out", fan_out),
        ("fan_in", fan_in),
        ("pass_through", pass_through),
        ("cycle", cycles),
    ):
        for pattern in finder(graph):
            for tx_id in pattern.transaction_ids:
                flags[tx_id].add(name)
    return {tx_id: sorted(names) for tx_id, names in sorted(flags.items())}
