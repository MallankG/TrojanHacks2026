"""Time-respecting path search over in-memory transaction edges (no DB).

Moved here from app/services/graph.py so the graph engine and the API share one implementation;
app/services/graph.py re-exports it. Paths are investigative context: they show funds that *could*
have moved along a chain given timestamps and amounts, not tracing through commingled balances.
"""
import time
from collections import defaultdict
from datetime import datetime, timedelta

MAX_EXPANSIONS = 50_000


def parse_ts(ts) -> datetime:
    return ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def time_respecting_paths(
    edges: list[dict],
    source: str,
    target: str | None,
    max_hops: int,
    max_gap: timedelta,
    min_amount_ratio: float,
    limit: int,
    max_expansions: int | None = None,
    stats: dict | None = None,
    timeout_s: float | None = None,
) -> list[dict]:
    """Depth-first search where every hop occurs after the previous hop and within max_gap of it.

    Edges use the API shape: id, transaction_ref, origin_account_id, destination_account_id, occurred_at,
    amount, amount_usd, currency. min_amount_ratio bounds how much the amount may shrink hop-to-hop
    (0 disables). max_expansions caps DFS work so an unreachable target cannot trigger exponential search.
    """
    stats = {} if stats is None else stats
    stats.update(truncated=False, truncation_reason=None)
    deadline = time.monotonic() + timeout_s if timeout_s is not None else None
    outgoing: dict[str, list[dict]] = defaultdict(list)
    for e in edges:
        outgoing[e["origin_account_id"]].append({**e, "_t": parse_ts(e["occurred_at"]), "_amt": float(e["amount_usd"] if e.get("amount_usd") is not None else e["amount"])})
    for lst in outgoing.values():
        lst.sort(key=lambda e: e["_t"])

    results: list[list[dict]] = []
    budget = [MAX_EXPANSIONS if max_expansions is None else max_expansions]

    def dfs(node: str, path: list[dict], visited: set[str]) -> None:
        if len(results) >= limit:
            return
        if budget[0] <= 0 or (deadline is not None and time.monotonic() > deadline):
            stats.update(truncated=True, truncation_reason="max_expansions" if budget[0] <= 0 else "timeout")
            return
        budget[0] -= 1
        if path and (target is None or node == target) and (target is not None or len(path) >= 2):
            results.append(list(path))
            if target is not None:
                return
        if len(path) >= max_hops:
            return
        last = path[-1] if path else None
        for e in outgoing.get(node, []):
            if len(results) >= limit or stats["truncated"]:
                break
            if last is not None:
                if e["_t"] <= last["_t"] or e["_t"] - last["_t"] > max_gap:
                    continue
                if min_amount_ratio and e["_amt"] < last["_amt"] * min_amount_ratio:
                    continue
            nxt = e["destination_account_id"]
            if nxt in visited:
                continue
            path.append(e)
            dfs(nxt, path, visited | {nxt})
            path.pop()

    dfs(source, [], {source})

    out = []
    for path in results:
        hops = []
        for i, e in enumerate(path):
            gap = (e["_t"] - path[i - 1]["_t"]).total_seconds() / 3600 if i else None
            hops.append({
                "transaction_id": e["id"], "transaction_ref": e.get("transaction_ref"), "from": e["origin_account_id"],
                "to": e["destination_account_id"], "occurred_at": e["occurred_at"], "amount": e["amount"],
                "currency": e.get("currency"), "amount_usd": e.get("amount_usd"), "gap_hours": round(gap, 2) if gap is not None else None,
            })
        out.append({
            "hops": hops,
            "length": len(hops),
            "start_amount_usd": path[0]["_amt"],
            "end_amount_usd": path[-1]["_amt"],
            "elapsed_hours": round((path[-1]["_t"] - path[0]["_t"]).total_seconds() / 3600, 2),
        })
    return out
