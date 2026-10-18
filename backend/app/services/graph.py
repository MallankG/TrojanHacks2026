"""Bounded money-flow graph queries over canonical transactions.

Graph analytics only (no graph ML). Every query is bounded: hop depth, time window, node and edge
caps, a DFS expansion budget and a wall-clock deadline. Results say when a bound was hit.

Paths, cycles and motifs are investigative context: they show funds that *could* have moved along a
chain given timestamps and amounts. They do not prove tracing through commingled balances.
"""

import time
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from app.core.db import PAGE_SIZE, fetch_by_ids, get_db

TXN_FIELDS = (
    "id, transaction_ref, occurred_at, amount, currency, amount_usd, channel, origin_account_id, "
    "destination_account_id, source_row_id, ingestion_run_id, transaction_status"
)
ACCOUNT_FIELDS = "id, account_ref, institution_ref, is_internal, status"
MAX_EDGES = 5000
MAX_NODES = 300  # neighborhood expansion stops once this many accounts are reached
MAX_PATTERN_EDGES = 3000  # subgraph cap for pattern search
MAX_EXPANSIONS = 50_000
QUERY_TIMEOUT_S = 10.0
MAX_WINDOW = timedelta(days=366)
MAX_PATTERN_WINDOW = timedelta(days=31)
DEFAULT_PATTERN_WINDOW = timedelta(days=7)

Direction = Literal["outgoing", "incoming", "both"]

CAVEAT = (
    "Paths are investigative context based on timing and amounts; they do not establish that the same "
    "funds moved through commingled balances."
)
LIMITATION = (
    "Bounded search, not complete enumeration: results are limited by hop depth, time window, the subgraph "
    "edge cap, an expansion budget and a time limit. An empty result does not mean no such pattern exists."
)


class GraphLimitError(ValueError):
    """A request exceeded a server-side cap; the route turns this into a 422."""


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _dec(value) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal(0)


def _usd(e: dict) -> Decimal:
    return _dec(e.get("amount_usd") if e.get("amount_usd") is not None else e.get("amount"))


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def check_window(start: datetime | None, end: datetime | None, cap: timedelta = MAX_WINDOW) -> None:
    if start and end:
        if end < start:
            raise GraphLimitError("end must be after start")
        if end - start > cap:
            raise GraphLimitError(f"Time window is longer than the {cap.days}-day limit; narrow start and end")


def _window(query, start: datetime | None, end: datetime | None):
    if start:
        query = query.gte("occurred_at", start.isoformat())
    if end:
        query = query.lte("occurred_at", end.isoformat())
    return query


def _filters(query, channels: Iterable[str] | None, min_amount: Decimal | None):
    if channels:
        query = query.in_("channel", sorted(set(channels)))
    if min_amount:
        # USD-equivalent amount; rows without a USD conversion cannot meet a minimum and are left out.
        query = query.gte("amount_usd", str(min_amount))
    return query


def _edges_touching(
    tenant_id: str,
    account_ids: set[str],
    start: datetime | None,
    end: datetime | None,
    side: Direction = "both",
    channels: Iterable[str] | None = None,
    min_amount: Decimal | None = None,
    cap: int = MAX_EDGES,
    deadline: float | None = None,
) -> list[dict]:
    """Transactions with an endpoint in account_ids. side="outgoing" means the account sent them."""
    if cap <= 0:
        return []
    db = get_db()
    edges: dict[str, dict] = {}
    ids = sorted(account_ids)
    for i in range(0, len(ids), 100):
        if deadline is not None and time.monotonic() > deadline:
            break
        batch = ids[i : i + 100]
        joined = ",".join(batch)

        def build(b=batch, j=joined):
            q = db.table("transactions").select(TXN_FIELDS).eq("tenant_id", tenant_id)
            if side == "outgoing":
                q = q.in_("origin_account_id", b)
            elif side == "incoming":
                q = q.in_("destination_account_id", b)
            else:
                q = q.or_(f"origin_account_id.in.({j}),destination_account_id.in.({j})")
            return _filters(_window(q, start, end), channels, min_amount).order("occurred_at").order("id")

        offset = 0
        while len(edges) < cap:
            if deadline is not None and time.monotonic() > deadline:
                return list(edges.values())
            size = min(PAGE_SIZE, cap - len(edges))
            rows = build().range(offset, offset + size - 1).execute().data or []
            for row in rows:
                edges[row["id"]] = row
            if len(rows) < size:
                break
            offset += size
        if len(edges) >= cap:
            return list(edges.values())
    return list(edges.values())


