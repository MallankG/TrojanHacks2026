"""Bounded cycle and motif detection over canonical transaction edges (PRD v3.3 §6).

Input edges: dicts with transaction_id, origin_account_id, destination_account_id, amount, occurred_at
(ISO string or datetime, UTC). Every item returned has the same shape so rules and alert fusion can
consume cycles and motifs alike:

    {kind, edge_ids, accounts, hops|branches, elapsed_hours, start_amount, end_amount, amount_delta,
     amount_delta_pct, total_amount, first_at, last_at, repetition_count}

Results are investigative leads. Cycle search is Johnson's algorithm on a capped subgraph with a hop
bound; it is NOT a complete enumeration of an unbounded graph, and a cycle does not prove laundering.
Pass `stats={}` to learn whether a cap or timeout truncated the search.
"""
import bisect
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import networkx as nx

from .paths import parse_ts

CYCLE_CAVEAT = ("Bounded search (hop limit, node cap, time window, timeout) over the supplied edges; not a complete "
                "enumeration. A cycle or motif is an investigative lead, not proof of laundering.")
MOTIF_KINDS = ("fan_in", "fan_out", "split_merge")
MAX_PAIRS_PER_INTERMEDIATE = 10_000  # ponytail: hub guard for split-merge; raise if hubs matter more than latency


def _prep(edges, start=None, end=None):
    start, end = (parse_ts(start) if start else None), (parse_ts(end) if end else None)
    out = []
    for e in edges:
        t = parse_ts(e["occurred_at"])
        if (start and t < start) or (end and t > end):
            continue
        out.append({"id": e["transaction_id"], "o": e["origin_account_id"], "d": e["destination_account_id"],
                    "t": t, "amt": float(e["amount_usd"] if e.get("amount_usd") is not None else e["amount"])})
    out.sort(key=lambda e: (e["t"], e["id"]))
    return out


def _item(kind, chain, accounts, size_key, size, repetition, start_amount=None, end_amount=None):
    first, last = chain[0]["t"], max(e["t"] for e in chain)
    sa = chain[0]["amt"] if start_amount is None else start_amount
    ea = chain[-1]["amt"] if end_amount is None else end_amount
    return {
        "kind": kind, "edge_ids": [e["id"] for e in chain], "accounts": accounts, size_key: size,
        "elapsed_hours": round((last - first).total_seconds() / 3600, 3),
        "start_amount": round(sa, 2), "end_amount": round(ea, 2), "amount_delta": round(ea - sa, 2),
        "amount_delta_pct": round(100 * (ea - sa) / sa, 2) if sa else None,
        "total_amount": round(sum(e["amt"] for e in chain), 2),
        "first_at": first.isoformat(), "last_at": last.isoformat(), "repetition_count": repetition,
    }


class _Clock:
    def __init__(self, timeout_s):
        self.deadline = time.monotonic() + timeout_s

    def expired(self):
        return time.monotonic() > self.deadline


def _cycle_instances(cycle, by_pair, max_gap):
    """Disjoint, time-respecting instances of an account cycle (each hop strictly later, within max_gap)."""
    hops = list(zip(cycle, cycle[1:] + cycle[:1]))
    first_edges = by_pair[hops[0]]
    found, after = [], None
    for e0 in first_edges:
        if after is not None and e0["t"] <= after:
            continue
        chain, t = [e0], e0["t"]
        for pair in hops[1:]:
            lst = by_pair[pair]
            i = bisect.bisect_right([x["t"] for x in lst], t)
            if i == len(lst) or (max_gap is not None and lst[i]["t"] - t > max_gap):
                chain = None
                break
            chain.append(lst[i])
            t = lst[i]["t"]
        if chain:
            found.append(chain)
            after = chain[-1]["t"]
    return found


def find_cycles(edges, *, min_hops=3, max_hops=10, start=None, end=None, max_gap_hours=None,
                max_nodes=2000, max_cycles=100, timeout_s=5.0, stats=None, focus_account=None):
    if not (2 <= min_hops <= max_hops <= 10):
        raise ValueError("cycle hops must satisfy 2 <= min_hops <= max_hops <= 10")
    stats = {} if stats is None else stats
    stats.update(truncated=False, truncation_reason=None, caveat=CYCLE_CAVEAT)
    clock = _Clock(timeout_s)
    E = _prep(edges, start, end)
    by_pair = defaultdict(list)
    for e in E:
        by_pair[(e["o"], e["d"])].append(e)
    G = nx.DiGraph(list(by_pair))
    keep = set().union(*[c for c in nx.strongly_connected_components(G) if len(c) > 1] or [set()])
    if len(keep) > max_nodes:  # cap: keep the busiest accounts, say so
        keep = set(sorted(keep, key=lambda n: -G.degree(n))[:max_nodes])
        stats.update(truncated=True, truncation_reason="max_nodes")
    G = G.subgraph(keep)
    max_gap = timedelta(hours=max_gap_hours) if max_gap_hours else None
    items = []
    for cyc in nx.simple_cycles(G, length_bound=max_hops):
        if clock.expired():
            stats.update(truncated=True, truncation_reason="timeout")
            break
        if len(cyc) < min_hops or (focus_account is not None and focus_account not in cyc):
            continue
        best = []
        for r in range(len(cyc)):  # money may start at any account on the cycle
            inst = _cycle_instances(cyc[r:] + cyc[:r], by_pair, max_gap)
            if len(inst) > len(best) or (inst and best and inst[0][0]["t"] < best[0][0]["t"] and len(inst) == len(best)):
                best, rot = inst, cyc[r:] + cyc[:r]
        if best:
            it = _item("cycle", best[0], rot, "hops", len(rot), len(best))
            it["account_cycle"] = rot + rot[:1]
            items.append(it)
            if len(items) >= max_cycles:
                stats.update(truncated=True, truncation_reason="max_cycles")
                break
    return sorted(items, key=lambda i: (-i["repetition_count"], i["first_at"]))


