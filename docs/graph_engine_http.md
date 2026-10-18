# Graph engine HTTP integration

The live graph workbench calls `POST /graph/engine/analysis` for its canvas neighborhoods. The backend loads a bounded canonical neighborhood from Supabase, constructs a fresh NetworkX `GraphAdapter`, runs the requested operation, and discards the graph after the request. Supabase remains the system of record. Existing `/graph/accounts`, `/graph/paths`, and `/graph/patterns/search` contracts remain available for account search, exact path inspection and the existing pattern tools.

This implements the PRD v3.3 §6 adapter and canonical JSON requirements behind authenticated FastAPI routes. The analytical envelope includes `status`, `as_of`, `graph_version`, `source_snapshot_id`, `limits`, `truncated`, `truncation_reason`, and `caveats`. The source snapshot ID hashes the bounded source payload; `graph_version` is the request-local adapter revision, not a persistent synchronization watermark. `source.window` reports the actual database time window.

## Routes

Both routes use the existing bearer-token or internal worker authentication and derive tenant scope from the authenticated caller. A body cannot override `tenant_id`. Unknown or other-tenant accounts return 404 before loading a graph.

- `GET /graph/engine/health`: provider import/runtime check; reports `networkx-inmemory`, request-local mode and server limits. It does not test database connectivity.
- `POST /graph/engine/analysis`: typed, read-only operation; never accepts query text.

Required body fields: `operation` and `account_id`. Operations: `neighborhood`, `trace`, `paths`, `cycles`, `metrics`, `motifs`, `snapshot`. `paths` optionally accepts `target_account_id`; `trace` accepts upstream/downstream `direction`; `motifs` accepts `fan_in`, `fan_out`, or `split_merge`. See `/docs` for the validated field schema.

Server limits: 6 hops, 300 nodes, 5,000 source edges, 120 days, 100 results, 10 seconds of cooperative database paging and 5 seconds of adapter work. Individual in-flight database requests use the configured client timeout. The default scope is 14 days around the focus account's latest activity. Explicit timestamps must include a timezone. Edge/node/time bounds propagate into the response; results describe the bounded subgraph and never establish that no pattern exists outside it.

The neighborhood response includes `workbench_graph`, a compatibility projection of canonical engine output consumed by the existing canvas. Direction, rails, minimum USD amount, edge IDs, node layers, selection, pinning and table view continue to work. The UI displays the effective source window.

Canonical edges preserve native amount/currency, rail, transaction reference, source-row lineage and ingestion run. Monetary analytics use `amount_usd`, falling back to native amount only for USD. Non-USD edges without conversion remain visible in neighborhoods/snapshots but are excluded from monetary analytics with an explicit count and caveat. Graph analytical floating-point values are investigative context, not accounting balances.

Snapshots are response artifacts only: this endpoint does not persist snapshots or offer retrieval by ID. Existing storage/case export controls remain responsible for durable evidence.

## Start and verify

Install `backend/requirements.txt` (includes `networkx==3.6.1`). Configure `backend/.env` using its existing example and start the API from `backend`:

```sh
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Configure the frontend's existing `.env.local` with `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000` and the Supabase public URL/key, then rebuild/restart it. Sign in using a provisioned tenant account and open `/graph`. In browser network tools, selecting an account, changing direction, rails, minimum amount or time window should send `POST /graph/engine/analysis` and return canonical edges plus `workbench_graph`. Verify table view, edge selection and pinning still refer to the returned transaction IDs. A backend failure is shown through the existing live error state.

Using an existing access token and a real account UUID from that tenant:

```sh
curl --fail http://127.0.0.1:8000/graph/engine/health -H "Authorization: Bearer $ACCESS_TOKEN"
curl --fail http://127.0.0.1:8000/graph/engine/analysis \
  -H "Authorization: Bearer $ACCESS_TOKEN" -H 'Content-Type: application/json' \
  --data "{\"operation\":\"metrics\",\"account_id\":\"$ACCOUNT_ID\",\"hops\":2}"
```

Without authentication both graph engine routes return 401. A cross-tenant account returns 404; out-of-range hops, nodes, results or time windows return 422.

Automated checks from `backend`:

```sh
python -m pytest -q
GRAPH_HTTP_SOCKET_TEST=1 python -m pytest -q tests/test_graph_engine_http.py::test_actual_http_transport
```

The opt-in smoke test opens a temporary localhost socket and verifies provider health, metrics and unauthenticated denial over real TCP/HTTP. It uses explicitly mocked synthetic source/auth fixtures; it does not assert real Supabase connectivity. Normal HTTP contract tests execute the real engine with tenant-specific fixtures and cover canonical output, filters, caps/timezones, isolation, missing accounts, truncation, freshness and currency handling.

From `frontend`, run `node --test tests/*.test.cjs`, the normal lint/build checks, and inspect `/graph` in live mode. The frontend regression test verifies that the canvas client calls the new endpoint with all filter values and consumes the compatibility projection.

## Explicit synthetic browser fixture

For UI verification without Supabase credentials, run `PYTHONPATH=. python tests/serve_graph_fixture.py --port 8011 --frontend-origin http://127.0.0.1:3103` from `backend`. This **test-only** server binds localhost and serves three synthetic cycle accounts through the actual graph engine routes. Source reads, session identity, account search and shell summaries are fixture data. It is never imported into the production app.

Start a separate frontend with `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8011` on port 3103. First complete the existing company SSO seeded-demo sign-in so `tracelayer.demo-state` contains a valid authenticated shape. Then set `localStorage['tracelayer.live-session']` to the synthetic session JSON printed by the fixture and reload `/graph`. This exercises the actual live React canvas → authenticated HTTP → request-local NetworkX → canonical output path. No Supabase Auth interception is required; the test token expires in 2100. The default account is `FIXTURE-1`, with three cycle edges; change direction/rails, switch to table view and select an edge. Verify a successful `POST http://127.0.0.1:8011/graph/engine/analysis` rather than intercepting its response. Clear the fixture browser session and stop the fixture afterward.