def graph_payload(tenant_id: str, edges: list[dict], roles: dict[str, str] | None = None,
                  layers: dict[str, int] | None = None) -> dict:
    """Nodes (accounts) + edges (transactions) in a shape graph UIs consume directly."""
    account_ids = {e["origin_account_id"] for e in edges} | {e["destination_account_id"] for e in edges} | set(roles or {})
    accounts = {a["id"]: a for a in fetch_by_ids("accounts", tenant_id, account_ids, ACCOUNT_FIELDS)}
    in_deg: dict[str, int] = defaultdict(int)
    out_deg: dict[str, int] = defaultdict(int)
    for e in edges:
        out_deg[e["origin_account_id"]] += 1
        in_deg[e["destination_account_id"]] += 1
    nodes = [
        {
            **accounts.get(a, {"id": a}), "degree": in_deg[a] + out_deg[a], "in_degree": in_deg[a],
            "out_degree": out_deg[a], "role": (roles or {}).get(a),
            **({"layer": layers.get(a)} if layers is not None else {}),
        }
        for a in sorted(account_ids)
    ]
    links = [
        {
            "id": e["id"], "source": e["origin_account_id"], "target": e["destination_account_id"],
            "transaction_ref": e.get("transaction_ref"), "occurred_at": e["occurred_at"], "amount": e["amount"],
            "currency": e["currency"], "amount_usd": e.get("amount_usd"), "channel": e["channel"],
            "source_row_id": e.get("source_row_id"),
            "ingestion_run_id": e.get("ingestion_run_id"), "transaction_status": e.get("transaction_status"),
        }
        for e in sorted(edges, key=lambda e: (e["occurred_at"], e["id"]))
    ]
    return {"nodes": nodes, "edges": links, "truncated": len(edges) >= MAX_EDGES}


# --- Account search --------------------------------------------------------------------------------


def search_accounts(tenant_id: str, q: str | None, limit: int) -> dict:
    """Starting points for the workbench, ranked by transaction degree, plus a suggested start."""
    term = (q or "").replace("%", "").replace("_", "").strip() or None
    rows = get_db().rpc("graph_account_search", {"p_tenant_id": tenant_id, "p_query": term, "p_limit": limit}).execute().data or []
    return {"items": rows, "suggested": None if term else _suggested_start(tenant_id, rows)}


def _suggested_start(tenant_id: str, ranked: list[dict]) -> dict | None:
    """The subject of the top-ranked alert in the latest ranker run, else the most connected account."""
    db = get_db()
    run = (
        db.table("model_runs").select("id").eq("tenant_id", tenant_id).eq("model_kind", "ranker").eq("status", "completed")
        .order("completed_at", desc=True).limit(1).execute().data
    )
    if run:
        top = (
            db.table("alerts").select("id, alert_number").eq("tenant_id", tenant_id).eq("model_run_id", run[0]["id"])
            .order("priority_rank", nullsfirst=False).order("created_at").limit(1).execute().data
        )
        if top:
            links = db.table("alert_accounts").select("account_id, role").eq("tenant_id", tenant_id).eq("alert_id", top[0]["id"]).execute().data or []
            order = {"subject": 0, "intermediary": 1, "counterparty": 2}
            links.sort(key=lambda r: (order.get(r["role"], 3), r["account_id"]))
            if links:
                return {"account_id": links[0]["account_id"], "reason": "top_alert", "alert_id": top[0]["id"],
                        "alert_number": top[0]["alert_number"]}
    if ranked and ranked[0].get("degree"):
        return {"account_id": ranked[0]["id"], "reason": "most_connected", "alert_id": None, "alert_number": None}
    return None


# --- Neighborhood ----------------------------------------------------------------------------------


