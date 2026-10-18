"""In-memory NetworkX GraphBase provider behind the GraphAdapter contract (PRD v3.3 §6).

The system of record stays in Supabase; services upsert canonical rows here and query bounded views.
One isolated graph per tenant: a query for another tenant's account is NotFound, never a cross-tenant read.
"""
import hashlib
import heapq
import json
import math
import time
from collections import Counter, defaultdict
from datetime import timedelta

import networkx as nx

from .adapter import (NODE_TYPES, PATH_CAVEAT, RELATIONSHIP_TYPES, TXN_OPTIONAL, TXN_REQUIRED, GraphLimits,
                      LimitExceeded, NotFound, envelope)
from .motifs import CYCLE_CAVEAT, find_cycles
from .paths import parse_ts, time_respecting_paths

PROVIDER = "networkx-inmemory"


class _Tenant:
    def __init__(self):
        self.entities: dict[str, dict] = {}
        self.relationships: dict[tuple, dict] = {}
        self.txns: dict[str, dict] = {}
        self.out: dict[str, set] = defaultdict(set)
        self.inn: dict[str, set] = defaultdict(set)
        self.version = 0
        self.snapshots: dict[str, dict] = {}


class NetworkXGraphAdapter:
    def __init__(self, limits: GraphLimits | None = None):
        self.limits = limits or GraphLimits()
        self._tenants: dict[str, _Tenant] = {}

    # --- helpers -----------------------------------------------------------
    def _t(self, tenant_id, create=False) -> _Tenant:
        if not tenant_id:
            raise NotFound("tenant_id is required")
        if tenant_id not in self._tenants:
            if not create:
                raise NotFound("unknown tenant")
            self._tenants[tenant_id] = _Tenant()
        return self._tenants[tenant_id]

    def _account(self, T, account_id):
        if account_id not in T.out and account_id not in T.inn and account_id not in T.entities:
            raise NotFound("unknown account")

    def _in_window(self, tx, start, end):
        return (start is None or tx["_t"] >= start) and (end is None or tx["_t"] <= end)

    def _window(self, start, end):
        return self.limits.window(parse_ts(start) if start else None, parse_ts(end) if end else None)

    def _env(self, T, **kw):
        return envelope(graph_version=T.version, limits=self.limits, **kw)

    @staticmethod
    def _public(tx):
        return {k: v for k, v in tx.items() if not k.startswith("_")}

    @staticmethod
    def _api_shape(tx):
        """Edge shape expected by ml.graph.paths.time_respecting_paths (shared with app/services/graph.py)."""
        return {"id": tx["transaction_id"], "transaction_ref": tx.get("source_ref") or tx["transaction_id"],
                "origin_account_id": tx["origin_account_id"], "destination_account_id": tx["destination_account_id"],
                "occurred_at": tx["occurred_at"], "amount": tx["amount"], "amount_usd": tx["amount_usd"] if tx.get("amount_usd") is not None else tx["amount"],
                "currency": tx["currency"]}

    # --- writes ------------------------------------------------------------
    def upsert_entities(self, tenant_id, entities, relationships=None):
        T = self._t(tenant_id, create=True)
        rejected, n_ent, n_rel = [], 0, 0
        for i, e in enumerate(entities):
            if not e.get("entity_id") or e.get("type") not in NODE_TYPES:
                rejected.append({"index": i, "entity_id": e.get("entity_id"), "reason": "entity_id and a valid type are required"})
                continue
            T.entities[e["entity_id"]] = {"entity_id": e["entity_id"], "type": e["type"], "attrs": dict(e.get("attrs", {}))}
            n_ent += 1
        for i, r in enumerate(relationships or []):
            if r.get("type") not in RELATIONSHIP_TYPES:
                rejected.append({"index": i, "relationship": r.get("type"), "reason": "unknown relationship type"})
            elif r.get("source_id") not in T.entities or r.get("target_id") not in T.entities:
                rejected.append({"index": i, "relationship": r.get("type"), "reason": "source/target entity not found"})
            else:
                T.relationships[(r["type"], r["source_id"], r["target_id"])] = {k: r[k] for k in ("type", "source_id", "target_id")} | {"attrs": dict(r.get("attrs", {}))}
                n_rel += 1
        if n_ent or n_rel:
            T.version += 1
        return {"status": "ok", "accepted_entities": n_ent, "accepted_relationships": n_rel, "rejected": rejected, "graph_version": T.version}

    def upsert_transactions(self, tenant_id, transactions):
        """Idempotent on transaction_id; rows missing required fields or with bad amount/timestamp are rejected."""
        T = self._t(tenant_id, create=True)
        accepted, rejected = 0, []
        for i, row in enumerate(transactions):
            missing = [k for k in TXN_REQUIRED if row.get(k) in (None, "")]
            try:
                amount, t = float(row.get("amount")), parse_ts(row.get("occurred_at"))
                bad = None if (not missing and math.isfinite(amount) and amount >= 0) else "invalid amount"
            except (TypeError, ValueError):
                bad = "unparseable amount or occurred_at"
            if missing or bad:
                rejected.append({"index": i, "transaction_id": row.get("transaction_id"), "reason": f"missing {missing}" if missing else bad})
                continue
            old = T.txns.get(row["transaction_id"])
            if old:
                T.out[old["origin_account_id"]].discard(old["transaction_id"])
                T.inn[old["destination_account_id"]].discard(old["transaction_id"])
            tx = {k: row[k] for k in TXN_REQUIRED + TXN_OPTIONAL if k in row} | {"_t": t}
            tx["occurred_at"] = t.isoformat()
            T.txns[tx["transaction_id"]] = tx
            T.out[tx["origin_account_id"]].add(tx["transaction_id"])
            T.inn[tx["destination_account_id"]].add(tx["transaction_id"])
            for acct in (tx["origin_account_id"], tx["destination_account_id"]):
                T.entities.setdefault(acct, {"entity_id": acct, "type": "Account", "attrs": {}})
            accepted += 1
        if accepted:
            T.version += 1
        return {"status": "ok", "accepted": accepted, "rejected": rejected, "graph_version": T.version}

    # --- reads -------------------------------------------------------------
    def _bfs(self, T, seed, hops, start, end, direction="both"):
        """Undirected (or directed) BFS over transaction edges; capped by nodes, edges and timeout."""
        lim, deadline = self.limits, time.monotonic() + self.limits.timeout_s
        hop_of, edges, frontier = {seed: 0}, {}, [seed]
        reason = None
        for h in range(1, hops + 1):
            nxt = []
            for n in frontier:
                ids = (T.out[n] if direction in ("both", "out") else set()) | (T.inn[n] if direction in ("both", "in") else set())
                for tid in sorted(ids):
                    tx = T.txns[tid]
                    if not self._in_window(tx, start, end):
                        continue
                    if len(edges) >= lim.max_edges:
                        reason = "max_edges"
                        break
                    edges[tid] = tx
                    other = tx["destination_account_id"] if tx["origin_account_id"] == n else tx["origin_account_id"]
                    if other not in hop_of:
                        if len(hop_of) >= lim.max_nodes:
                            reason = "max_nodes"
                            break
                        hop_of[other] = h
                        nxt.append(other)
                if reason or time.monotonic() > deadline:
                    reason = reason or "timeout"
                    break
            if reason:
                break
            frontier = nxt
        return hop_of, edges, reason

    def _nodes(self, T, hop_of):
        return [{**T.entities.get(n, {"entity_id": n, "type": "Account", "attrs": {}}), "hop": h} for n, h in sorted(hop_of.items(), key=lambda kv: (kv[1], kv[0]))]

    def get_neighborhood(self, tenant_id, account_id, *, hops=1, start=None, end=None):
        T = self._t(tenant_id)
        self._account(T, account_id)
        hops = self.limits.hops(hops)
        start, end = self._window(start, end)
        hop_of, edges, reason = self._bfs(T, account_id, hops, start, end)
        rels = [r for r in T.relationships.values() if r["source_id"] in hop_of or r["target_id"] in hop_of]
        return self._env(T, truncated=bool(reason), truncation_reason=reason, focus=account_id,
                         nodes=self._nodes(T, hop_of), edges=[self._public(e) for e in sorted(edges.values(), key=lambda e: e["_t"])],
                         relationships=rels)

    def _trace(self, T, seed, hops, start, end, down, gap):
        """Earliest-arrival expansion (Dijkstra on time; latest-departure upstream). Only edges that are
        time-feasible from the funds' arrival at a node are ever touched, so caps bind on relevant edges."""
        sign = 1 if down else -1
        reach = {seed: (0, None)}  # node -> (hops at best time, earliest arrival / latest departure)
        heap, used, reason, n = [(0.0, 0, 0, seed)], {}, None, 0  # (time key, tie-break, hops, node)
        deadline = time.monotonic() + self.limits.timeout_s
        while heap:
            _, _, h, node = heapq.heappop(heap)
            t_node = reach[node][1]
            if h >= hops:
                continue
            for tid in (T.out[node] if down else T.inn[node]):
                tx = T.txns[tid]
                if not self._in_window(tx, start, end):
                    continue
                if t_node is not None and ((down and tx["_t"] <= t_node) or (not down and tx["_t"] >= t_node) or (gap and abs(tx["_t"] - t_node) > gap)):
                    continue
                if len(used) >= self.limits.max_edges:
                    reason = "max_edges"
                    break
                used[tid] = tx
                nxt = tx["destination_account_id"] if down else tx["origin_account_id"]
                prev = reach.get(nxt)
                if nxt != seed and (prev is None or sign * (tx["_t"] - prev[1]).total_seconds() < 0):
                    if prev is None and len(reach) >= self.limits.max_nodes:
                        reason = "max_nodes"
                        break
                    reach[nxt] = (h + 1, tx["_t"])
                    n += 1
                    heapq.heappush(heap, (sign * tx["_t"].timestamp(), n, h + 1, nxt))
            if reason or time.monotonic() > deadline:
                reason = reason or "timeout"
                break
        return reach, used, reason

    def trace_funds(self, tenant_id, account_id, *, direction="downstream", hops=3, start=None, end=None, max_gap_hours=None):
        """Where could funds from (downstream) or into (upstream) this account have moved, respecting time order?

        max_gap_hours is measured from each account's earliest arrival (latest departure upstream): a simple,
        conservative bound. Use find_time_respecting_paths for exact per-hop gaps along specific chains."""
        if direction not in ("downstream", "upstream"):
            raise LimitExceeded("direction must be downstream or upstream")
        T = self._t(tenant_id)
        self._account(T, account_id)
        hops = self.limits.hops(hops)
        start, end = self._window(start, end)
        down = direction == "downstream"
        reach, used, reason = self._trace(T, account_id, hops, start, end, down, timedelta(hours=max_gap_hours) if max_gap_hours else None)
        key = "first_arrival" if down else "last_departure"
        nodes = [{"account_id": n, "hop": hp, key: t.isoformat() if t else None} for n, (hp, t) in sorted(reach.items(), key=lambda kv: (kv[1][0], kv[0]))]
        return self._env(T, truncated=bool(reason), truncation_reason=reason, caveats=[PATH_CAVEAT], focus=account_id,
                         direction=direction, nodes=nodes, edges=[self._public(e) for e in sorted(used.values(), key=lambda e: e["_t"])])

    def find_time_respecting_paths(self, tenant_id, source, target=None, *, hops=4, start=None, end=None,
                                   max_gap_hours=72, min_amount_ratio=0.0, limit=20):
        T = self._t(tenant_id)
        self._account(T, source)
        if target is not None:
            self._account(T, target)
        hops = self.limits.hops(hops)
        if not 1 <= limit <= self.limits.max_results:
            raise LimitExceeded(f"limit must be 1..{self.limits.max_results}")
        start, end = self._window(start, end)
        # candidates: time-feasible edges only; the gap is checked per hop by the DFS (a gap measured from a node's
        # earliest arrival would wrongly drop hops of a later chain through the same account)
        _, edges, reason = self._trace(T, source, hops, start, end, True, None)
        stats = {}
        found = time_respecting_paths([self._api_shape(e) for e in edges.values()], source, target, hops,
                                      timedelta(hours=max_gap_hours), min_amount_ratio, limit, stats=stats, timeout_s=self.limits.timeout_s)
        reason = reason or stats.get("truncation_reason")
        return self._env(T, truncated=bool(reason), truncation_reason=reason, caveats=[PATH_CAVEAT], items=found,
                         parameters={"hops": hops, "max_gap_hours": max_gap_hours, "min_amount_ratio": min_amount_ratio})

    def find_cycles_bounded(self, tenant_id, *, account_id=None, min_hops=3, max_hops=6, start=None, end=None,
                            max_gap_hours=None):
        T = self._t(tenant_id)
        self.limits.hops(max_hops)
        start, end = self._window(start, end)
        if account_id:
            self._account(T, account_id)
            # an account on a cycle of length <= L through the focus is reachable from it AND reaches back to it
            # within L-1 directed hops; restricting to that intersection keeps the subgraph small around busy hubs
            # time-pruned in both directions: cycles must be time-respecting, so only time-feasible edges matter
            fwd, _, r1 = self._trace(T, account_id, max_hops - 1, start, end, True, None)
            back, _, r2 = self._trace(T, account_id, max_hops - 1, start, end, False, None)
            keep = set(fwd) & set(back)
            edges = [T.txns[i] for a in keep for i in T.out[a]
                     if T.txns[i]["destination_account_id"] in keep and self._in_window(T.txns[i], start, end)]
            reason = r1 or r2
        else:
            edges = [tx for tx in T.txns.values() if self._in_window(tx, start, end)]
            if len(edges) > self.limits.max_edges:
                raise LimitExceeded("tenant-wide cycle search over max_edges; scope to an account or narrow the time range")
            reason = None
        stats = {}
        items = find_cycles(edges, min_hops=min_hops, max_hops=max_hops, max_gap_hours=max_gap_hours,
                            max_nodes=self.limits.max_nodes, max_cycles=self.limits.max_results,
                            timeout_s=self.limits.timeout_s, stats=stats, focus_account=account_id)
        if account_id:
            items = [c for c in items if account_id in c["accounts"]]
        reason = reason or stats["truncation_reason"]
        return self._env(T, truncated=bool(reason), truncation_reason=reason, caveats=[CYCLE_CAVEAT], items=items,
                         parameters={"min_hops": min_hops, "max_hops": max_hops, "max_gap_hours": max_gap_hours, "account_id": account_id})

    def get_graph_metrics(self, tenant_id, *, account_ids=None, start=None, end=None):
        T = self._t(tenant_id)
        start, end = self._window(start, end)
        reason = None
        if account_ids:
            for a in account_ids:
                self._account(T, a)
            edges = {}
            for a in account_ids:  # metrics on the 2-hop context of the requested accounts
                _, e, r = self._bfs(T, a, 2, start, end)
                edges.update(e)
                reason = reason or r
            edges = list(edges.values())
        else:
            edges = [tx for tx in T.txns.values() if self._in_window(tx, start, end)]
        if len(edges) > self.limits.max_edges:
            raise LimitExceeded("metrics over max_edges; pass account_ids or narrow the time range")
        G = nx.DiGraph()
        for tx in edges:
            u, v = tx["origin_account_id"], tx["destination_account_id"]
            d = G.get_edge_data(u, v) or {"count": 0, "amount": 0.0}
            G.add_edge(u, v, count=d["count"] + 1, amount=d["amount"] + float(tx["amount_usd"] if tx.get("amount_usd") is not None else tx["amount"]))
        n = G.number_of_nodes()
        if n == 0:
            return self._env(T, items=[], summary={"nodes": 0, "edges": 0})
        U = nx.Graph(G)
        U.remove_edges_from(nx.selfloop_edges(U))
        core = nx.core_number(U)
        comp = {node: i for i, c in enumerate(sorted(nx.weakly_connected_components(G), key=lambda c: (-len(c), min(c)))) for node in c}
        pr = nx.pagerank(G, weight="amount")
        approx = n > 500
        btw = nx.betweenness_centrality(G, k=min(200, n) if approx else None, seed=0)  # sampled on big graphs
        nodes = account_ids or sorted(pr, key=lambda x: -pr[x])[: self.limits.max_results]
        items = [{"account_id": a,
                  "in_degree": sum(d["count"] for _, _, d in G.in_edges(a, data=True)),
                  "out_degree": sum(d["count"] for _, _, d in G.out_edges(a, data=True)),
                  "weighted_in": round(sum(d["amount"] for _, _, d in G.in_edges(a, data=True)), 2),
                  "weighted_out": round(sum(d["amount"] for _, _, d in G.out_edges(a, data=True)), 2),
                  "component_id": comp[a], "k_core": core.get(a, 0), "pagerank": round(pr[a], 6),
                  "betweenness": round(btw[a], 6)} for a in nodes if a in G]
        caveats = ["Betweenness is sampled (k=200) on graphs over 500 nodes."] if approx else []
        return self._env(T, truncated=bool(reason), truncation_reason=reason, caveats=caveats, items=items,
                         summary={"nodes": n, "edges": G.number_of_edges(), "transactions": len(edges),
                                  "components": len(set(comp.values())), "largest_component": max(Counter(comp.values()).values())})

    def get_graph_snapshot(self, tenant_id, *, account_id=None, hops=2, start=None, end=None, snapshot_id=None):
        """Saved, content-addressed snapshot: id + sha256 of the canonical payload + graph version."""
        T = self._t(tenant_id)
        if snapshot_id:
            if snapshot_id not in T.snapshots:
                raise NotFound("unknown snapshot")
            return T.snapshots[snapshot_id]
        start, end = self._window(start, end)
        if account_id:
            self._account(T, account_id)
            hop_of, edges, reason = self._bfs(T, account_id, self.limits.hops(hops), start, end)
            nodes = self._nodes(T, hop_of)
        else:
            edges = {k: tx for k, tx in T.txns.items() if self._in_window(tx, start, end)}
            if len(edges) > self.limits.max_edges:
                raise LimitExceeded("snapshot over max_edges; scope to an account or narrow the time range")
            accts = {a for tx in edges.values() for a in (tx["origin_account_id"], tx["destination_account_id"])}
            nodes, reason = [T.entities.get(a, {"entity_id": a, "type": "Account", "attrs": {}}) for a in sorted(accts)], None
        body = {"scope": {"account_id": account_id, "hops": hops if account_id else None,
                          "start": start.isoformat() if start else None, "end": end.isoformat() if end else None},
                "nodes": nodes, "edges": [self._public(e) for e in sorted(edges.values(), key=lambda e: (e["_t"], e["transaction_id"]))]}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
        sid = f"gs_{digest[:16]}_v{T.version}"
        snap = self._env(T, truncated=bool(reason), truncation_reason=reason, snapshot_id=sid,
                         content_hash=digest, **body)
        snap["snapshot_id"] = sid
        T.snapshots[sid] = snap
        return snap

    def compare_snapshots(self, tenant_id, a, b):
        """Edge-level diff between two saved snapshots of the same tenant."""
        sa, sb = self.get_graph_snapshot(tenant_id, snapshot_id=a), self.get_graph_snapshot(tenant_id, snapshot_id=b)
        ea, eb = {e["transaction_id"] for e in sa["edges"]}, {e["transaction_id"] for e in sb["edges"]}
        return {"status": "ok", "from": a, "to": b, "added_edges": sorted(eb - ea), "removed_edges": sorted(ea - eb),
                "unchanged_edges": len(ea & eb), "same_content": sa["content_hash"] == sb["content_hash"]}

    def health_check(self):
        return {"status": "ok", "provider": PROVIDER, "tenants": len(self._tenants),
                "nodes": sum(len(T.entities) for T in self._tenants.values()),
                "edges": sum(len(T.txns) for T in self._tenants.values()), "limits": self.limits.as_dict()}
