"""Bounded money-flow graph queries over canonical transactions.

Paths are investigative context: they show funds that *could* have moved along a chain given
timestamps and amounts. They do not prove tracing through commingled balances.
"""

from collections import defaultdict
from datetime import datetime, timedelta

from app.core.db import fetch_all, fetch_by_ids, get_db

TXN_FIELDS = "id, transaction_ref, occurred_at, amount, currency, amount_usd, channel, origin_account_id, destination_account_id"
ACCOUNT_FIELDS = "id, account_ref, institution_ref, is_internal, status"
MAX_EDGES = 5000
MAX_EXPANSIONS = 50_000


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _window(query, start: datetime | None, end: datetime | None):
    if start:
        query = query.gte("occurred_at", start.isoformat())
    if end:
        query = query.lte("occurred_at", end.isoformat())
    return query


def _edges_touching(tenant_id: str, account_ids: set[str], start: datetime | None, end: datetime | None) -> list[dict]:
    db = get_db()
    edges: dict[str, dict] = {}
    ids = sorted(account_ids)
    for i in range(0, len(ids), 100):
        batch = ",".join(ids[i : i + 100])
        rows = fetch_all(lambda b=batch: _window(
            db.table("transactions").select(TXN_FIELDS).eq("tenant_id", tenant_id)
            .or_(f"origin_account_id.in.({b}),destination_account_id.in.({b})"), start, end).order("occurred_at").order("id"))
        for row in rows:
            edges[row["id"]] = row
            if len(edges) >= MAX_EDGES:
                return list(edges.values())
    return list(edges.values())


def graph_payload(tenant_id: str, edges: list[dict], roles: dict[str, str] | None = None) -> dict:
    """Nodes (accounts) + edges (transactions) in a shape graph UIs consume directly."""
    account_ids = {e["origin_account_id"] for e in edges} | {e["destination_account_id"] for e in edges}
    accounts = {a["id"]: a for a in fetch_by_ids("accounts", tenant_id, account_ids, ACCOUNT_FIELDS)}
    degree: dict[str, int] = defaultdict(int)
    for e in edges:
        degree[e["origin_account_id"]] += 1
        degree[e["destination_account_id"]] += 1
    nodes = [
        {**accounts.get(a, {"id": a}), "degree": degree[a], "role": (roles or {}).get(a)}
        for a in sorted(account_ids)
    ]
    links = [
        {
            "id": e["id"], "source": e["origin_account_id"], "target": e["destination_account_id"],
            "transaction_ref": e["transaction_ref"], "occurred_at": e["occurred_at"], "amount": e["amount"],
            "currency": e["currency"], "amount_usd": e["amount_usd"], "channel": e["channel"],
        }
        for e in sorted(edges, key=lambda e: e["occurred_at"])
    ]
    return {"nodes": nodes, "edges": links, "truncated": len(edges) >= MAX_EDGES}


def neighborhood(tenant_id: str, account_id: str, hops: int, start: datetime | None, end: datetime | None) -> dict:
    frontier, seen, edges = {account_id}, {account_id}, {}
    for _ in range(hops):
        found = _edges_touching(tenant_id, frontier, start, end)
        nxt: set[str] = set()
        for e in found:
            edges[e["id"]] = e
            nxt.update((e["origin_account_id"], e["destination_account_id"]))
        frontier = nxt - seen
        seen |= nxt
        if not frontier or len(edges) >= MAX_EDGES:
            break
    return graph_payload(tenant_id, list(edges.values()), {account_id: "focus"})


def time_respecting_paths(
    edges: list[dict],
    source: str,
    target: str | None,
    max_hops: int,
    max_gap: timedelta,
    min_amount_ratio: float,
    limit: int,
) -> list[dict]:
    """Depth-first search where every hop occurs after the previous hop and within max_gap of it.

    min_amount_ratio bounds how much the amount may shrink hop-to-hop (0 disables the check).
    """
    outgoing: dict[str, list[dict]] = defaultdict(list)
    for e in edges:
        outgoing[e["origin_account_id"]].append({**e, "_t": _parse(e["occurred_at"]), "_amt": float(e["amount_usd"] or e["amount"])})
    for lst in outgoing.values():
        lst.sort(key=lambda e: e["_t"])

    results: list[list[dict]] = []
    budget = [MAX_EXPANSIONS]  # hard cap on work so an unreachable target cannot trigger exponential search

    def dfs(node: str, path: list[dict], visited: set[str]) -> None:
        if len(results) >= limit or budget[0] <= 0:
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
                "transaction_id": e["id"], "transaction_ref": e["transaction_ref"], "from": e["origin_account_id"],
                "to": e["destination_account_id"], "occurred_at": e["occurred_at"], "amount": e["amount"],
                "currency": e["currency"], "amount_usd": e["amount_usd"], "gap_hours": round(gap, 2) if gap is not None else None,
            })
        out.append({
            "hops": hops,
            "length": len(hops),
            "start_amount_usd": path[0]["_amt"],
            "end_amount_usd": path[-1]["_amt"],
            "elapsed_hours": round((path[-1]["_t"] - path[0]["_t"]).total_seconds() / 3600, 2),
        })
    return out


def paths(tenant_id: str, source: str, target: str | None, start: datetime | None, end: datetime | None,
          max_hops: int, max_gap_hours: float, min_amount_ratio: float, limit: int) -> dict:
    # Collect the reachable edge set hop by hop from the source, then search it in memory.
    frontier, seen, edges = {source}, {source}, {}
    for _ in range(max_hops):
        found = [e for e in _edges_touching(tenant_id, frontier, start, end) if e["origin_account_id"] in frontier]
        nxt = set()
        for e in found:
            edges[e["id"]] = e
            nxt.add(e["destination_account_id"])
        frontier = nxt - seen
        seen |= nxt
        if not frontier or len(edges) >= MAX_EDGES:
            break
    found_paths = time_respecting_paths(
        list(edges.values()), source, target, max_hops, timedelta(hours=max_gap_hours), min_amount_ratio, limit
    )
    return {
        "paths": found_paths,
        "parameters": {"max_hops": max_hops, "max_gap_hours": max_gap_hours, "min_amount_ratio": min_amount_ratio},
        "caveat": "Paths are investigative context based on timing and amounts; they do not establish that the same "
                  "funds moved through commingled balances.",
    }