def neighborhood(
    tenant_id: str,
    account_id: str,
    hops: int,
    start: datetime | None,
    end: datetime | None,
    direction: Direction = "both",
    channels: Iterable[str] | None = None,
    min_amount: Decimal | None = None,
    max_nodes: int = MAX_NODES,
) -> dict:
    """Breadth-first expansion. Each node's layer is its signed hop distance: negative = sends to the focus side."""
    deadline = time.monotonic() + QUERY_TIMEOUT_S
    layers: dict[str, int] = {account_id: 0}
    frontier, edges = {account_id}, {}
    stopped: str | None = None
    for depth in range(1, hops + 1):
        found = _edges_touching(tenant_id, frontier, start, end, direction, channels, min_amount,
                                cap=MAX_EDGES - len(edges), deadline=deadline)
        nxt: set[str] = set()
        for e in found:
            src, dst = e["origin_account_id"], e["destination_account_id"]
            # Keep only edges that move along the requested direction from the frontier.
            forward = src in frontier and direction != "incoming"
            backward = dst in frontier and direction != "outgoing"
            if not (forward or backward):
                continue
            edges[e["id"]] = e
            for node, sign, ok in ((dst, 1, forward), (src, -1, backward)):
                if ok and node not in layers:
                    if len(layers) >= max_nodes:
                        stopped = "max_nodes"
                        continue
                    layers[node] = sign * depth if direction == "both" else depth
                    nxt.add(node)
        frontier = nxt
        if time.monotonic() > deadline:
            stopped = "timeout"
        if len(edges) >= MAX_EDGES:
            stopped = "max_edges"
        if not frontier or stopped:
            break
    # Drop edges to accounts left out by the node cap so the graph stays consistent.
    kept = [e for e in edges.values() if e["origin_account_id"] in layers and e["destination_account_id"] in layers]
    payload = graph_payload(tenant_id, kept, {account_id: "focus"}, layers)
    payload["truncated"] = payload["truncated"] or stopped is not None
    payload["limits"] = {"hops": hops, "direction": direction, "max_nodes": max_nodes, "max_edges": MAX_EDGES,
                         "timeout_s": QUERY_TIMEOUT_S, "stopped_by": stopped}
    return payload


# --- Time-respecting paths -------------------------------------------------------------------------


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
        outgoing[e["origin_account_id"]].append({**e, "_t": _parse(e["occurred_at"]), "_amt": float(_usd(e))})
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
                "currency": e["currency"], "amount_usd": e["amount_usd"], "channel": e.get("channel"),
                "gap_hours": round(gap, 2) if gap is not None else None,
            })
        start_amt, end_amt = _usd(path[0]), _usd(path[-1])
        out.append({
            "hops": hops,
            "length": len(hops),
            "start_amount_usd": path[0]["_amt"],
            "end_amount_usd": path[-1]["_amt"],
            "amount_delta_usd": _money(start_amt - end_amt),
            "max_gap_hours": max((h["gap_hours"] for h in hops if h["gap_hours"] is not None), default=None),
            "elapsed_hours": round((path[-1]["_t"] - path[0]["_t"]).total_seconds() / 3600, 2),
        })
    return out


def paths(tenant_id: str, source: str, target: str | None, start: datetime | None, end: datetime | None,
          max_hops: int, max_gap_hours: float, min_amount_ratio: float, limit: int,
          channels: Iterable[str] | None = None, min_amount: Decimal | None = None) -> dict:
    # Collect the reachable edge set hop by hop from the source, then search it in memory.
    deadline = time.monotonic() + QUERY_TIMEOUT_S
    frontier, seen, edges = {source}, {source}, {}
    for _ in range(max_hops):
        found = _edges_touching(tenant_id, frontier, start, end, "outgoing", channels, min_amount,
                                cap=MAX_EDGES - len(edges), deadline=deadline)
        nxt = set()
        for e in found:
            edges[e["id"]] = e
            nxt.add(e["destination_account_id"])
        frontier = nxt - seen
        seen |= nxt
        if not frontier or len(edges) >= MAX_EDGES or time.monotonic() > deadline:
            break
    found_paths = time_respecting_paths(
        list(edges.values()), source, target, max_hops, timedelta(hours=max_gap_hours), min_amount_ratio, limit
    )
    involved = {h["transaction_id"] for p in found_paths for h in p["hops"]}
    return {
        "paths": found_paths,
        "graph": graph_payload(tenant_id, [e for e in edges.values() if e["id"] in involved],
                               {source: "source", **({target: "target"} if target else {})}),
        "parameters": {"max_hops": max_hops, "max_gap_hours": max_gap_hours, "min_amount_ratio": min_amount_ratio,
                       "channels": sorted(channels) if channels else None,
                       "min_amount": str(min_amount) if min_amount else None},
        "searched_edges": len(edges),
        "truncated": len(edges) >= MAX_EDGES,
        "caveat": CAVEAT,
    }


