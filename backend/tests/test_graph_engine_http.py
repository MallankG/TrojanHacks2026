"""Authenticated HTTP contracts with real NetworkX analytics over tenant-scoped source fixtures."""
import os
from copy import deepcopy
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core.security import CurrentUser, get_current_user
from app.main import app
from app.services import graph_engine as service

A, B, C, D = [f"00000000-0000-0000-0000-{i:012d}" for i in range(1, 5)]


def source(tenant):
    ids = [A, B, C] if tenant == "t1" else [D]
    edges = [{"id": f"tx-{tenant}-{i}", "source": u, "target": v, "occurred_at": f"2026-10-01T{10+i:02d}:00:00Z",
              "amount": "100", "amount_usd": "100", "currency": "USD", "channel": "wire", "transaction_ref": f"ref-{i}",
              "source_row_id": f"row-{i}"} for i, (u, v) in enumerate([(A, B), (B, C), (C, A)])] if tenant == "t1" else []
    return {"nodes": [{"id": i, "account_ref": f"account-{tenant}-{i}", "degree": 2} for i in ids],
            "edges": edges, "truncated": False, "limits": {"max_edges": 5000, "max_nodes": 300, "stopped_by": None}}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def authenticated(client):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("user", "t1", "analyst")
    def account(table, tenant, account_id, columns):
        return {"id": account_id} if account_id in ({A, B, C} if tenant == "t1" else {D}) else None
    with patch.object(service, "get_one", side_effect=account), \
            patch.object(service.graph, "neighborhood", side_effect=lambda tid, *args, **kwargs: deepcopy(source(tid))) as load, \
            patch.object(service.graph, "_default_window", return_value=None):
        yield client, load


def analyze(client, op, **params):
    return client.post("/graph/engine/analysis", json={"operation": op, "account_id": A,
                       "start": "2026-10-01T00:00:00Z", "end": "2026-10-02T00:00:00Z", **params})


def test_engine_routes_require_authentication(client):
    assert client.get("/graph/engine/health").status_code == 401
    assert analyze(client, "metrics").status_code == 401


def test_canonical_neighborhood_and_lineage(authenticated):
    client, load = authenticated
    response = analyze(client, "neighborhood", graph_direction="incoming", channels=["wire"], min_amount=100, max_nodes=50)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok" and data["source_snapshot_id"].startswith("db_")
    assert {n["entity_id"] for n in data["nodes"]} == {A, B, C}
    assert {e["transaction_id"] for e in data["edges"]} == {"tx-t1-0", "tx-t1-1", "tx-t1-2"}
    assert data["edges"][0]["lineage_ref"] == "row-0"
    assert data["workbench_graph"]["edges"][0]["id"] == "tx-t1-0"
    assert data["limits"]["max_edges"] == 5000
    assert load.call_args.args[0] == "t1"
    assert load.call_args.args[3].tzinfo is not None
    assert load.call_args.kwargs["direction"] == "incoming"
    assert load.call_args.kwargs["channels"] == ["wire"]
    assert load.call_args.kwargs["min_amount"] == 100
    assert load.call_args.kwargs["max_nodes"] == 50


def test_metrics_trace_paths_cycles_and_motifs_use_real_engine(authenticated):
    client, _ = authenticated
    metric = analyze(client, "metrics").json()
    assert metric["items"][0]["in_degree"] == 1
    assert metric["items"][0]["weighted_out"] == 100
    trace = analyze(client, "trace").json()
    assert {e["transaction_id"] for e in trace["edges"]} >= {"tx-t1-0", "tx-t1-1"}
    paths = analyze(client, "paths", target_account_id=C).json()
    assert [h["transaction_id"] for h in paths["items"][0]["hops"]] == ["tx-t1-0", "tx-t1-1"]
    cycles = analyze(client, "cycles").json()
    assert set(cycles["items"][0]["edge_ids"]) == {"tx-t1-0", "tx-t1-1", "tx-t1-2"}
    assert analyze(client, "motifs").status_code == 200
    assert analyze(client, "snapshot").json()["content_hash"]


def test_other_tenant_account_is_not_found_and_graph_is_not_loaded(authenticated):
    client, load = authenticated
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("other-user", "t2", "reviewer")
    assert analyze(client, "neighborhood").status_code == 404
    load.assert_not_called()
    response = analyze(client, "neighborhood", account_id=D)
    assert response.status_code == 200
    assert response.json()["edges"] == []
    assert {n["entity_id"] for n in response.json()["nodes"]} == {D}


def test_caps_timezones_and_query_text_rejected_before_loading(authenticated):
    client, load = authenticated
    for override in ({"hops": 7}, {"max_nodes": 301}, {"limit": 101}, {"operation": "raw_sql"},
                     {"start": "2026-01-01T00:00:00Z"}, {"start": "2026-10-03T00:00:00Z"},
                     {"start": "2026-10-01T00:00:00"}, {"tenant_id": "t2"}):
        assert analyze(client, "metrics", **override).status_code == 422
    load.assert_not_called()


def test_source_truncation_survives_engine_analysis(authenticated):
    client, load = authenticated
    payload = source("t1")
    payload["truncated"] = True
    payload["limits"]["stopped_by"] = "max_nodes"
    load.side_effect = None
    load.return_value = payload
    data = analyze(client, "metrics").json()
    assert data["truncated"] and data["truncation_reason"] == "max_nodes"


def test_fx_is_used_without_losing_canonical_amounts(authenticated):
    client, load = authenticated
    payload = source("t1")
    payload["edges"][0].update(amount="15000", currency="JPY", amount_usd="100")
    load.side_effect = None
    load.return_value = payload
    assert analyze(client, "metrics").json()["items"][0]["weighted_out"] == 100
    canonical = analyze(client, "neighborhood").json()["edges"][0]
    assert canonical["amount"] == "15000" and canonical["currency"] == "JPY"
    payload["edges"][0]["amount_usd"] = None
    data = analyze(client, "metrics").json()
    assert data["source"]["excluded_no_usd"] == 1
    assert any("conversion is missing" in c for c in data["caveats"])


def test_new_request_rebuilds_graph_and_snapshot_is_repeatable(authenticated):
    client, load = authenticated
    first = analyze(client, "neighborhood").json()
    assert analyze(client, "neighborhood").json()["source_snapshot_id"] == first["source_snapshot_id"]
    payload = source("t1")
    payload["edges"] = []
    load.side_effect = None
    load.return_value = payload
    second = analyze(client, "neighborhood").json()
    assert second["edges"] == []
    assert second["source_snapshot_id"] != first["source_snapshot_id"]


@pytest.mark.skipif(os.environ.get("GRAPH_HTTP_SOCKET_TEST") != "1", reason="opt-in localhost socket smoke test")
def test_actual_http_transport(authenticated):
    """Exercise real TCP/HTTP with explicit synthetic source/auth fixtures, not production Supabase."""
    import socket
    import threading
    import time
    import httpx
    import uvicorn

    _, _load = authenticated
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        base = f"http://127.0.0.1:{sock.getsockname()[1]}"
        with httpx.Client(base_url=base) as http:
            assert http.get("/graph/engine/health").json()["provider"] == "networkx-inmemory"
            response = http.post("/graph/engine/analysis", json={"operation": "metrics", "account_id": A,
                                "start": "2026-10-01T00:00:00Z", "end": "2026-10-02T00:00:00Z"})
            assert response.status_code == 200
            assert response.json()["items"][0]["weighted_out"] == 100
            app.dependency_overrides.pop(get_current_user)
            assert http.get("/graph/engine/health").status_code == 401
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
