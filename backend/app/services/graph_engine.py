"""Request-local graph engine hydrated from bounded, tenant-scoped canonical DB reads.

No process-global tenant graph or stale synchronization cache is retained. Supabase remains the
system of record. Snapshots returned here are content-addressed response artifacts, not saved rows.
"""
import hashlib
import json
from datetime import timedelta

from fastapi import HTTPException

from app.core.db import get_one
from app.core.errors import NotFound
from app.services import graph
from ml.graph import GraphLimits, LimitExceeded, NetworkXGraphAdapter, find_motifs
from ml.graph.adapter import envelope
from ml.graph.motifs import CYCLE_CAVEAT

LIMITS = GraphLimits(max_hops=6, max_nodes=300, max_edges=5000, max_range_days=120, timeout_s=5, max_results=100)


def health() -> dict:
    return {**NetworkXGraphAdapter(LIMITS).health_check(), "mode": "request-local", "system_of_record": "supabase"}


def _window(tenant_id, account_id, start, end):
    if not (start and end):
        default = graph._default_window(tenant_id, account_id)
        if default:
            start, end = start or default[0], end or default[1]
        elif start:
            end = start + timedelta(days=14)
        elif end:
            start = end - timedelta(days=14)
    if start and end:
        try:
            LIMITS.window(start, end)
        except LimitExceeded as exc:
            raise HTTPException(422, str(exc)) from exc
    return start, end


def analyze(tenant_id: str, params: dict) -> dict:
    account = str(params["account_id"])
    if not get_one("accounts", tenant_id, account, "id"):
        raise NotFound("Account")
    target = str(params["target_account_id"]) if params.get("target_account_id") else None
    if target and not get_one("accounts", tenant_id, target, "id"):
        raise NotFound("Target account")
    start, end = _window(tenant_id, account, params.get("start"), params.get("end"))
    # Cycles, metrics and split/merge need both sides of the focus. Source graph caps apply before analytics.
    payload = graph.neighborhood(tenant_id, account, params["hops"], start, end,
                                 direction=params["graph_direction"] if params["operation"] == "neighborhood" else "both",
                                 channels=params["channels"], min_amount=params["min_amount"], max_nodes=params["max_nodes"])
    limits = GraphLimits(**{**LIMITS.as_dict(), "max_nodes": params["max_nodes"], "max_results": params["limit"]})
    engine = NetworkXGraphAdapter(limits)
    engine.upsert_entities(tenant_id, [{"entity_id": n["id"], "type": "Account",
                                      "attrs": {k: v for k, v in n.items() if k not in ("id", "role", "layer")}}
                                     for n in payload["nodes"]])
    operation = params["operation"]
    edges = payload["edges"]
    # Amount-based methods require a common unit; preserve original amounts/currencies on canonical edges.
    monetary = operation in ("paths", "cycles", "metrics", "motifs")
    eligible = [e for e in edges if not monetary or e.get("amount_usd") is not None or e["currency"] == "USD"]
    excluded = len(edges) - len(eligible)
    transactions = [{"transaction_id": e["id"], "origin_account_id": e["source"], "destination_account_id": e["target"],
                     "amount": e["amount"], "currency": e["currency"], "amount_usd": e.get("amount_usd"),
                     "occurred_at": e["occurred_at"], "rail": e.get("channel"), "source_ref": e.get("transaction_ref"),
                     "lineage_ref": e.get("source_row_id"), "ingestion_run_id": e.get("ingestion_run_id"),
                     "status": e.get("transaction_status")} for e in eligible]
    loaded = engine.upsert_transactions(tenant_id, transactions)
    if loaded["rejected"]:
        raise HTTPException(422, "Canonical transactions could not be loaded into the graph engine")
    kw = {"start": start, "end": end}
    try:
        if operation == "neighborhood":
            result = engine.get_neighborhood(tenant_id, account, hops=params["hops"], **kw)
        elif operation == "trace":
            result = engine.trace_funds(tenant_id, account, hops=params["hops"], direction=params["direction"],
                                        max_gap_hours=params["max_gap_hours"], **kw)
        elif operation == "paths":
            # A valid target outside the hydrated neighborhood is unreachable inside this bounded search.
            if target and target not in {n["id"] for n in payload["nodes"]}:
                engine.upsert_entities(tenant_id, [{"entity_id": target, "type": "Account"}])
            result = engine.find_time_respecting_paths(tenant_id, account, target, hops=params["hops"],
                     max_gap_hours=params["max_gap_hours"], min_amount_ratio=params["min_amount_ratio"], limit=params["limit"], **kw)
        elif operation == "cycles":
            result = engine.find_cycles_bounded(tenant_id, account_id=account, min_hops=params["min_hops"],
                     max_hops=params["hops"], max_gap_hours=params["max_gap_hours"], **kw)
        elif operation == "metrics":
            result = engine.get_graph_metrics(tenant_id, account_ids=[account], **kw)
        elif operation == "snapshot":
            result = engine.get_graph_snapshot(tenant_id, account_id=account, hops=params["hops"], **kw)
        else:
            stats = {}
            items = find_motifs(params["motif"], transactions, min_branches=params["min_branches"],
                    window_hours=params["window_hours"], max_results=params["limit"], timeout_s=limits.timeout_s, stats=stats, **kw)
            # Fan findings belong to the requested focus; split/merge results stay within its hydrated context.
            if params["motif"] in ("fan_in", "fan_out"):
                items = [i for i in items if i.get("accounts", [None])[-1 if params["motif"] == "fan_in" else 0] == account]
            result = envelope(graph_version=loaded["graph_version"], limits=limits, items=items,
                     truncated=stats["truncated"], truncation_reason=stats["truncation_reason"], caveats=[CYCLE_CAVEAT])
    except LimitExceeded as exc:
        raise HTTPException(422, str(exc)) from exc
    if operation == "neighborhood":
        # Compatibility projection of the engine's returned canonical nodes/edges for the existing canvas.
        node_ids = {n["entity_id"] for n in result["nodes"]}
        edge_ids = {e["transaction_id"] for e in result["edges"]}
        result["workbench_graph"] = {**payload, "nodes": [n for n in payload["nodes"] if n["id"] in node_ids],
                                     "edges": [e for e in edges if e["id"] in edge_ids]}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    result["source_snapshot_id"] = f"db_{digest}"
    result["source"] = {"system": "supabase", "window": {"start": start.isoformat() if start else None,
                        "end": end.isoformat() if end else None}, "nodes": len(payload["nodes"]),
                        "edges": len(edges), "excluded_no_usd": excluded, "limits": payload["limits"]}
    if payload["truncated"]:
        result["truncated"] = True
        result["truncation_reason"] = payload["limits"]["stopped_by"] or "source_cap"
    if operation == "neighborhood":
        result["workbench_graph"]["truncated"] = result["truncated"]
        result["workbench_graph"]["source_window"] = result["source"]["window"]
    result["caveats"].append("Results cover a bounded database neighborhood. An empty result does not establish absence of a pattern.")
    if monetary:
        result["amount_basis"] = "USD; amount_usd, or native amount when currency is USD"
    if excluded:
        result["caveats"].append(f"Data-quality caveat: {excluded} non-USD edge(s) excluded because USD conversion is missing.")
    if operation == "snapshot":
        result["caveats"].append("Snapshot is returned in this response only; it is not persisted or recoverable by ID.")
    return result