# --- Pattern search --------------------------------------------------------------------------------


def _prepared(edges: list[dict]) -> list[dict]:
    return sorted(({**e, "_t": _parse(e["occurred_at"]), "_amt": _usd(e)} for e in edges), key=lambda e: (e["_t"], e["id"]))


def _hop(e: dict, gap: float | None = None) -> dict:
    return {
        "transaction_id": e["id"], "transaction_ref": e.get("transaction_ref"), "from": e["origin_account_id"],
        "to": e["destination_account_id"], "occurred_at": e["occurred_at"], "amount": e["amount"],
        "currency": e["currency"], "amount_usd": e.get("amount_usd"), "channel": e.get("channel"),
        "gap_hours": round(gap, 2) if gap is not None else None,
    }


def _hours(a: datetime, b: datetime) -> float:
    return round((b - a).total_seconds() / 3600, 2)


def find_cycles(
    edges: list[dict],
    anchor: str,
    min_hops: int,
    max_hops: int,
    time_respecting: bool,
    max_gap: timedelta,
    limit: int,
    budget: int = MAX_EXPANSIONS,
    deadline: float | None = None,
) -> tuple[list[dict], dict]:
    """Simple cycles that leave the anchor and return to it, found by bounded DFS.

    Time-respecting: every hop happens after the previous one and within max_gap of it.
    Returns (findings, stats) where stats says whether the budget or deadline cut the search short.
    """
    outgoing: dict[str, list[dict]] = defaultdict(list)
    for e in _prepared(edges):
        if e["origin_account_id"] != e["destination_account_id"]:
            outgoing[e["origin_account_id"]].append(e)
    results: list[list[dict]] = []
    state = {"budget": budget, "budget_exhausted": False, "timed_out": False}

    def dfs(node: str, path: list[dict], visited: set[str]) -> None:
        if len(results) >= limit:
            return
        if state["budget"] <= 0:
            state["budget_exhausted"] = True
            return
        if deadline is not None and time.monotonic() > deadline:
            state["timed_out"] = True
            return
        state["budget"] -= 1
        last = path[-1] if path else None
        for e in outgoing.get(node, []):
            if last is not None and time_respecting and (e["_t"] <= last["_t"] or e["_t"] - last["_t"] > max_gap):
                continue
            nxt = e["destination_account_id"]
            if nxt == anchor:
                if len(path) + 1 >= min_hops:
                    results.append([*path, e])
                    if len(results) >= limit:
                        return
                continue
            if nxt in visited or len(path) + 1 >= max_hops:
                continue
            path.append(e)
            visited.add(nxt)
            dfs(nxt, path, visited)
            visited.discard(nxt)
            path.pop()

    dfs(anchor, [], {anchor})

    findings = []
    for cyc in results:
        hops = [_hop(e, (e["_t"] - cyc[i - 1]["_t"]).total_seconds() / 3600 if i else None) for i, e in enumerate(cyc)]
        sent, returned = cyc[0]["_amt"], cyc[-1]["_amt"]
        findings.append({
            "kind": "cycle",
            "anchor_account_id": anchor,
            "account_ids": [cyc[0]["origin_account_id"], *(e["destination_account_id"] for e in cyc)],
            "transaction_ids": [e["id"] for e in cyc],
            "hops": hops,
            "hop_count": len(cyc),
            "elapsed_hours": _hours(min(e["_t"] for e in cyc), max(e["_t"] for e in cyc)),
            "time_respecting": time_respecting,
            "amount_out_usd": _money(sent),
            "amount_back_usd": _money(returned),
            "amount_delta_usd": _money(sent - returned),
            "summary": f"{len(cyc)}-hop round trip: {_money(sent)} sent, {_money(returned)} returned to the start account.",
        })
    return findings, {"budget_exhausted": state["budget_exhausted"], "timed_out": state["timed_out"],
                      "expansions": budget - state["budget"]}


