"""Explicit localhost-only SYNTHETIC graph HTTP fixture for browser verification.

Run from backend: PYTHONPATH=. python tests/serve_graph_fixture.py --port 8011
Never import this file into the production app or deploy it. Authentication and DB source are fixture data;
GraphBase hydration, typed HTTP routing, canonical engine calculations and canvas projection are real.
"""
import argparse
import json
import os
from copy import deepcopy
from datetime import UTC, datetime

os.environ.setdefault("SUPABASE_URL", "https://fixture.invalid")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-only-fixture-key")

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--port", type=int, default=8011)
parser.add_argument("--frontend-origin", default="http://127.0.0.1:3103")
args = parser.parse_args()

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from app.api.routes import graph as routes, graph_engine
from app.core.errors import install_error_handlers
from app.core.security import CurrentUser, get_current_user
from app.services import graph, graph_engine as service
from test_graph_engine_http import A, B, C, D, source

TOKEN = "graph-fixture-token"
PROFILE = {"id": A, "email": "graph-fixture@example.test", "display_name": "Synthetic graph fixture", "role": "analyst",
           "tenant": {"id": "t1", "slug": "graph-fixture", "name": "Synthetic graph fixture", "is_synthetic": True}}


def fixture_user(authorization: str | None = Header(default=None)):
    if authorization != f"Bearer {TOKEN}":
        raise HTTPException(401, "Synthetic fixture requires its test token")
    return CurrentUser(A, "t1", "analyst")


def get_account(table, tenant, account_id, columns="*"):
    return {"id": account_id} if account_id in ({A, B, C} if tenant == "t1" else {D}) else None


def neighborhood(tenant, account, hops, start, end, direction="both", channels=None, min_amount=None, max_nodes=300):
    payload = deepcopy(source(tenant))
    edges = [e for e in payload["edges"] if (not channels or e["channel"] in channels)
             and (not min_amount or float(e["amount_usd"]) >= float(min_amount))
             and (not start or datetime.fromisoformat(e["occurred_at"].replace("Z", "+00:00")) >= start)
             and (not end or datetime.fromisoformat(e["occurred_at"].replace("Z", "+00:00")) <= end)]
    layers, frontier, selected = {account: 0}, {account}, {}
    for depth in range(1, hops + 1):
        nxt = set()
        for e in edges:
            forward = e["source"] in frontier and direction != "incoming"
            backward = e["target"] in frontier and direction != "outgoing"
            if not (forward or backward):
                continue
            for node, sign, include in ((e["target"], 1, forward), (e["source"], -1, backward)):
                if include and node not in layers and len(layers) < max_nodes:
                    layers[node] = sign * depth
                    nxt.add(node)
            if e["source"] in layers and e["target"] in layers:
                selected[e["id"]] = e
        frontier = nxt
    payload["nodes"] = [{**n, "account_ref": f"FIXTURE-{i+1}", "layer": layers[n["id"]], "role": "focus" if n["id"] == account else None}
                        for i, n in enumerate(payload["nodes"]) if n["id"] in layers]
    payload["edges"] = list(selected.values())
    payload["limits"].update(hops=hops, direction=direction, max_nodes=max_nodes, timeout_s=10)
    return payload


def accounts(tenant, q=None, limit=25):
    rows = [{"id": i, "account_ref": f"FIXTURE-{n+1}", "institution_ref": "SYNTHETIC", "is_internal": True,
             "status": "active", "in_count": 1, "out_count": 1, "degree": 2, "counterparty_count": 2,
             "volume_usd": "200", "first_seen": "2026-10-01T10:00:00Z", "last_seen": "2026-10-01T12:00:00Z"}
            for n, i in enumerate([A, B, C])]
    return {"items": [r for r in rows if not q or q.lower() in r["account_ref"].lower()][:limit],
            "suggested": {"account_id": A, "reason": "most_connected", "alert_id": None, "alert_number": None}}


service.get_one = get_account
routes.get_one = get_account
graph.neighborhood = neighborhood
graph.search_accounts = accounts
graph._default_window = lambda tenant, account: (datetime(2026, 9, 24, tzinfo=UTC), datetime(2026, 10, 8, tzinfo=UTC))
fixture_app = FastAPI(title="TEST ONLY — Synthetic graph HTTP fixture")
fixture_app.add_middleware(CORSMiddleware, allow_origins=[args.frontend_origin], allow_headers=["*"], allow_methods=["*"])
install_error_handlers(fixture_app)
fixture_app.dependency_overrides[get_current_user] = fixture_user
fixture_app.include_router(graph_engine.router)
fixture_app.include_router(routes.router)


@fixture_app.get("/health")
def health():
    return {"status": "ok", "fixture": "synthetic-test-only"}


@fixture_app.get("/me")
def me(user=Depends(fixture_user)):
    return PROFILE


@fixture_app.get("/notifications")
def notifications(user=Depends(fixture_user)):
    return {"items": [], "total": 0}


@fixture_app.get("/dashboard/summary")
def summary(user=Depends(fixture_user)):
    from app.services.dashboard import summarize_alerts, summarize_cases, summarize_ingestion
    return {"generated_at": datetime.now(UTC).isoformat(),
            "alerts": {**summarize_alerts([], datetime.now(UTC), 5), "truncated": False, "run_alert_total": 0,
                       "connected_accounts": 3, "connected_accounts_truncated": False, "model_run": None},
            "cases": {**summarize_cases([]), "truncated": False},
            "ingestion": {**summarize_ingestion([], {}), "truncated": False},
            "model_freshness": dict.fromkeys(["rule_engine", "supervised", "anomaly", "graph_model", "ranker"]), "pipeline": []}


print("TEST ONLY: synthetic source and fixture auth; real NetworkX graph HTTP routes.")
print("Browser session for tracelayer.live-session:")
print(json.dumps({"accessToken": TOKEN, "refreshToken": "fixture-refresh", "expiresAt": 4102444800, "profile": PROFILE}))
uvicorn.run(fixture_app, host="127.0.0.1", port=args.port, log_level="info")