def _fan(E, center_key, other_key, kind, min_branches, window, clock, stats):
    by_center = defaultdict(list)
    for e in E:
        by_center[e[center_key]].append(e)
    items = []
    for c, lst in by_center.items():
        if clock.expired():
            stats.update(truncated=True, truncation_reason="timeout")
            break
        if len({e[other_key] for e in lst}) < min_branches:
            continue
        windows, j, cnt, best = [], 0, Counter(), (0, 0, 0)
        for i, e in enumerate(lst):  # two-pointer sliding window over time; distinct counterparties
            cnt[e[other_key]] += 1
            while e["t"] - lst[j]["t"] > window:
                cnt[lst[j][other_key]] -= 1
                if not cnt[lst[j][other_key]]:
                    del cnt[lst[j][other_key]]
                j += 1
            if len(cnt) >= min_branches:
                if not windows or j > windows[-1][1]:
                    windows.append((j, i))  # disjoint qualifying windows -> repetition_count
                if len(cnt) > best[0]:
                    best = (len(cnt), j, i)  # widest-distinct window -> the reported motif
        if windows:
            _, j, i = best
            chain = lst[j:i + 1]
            others = list(dict.fromkeys(e[other_key] for e in chain))
            accounts = others + [c] if kind == "fan_in" else [c] + others
            items.append(_item(kind, chain, accounts, "branches", len(others), len(windows)))
    return items


def _split_merge(E, min_branches, window, clock, stats):
    inn, out = defaultdict(list), defaultdict(list)
    for e in E:
        out[e["o"]].append(e)
        inn[e["d"]].append(e)
    legs = defaultdict(dict)  # (src, sink) -> {intermediate: (split_edge, merge_edge)} earliest per intermediate
    for m in set(inn) & set(out):
        if clock.expired():
            stats.update(truncated=True, truncation_reason="timeout")
            break
        if len(inn[m]) * len(out[m]) > MAX_PAIRS_PER_INTERMEDIATE:
            stats["skipped_hubs"] = stats.get("skipped_hubs", 0) + 1
            continue
        for a in inn[m]:
            for b in out[m]:
                if a["o"] in (m, b["d"]) or b["d"] == m or not (a["t"] < b["t"] <= a["t"] + window):
                    continue
                key = (a["o"], b["d"])
                if m not in legs[key] or a["t"] < legs[key][m][0]["t"]:
                    legs[key][m] = (a, b)
    items = []
    for (s, t), by_m in legs.items():
        if len(by_m) < min_branches:
            continue
        pairs = sorted(by_m.items(), key=lambda kv: kv[1][0]["t"])
        best, j = (0, 0), 0
        for i in range(len(pairs)):  # splits within `window` of each other
            while pairs[i][1][0]["t"] - pairs[j][1][0]["t"] > window:
                j += 1
            if i - j + 1 > best[1] - best[0]:
                best = (j, i + 1)
        sel = pairs[best[0]:best[1]]
        if len(sel) < min_branches:
            continue
        splits, merges = [p[1][0] for p in sel], [p[1][1] for p in sel]
        chain = sorted(splits + merges, key=lambda e: e["t"])
        items.append(_item("split_merge", chain, [s] + [p[0] for p in sel] + [t], "branches", len(sel), 1,
                           sum(e["amt"] for e in splits), sum(e["amt"] for e in merges)))
    return items


def find_motifs(kind, edges, *, min_branches=3, window_hours=72, start=None, end=None, max_results=100,
                timeout_s=5.0, stats=None):
    if kind not in MOTIF_KINDS:
        raise ValueError(f"kind must be one of {MOTIF_KINDS}")
    if min_branches < 2:
        raise ValueError("min_branches must be >= 2")
    stats = {} if stats is None else stats
    stats.update(truncated=False, truncation_reason=None, caveat=CYCLE_CAVEAT)
    clock, window = _Clock(timeout_s), timedelta(hours=window_hours)
    E = _prep(edges, start, end)
    if kind == "fan_in":
        items = _fan(E, "d", "o", kind, min_branches, window, clock, stats)
    elif kind == "fan_out":
        items = _fan(E, "o", "d", kind, min_branches, window, clock, stats)
    else:
        items = _split_merge(E, min_branches, window, clock, stats)
    items.sort(key=lambda i: (-i["branches"], -i["total_amount"]))
    if len(items) > max_results:
        stats.update(truncated=True, truncation_reason="max_results")
    return items[:max_results]