def find_fan(edges: list[dict], anchor: str, kind: Literal["fan_in", "fan_out"], min_counterparties: int,
             span: timedelta, limit: int) -> list[dict]:
    """Many distinct counterparties paying into (fan-in) or out of (fan-out) the anchor within `span`.

    Sliding window over the anchor's edges; non-overlapping windows with the most counterparties win.
    """
    mine = [e for e in _prepared(edges)
            if (e["destination_account_id"] if kind == "fan_in" else e["origin_account_id"]) == anchor
            and e["origin_account_id"] != e["destination_account_id"]]
    other = "origin_account_id" if kind == "fan_in" else "destination_account_id"
    candidates = []
    j = 0
    for i, first in enumerate(mine):
        j = max(j, i)
        while j + 1 < len(mine) and mine[j + 1]["_t"] - first["_t"] <= span:
            j += 1
        window = mine[i : j + 1]
        parties = {e[other] for e in window}
        if len(parties) >= min_counterparties:
            candidates.append((len(parties), sum((e["_amt"] for e in window), Decimal(0)), i, j))
    candidates.sort(key=lambda c: (-c[0], -c[1], c[2]))
    used: set[int] = set()
    findings = []
    for parties_n, total, i, j in candidates:
        if len(findings) >= limit:
            break
        if used & set(range(i, j + 1)):
            continue
        used |= set(range(i, j + 1))
        window = mine[i : j + 1]
        verb = "into" if kind == "fan_in" else "out of"
        findings.append({
            "kind": kind,
            "anchor_account_id": anchor,
            "account_ids": [anchor, *sorted({e[other] for e in window})],
            "transaction_ids": [e["id"] for e in window],
            "hops": [_hop(e) for e in window],
            "counterparty_count": parties_n,
            "transaction_count": len(window),
            "elapsed_hours": _hours(window[0]["_t"], window[-1]["_t"]),
            "total_amount_usd": _money(total),
            "summary": f"{parties_n} counterparties, {len(window)} transfers {verb} the account within "
                       f"{_hours(window[0]['_t'], window[-1]['_t'])} h, {_money(total)} in total.",
        })
    return findings


def find_pass_through(edges: list[dict], anchor: str, max_gap: timedelta, min_ratio: float, limit: int) -> list[dict]:
    """Money in, then most of it out again soon after: each inbound transfer is matched with the
    outbound transfers that follow it within max_gap until they reach min_ratio of the amount."""
    prepared = _prepared(edges)
    inbound = [e for e in prepared if e["destination_account_id"] == anchor and e["origin_account_id"] != anchor]
    outbound = [e for e in prepared if e["origin_account_id"] == anchor and e["destination_account_id"] != anchor]
    used_out: set[str] = set()
    findings = []
    for inc in inbound:
        if len(findings) >= limit:
            break
        outs, total = [], Decimal(0)
        for out in outbound:
            if out["id"] in used_out or out["_t"] <= inc["_t"]:
                continue
            if out["_t"] - inc["_t"] > max_gap:
                break
            outs.append(out)
            total += out["_amt"]
            if total >= inc["_amt"] * Decimal(str(min_ratio)):
                break
        if not outs or total < inc["_amt"] * Decimal(str(min_ratio)):
            continue
        used_out.update(o["id"] for o in outs)
        ratio = float(total / inc["_amt"]) if inc["_amt"] else 0.0
        findings.append({
            "kind": "pass_through",
            "anchor_account_id": anchor,
            "account_ids": [inc["origin_account_id"], anchor, *dict.fromkeys(o["destination_account_id"] for o in outs)],
            "transaction_ids": [inc["id"], *(o["id"] for o in outs)],
            "hops": [_hop(inc), *(_hop(o, (o["_t"] - inc["_t"]).total_seconds() / 3600) for o in outs)],
            "elapsed_hours": _hours(inc["_t"], outs[-1]["_t"]),
            "amount_in_usd": _money(inc["_amt"]),
            "amount_out_usd": _money(total),
            "amount_delta_usd": _money(inc["_amt"] - total),
            "pass_through_ratio": round(ratio, 4),
            "summary": f"{_money(inc['_amt'])} in, {_money(total)} out within {_hours(inc['_t'], outs[-1]['_t'])} h "
                       f"({round(ratio * 100, 1)}% passed on).",
        })
    return findings


def _default_window(tenant_id: str, account_id: str) -> tuple[datetime, datetime] | None:
    """Seven days either side of the account's latest transaction, so a search without a window stays
    bounded yet still sees a cycle's return leg that lands after the account last sent money."""
    db = get_db()
    latest = (
        db.table("transactions").select("occurred_at").eq("tenant_id", tenant_id)
        .or_(f"origin_account_id.eq.{account_id},destination_account_id.eq.{account_id}")
        .order("occurred_at", desc=True).limit(1).execute().data
    )
    if not latest:
        return None
    last = _parse(latest[0]["occurred_at"])
    return last - DEFAULT_PATTERN_WINDOW, last + DEFAULT_PATTERN_WINDOW


def pattern_search(tenant_id: str, params: dict) -> dict:
    """Run one typed pattern operation around an account inside a bounded subgraph."""
    began = time.monotonic()
    deadline = began + QUERY_TIMEOUT_S
    op, anchor = params["operation"], params["account_id"]
    start, end = params.get("start"), params.get("end")
    if not (start and end):
        window = _default_window(tenant_id, anchor)
        if window is None:
            return _pattern_result(op, params, [], None, None, {"edges": 0, "accounts": 1}, began, {})
        start, end = start or window[0], end or window[1]
    check_window(start, end, MAX_PATTERN_WINDOW)
    channels, min_amount = params.get("channels"), params.get("min_amount")

    stats: dict = {}
    if op == "cycles":
        # Outgoing expansion to depth max_hops - 1 already contains every edge a cycle can use back to the anchor.
        frontier, seen, edges = {anchor}, {anchor}, {}
        for _ in range(params["max_hops"]):
            found = _edges_touching(tenant_id, frontier, start, end, "outgoing", channels, min_amount,
                                    cap=MAX_PATTERN_EDGES - len(edges), deadline=deadline)
            nxt = set()
            for e in found:
                edges[e["id"]] = e
                nxt.add(e["destination_account_id"])
            frontier = nxt - seen
            seen |= nxt
            if not frontier or len(edges) >= MAX_PATTERN_EDGES or time.monotonic() > deadline:
                break
        edge_list = list(edges.values())
        findings, stats = find_cycles(
            edge_list, anchor, params["min_hops"], params["max_hops"], params["time_respecting"],
            timedelta(hours=params["max_gap_hours"]), params["limit"], deadline=deadline,
        )
    else:
        side: Direction = {"fan_in": "incoming", "fan_out": "outgoing", "pass_through": "both"}[op]
        edge_list = _edges_touching(tenant_id, {anchor}, start, end, side, channels, min_amount,
                                    cap=MAX_PATTERN_EDGES, deadline=deadline)
        if op == "pass_through":
            findings = find_pass_through(edge_list, anchor, timedelta(hours=params["max_gap_hours"]),
                                         params["min_pass_through_ratio"], params["limit"])
        else:
            findings = find_fan(edge_list, anchor, op, params["min_counterparties"],
                                timedelta(hours=params["max_span_hours"]), params["limit"])
    accounts = {e["origin_account_id"] for e in edge_list} | {e["destination_account_id"] for e in edge_list}
    searched = {"edges": len(edge_list), "accounts": len(accounts) or 1}
    stats["truncated"] = len(edge_list) >= MAX_PATTERN_EDGES
    stats["timed_out"] = stats.get("timed_out", False) or time.monotonic() > deadline
    return _pattern_result(op, params, findings, start, end, searched, began, stats,
                           graph_payload(tenant_id, [e for e in edge_list if e["id"] in {t for f in findings for t in f["transaction_ids"]}],
                                         {anchor: "focus"}) if findings else None)


def _pattern_result(op, params, findings, start, end, searched, began, stats, graph=None) -> dict:
    return {
        "operation": op,
        "account_id": params["account_id"],
        "window": {"start": start.isoformat() if start else None, "end": end.isoformat() if end else None},
        "parameters": {k: (str(v) if isinstance(v, Decimal) else v) for k, v in params.items()
                       if k not in ("operation", "account_id", "start", "end")},
        "findings": findings,
        "graph": graph,
        "searched": {**searched, **stats, "edge_cap": MAX_PATTERN_EDGES, "timeout_s": QUERY_TIMEOUT_S},
        "elapsed_ms": round((time.monotonic() - began) * 1000, 1),
        "complete": False,
        "limitation": LIMITATION,
        "caveat": CAVEAT,
    }
