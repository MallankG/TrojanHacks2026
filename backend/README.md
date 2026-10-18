# TraceLayer backend (FastAPI + Supabase)

Owns ingestion, data quality, lineage, case workflow, audit and the storage/read APIs for the
model and agent services. It does **not** train models or call LLMs. Those workers write their
results here through the API.

## Run

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate      # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                  # fill SUPABASE_SERVICE_ROLE_KEY and SERVICE_API_KEY
uvicorn main:app --reload
```

Interactive docs: http://127.0.0.1:8000/docs. Tests: `python -m pytest -q`.

Database schema lives in `../supabase/migrations` (already applied to the project).

### Test the full system with the frontend

```bash
# test users in the demo-bank tenant (Supabase Auth, email confirmed)
python -m scripts.create_user --email analyst@demo.test  --password 'choose-a-long-one' --role analyst
python -m scripts.create_user --email reviewer@demo.test --password 'choose-a-long-one' --role reviewer
python -m scripts.create_user --email steward@demo.test  --password 'choose-a-long-one' --role data_steward
```

Then sign in to the frontend with one of these emails (`frontend/README.md`) and upload `sample_data/demo_transactions.csv` from Data operations → Upload CSV batch.
It has a 5-hop layering chain, structuring-like cash deposits, a 3-account cycle, benign payroll and 7 malformed
rows (bad timestamp, unknown currency, negative amount, duplicate ID, unknown channel, missing account, no timezone).

## Auth

| Caller | How | Role |
|---|---|---|
| Browser user | `Authorization: Bearer <supabase access token>` | from `profiles.role` (admin, investigator, analyst, reviewer, data_steward) |
| Model / agent worker | `X-Service-Key: <SERVICE_API_KEY>` + `X-Tenant-Id: <tenant uuid>` (+ optional `X-Service-Name`) | `service` |

New sign-ups have no tenant and see nothing until an admin provisions them (`POST /admin/users` or
`PATCH /admin/users/{id}`). Workers can never record human review decisions.

Every write forwards the acting user to Postgres (`X-Actor-Id`), so audit triggers attribute changes correctly.

## Endpoints

| Area | Endpoints |
|---|---|
| Ingestion | `GET /ingestion/schema`, `POST /ingestion/preview-mapping`, `POST /ingestion/upload`, `GET /ingestion/runs[/{id}[/rows\|/issues]]`, `GET /ingestion/source-files/{id}/download-url` |
| Data quality | `GET /data-quality/issues`, `PATCH /data-quality/issues/{id}`, `GET /data-quality/source-rows/{id}` (row, issues and downstream impact) |
| Accounts & transactions | `GET /accounts[/{id}]`, `GET /counterparties`, `GET /transactions[/{id}]` |
| Graph | `GET /graph/accounts/{id}/neighborhood`, `GET /graph/paths` (time-respecting, bounded) |
| Model runs & signals | `POST/GET /model-runs`, `GET/PATCH /model-runs/{id}`, `POST /features`, `POST/GET /model-runs/{id}/signals` |
| Alerts | `POST /alerts` (publish batch), `GET /alerts` (queue; defaults to the latest completed ranker run), `GET/PATCH /alerts/{id}`, `GET /alerts/{id}/timeline`, `GET /alerts/{id}/graph` |
| Cases | `POST/GET /cases`, `GET/PATCH /cases/{id}`, `POST /cases/{id}/alerts`, `POST /cases/{id}/evidence`, `GET /cases/{id}/evidence-bundle`, `GET /cases/{id}/evidence-passport`, `POST/GET /cases/{id}/decisions`, `GET /cases/{id}/export-url` |
| Evidence passport | `POST /evidence-passports/verify` (stateless; recomputes section and package hashes) |
| Agent integration | `POST /cases/{id}/agent-runs`, `GET/PATCH /agent-runs/{id}`, `POST/GET /cases/{id}/narratives`, `GET /narratives/{id}`, `PUT /narratives/{id}/verification` |
| Policy (RAG) | `POST/GET /policy/documents`, `GET /policy/documents/{id}`, `GET /policy/chunks/{id}`, `POST /policy/search` |
| Lineage & audit | `GET /lineage/transactions/{id}`, `GET /lineage/alerts/{id}`, `GET /audit/events` |
| Users | `GET /me`, `GET/POST /admin/users`, `PATCH /admin/users/{id}` |

## Integration contract for the model worker

1. `POST /model-runs` with `model_kind` one of `rule_engine | supervised | anomaly | graph_model | ranker` → `id`.
2. Optional: `POST /features` (point-in-time features, `as_of_timestamp` = information cutoff).
3. `POST /model-runs/{id}/signals`, one row per rule/model output. Pass `client_ref` to map the returned ids.
4. `PATCH /model-runs/{id}` with `status=completed`, `metrics` (PR-AUC, precision@K, …) and an optional
   `artifact_path` (`<tenant_id>/...` in the `model-artifacts` bucket).
5. Ranking: create a `ranker` run, then `POST /alerts` with `model_run_id`, the alerts, `signal_ids` and the
   supporting `transactions`/`accounts`. The backend computes data-quality caveats per alert. Complete the
   ranker run so the queue switches to it.

Read the data with `GET /transactions` (paginate with `limit`/`offset`), or read Postgres directly
with your own read-only credentials.

## Integration contract for the agent service

1. `POST /cases/{id}/agent-runs` → `id`; report progress with `PATCH /agent-runs/{id}` (`status`, `current_step`,
   `append_steps`). The UI can subscribe to `agent_runs` via Supabase Realtime.
2. `GET /cases/{id}/evidence-bundle`: alerts, signals, transactions, accounts, DQ issues and evidence, all with
   stable ids. Treat free text (descriptions) as untrusted data.
3. Retrieval: `POST /policy/search` with `query_text` and/or a 768-dim `query_embedding` (nomic-embed-text).
   Load the corpus with `POST /policy/documents` (chunks and optional embeddings). `shared=true` (guidance
   visible to every tenant) is accepted only from service workers; re-posting a `source_url` revises it in place.
4. `POST /cases/{id}/narratives` with `content`, structured `claims` (fact/hypothesis/unknown with referenced
   transaction/account ids and cited chunk ids), `citations` and `llm_model`/`prompt_version`.
5. `PUT /narratives/{id}/verification` with the verifier result (`passed|flagged|failed`) and per-claim results.
6. A human reviewer then records `POST /cases/{id}/decisions` (accept/edit/reject/escalate and disposition).

## Evidence passport

`GET /cases/{id}/evidence-passport` returns a `tracelayer.evidence-passport/v1` document: eight sections (source
references, data quality, model, feature contributions, graph paths, policy passages, copilot claims, human
decisions), a SHA-256 hash per section over canonical JSON (sorted keys, compact, UTF-8) and a package hash over the
header plus the section hashes. It is not signed. `POST /evidence-passports/verify` names the sections that changed.
The frontend builds and verifies passports in the same format (`frontend/src/lib/domain/passport.ts`).

## Policy corpus note

FFIEC's BSA/AML manual site is behind a CAPTCHA, so it can't be scraped. Paste section text from the
official pages into `POST /policy/documents` with the real `source_url`. FinCEN pages such as
https://www.fincen.gov/resources/statutes-regulations/guidance/frequently-asked-questions-regarding-suspicious-activity
can be fetched normally.

## PRD 20.2 — data governance

The workspace at `/governance/superpowers` covers the twelve capabilities with fixed synthetic
examples and an authenticated catalog mode. Apply
`supabase/migrations/20261018000001_governance_superpowers.sql` before using catalog persistence.
This change does not apply the migration to a hosted project automatically.

- `GET /governance/demo?scenario=healthy|drift`: fixed public synthetic examples, no tenant reads or writes.
- `POST/GET /governance/products`, `GET /governance/products/{id}`: register a documented product and inspect its immutable event history (latest 100). Source-system names are unique within a tenant.
- `POST /governance/products/{id}/assessments`: evaluate up to 500 rows and 500 baseline rows; provide the registered `purpose`, rows, optional baseline, ledger totals and source checksum. Saves a receipt, component trust scores, quarantine reasons, drift suggestions, registered dependency impact and correlated incidents. Raw values/documents are excluded from the saved receipt.
- `POST /governance/products/{id}/reviews`: append owner-assigned access requests, exceptions, waivers, mapping/remediation reviews and incident notes/resolutions. Every record expires within 90 days. Only admin/reviewer can approve/reject, referencing a saved `request_id`; expiry cannot be extended and concurrent terminal decisions are constrained by the database.
- `POST /governance/products/{id}/steward-query`: preview (`execute=false`, default) then execute an allowlisted read. Supported questions: `which alerts depend on amount?` and `why was this row quarantined?` (requires `row` and `assessment_id`). Tenant scoped, capped at 100 results, execution audited; no generated SQL.

Registration/assessment requires admin or data_steward. All live endpoints require a provisioned user.
The prototype frontend sign-in is separate: expand “Connect a governed catalog” and supply a Supabase
access token, kept only in page memory. Configure the API URL using `frontend/.env.example`.

Set `GOVERNANCE_CONTRACTS_ENABLED=true` **after applying the migration** to enforce registered
source contracts in the CSV ingestion path. Checks run on original fields before canonical writes;
failures retain the source row and create an error DQ issue. Unregistered source systems keep the
existing canonical checks. Missing timezone, unknown enum, bounds, reference and freshness failures
are quarantined rather than silently normalized by the canonical parser.

Prototype boundaries: metadata dependencies are explicitly registered, not auto-discovered from the
whole database. Impact/retention estimates cannot cover unregistered external copies. PII discovery
is heuristic, receipts mask values, and a supplied checksum is not independently verified. Distribution
drift uses a median-ratio heuristic (10 observations minimum). Reconciliation compares accepted totals
per currency with supplied authorized ledger totals; missing legs, reversals and balances require
additional feeds. Approvals record decisions; they do not automatically grant access, remap fields,
execute reprocessing, delete records or claim external erasure. Incident notes preserve original
assessments and root cause remains unconfirmed until reviewed.

## PRD 20.10 — enterprise investigation workspace

Case collaboration, attribute-based case access, versioned evidence exports, SLO telemetry,
Ed25519 release tooling, declared residency boundaries and recipient-filtered handoffs are
implemented behind `ENTERPRISE_ENABLED`. Apply the enterprise migration and provision user
attributes/case policies before rollout. Keep `CASE_AUTHORIZATION_ENABLED=true` if the UI is
later disabled. See [the enterprise runbook](../docs/enterprise_runbook.md) for setup, API
contracts, tests and prototype boundaries. Model/data monitoring pointers and snapshot formats
are described in [the drift guide](../docs/drift_monitoring.md).

Source-contract corrections use `POST /governance/products/{product_id}/replacements`
with `{ "product": <complete corrected product>, "reason": "reviewed correction reason" }`.
A steward must supply the same source system and a new contract version. The operation
atomically appends an immutable revision and remediation receipt; concurrent or stale
replacements return 409. The highest source revision governs future ingestion, and each
run pins its exact product ID. Historical products, receipts and run pins remain unchanged.
Apply `20261018000003_source_contract_versions.sql` before enabling this operation.

Run the contract migration regression suite against disposable PostgreSQL with
`node scripts/test-contract-db.mjs /path/to/@electric-sql/pglite/dist/index.js`
from `backend/` (omit the runtime path when PGlite is locally installed). It applies
the actual tenancy, audit and governance SQL with only the ingestion table stubbed,
and checks replacement/audit atomicity, stale edits, immutable history and pins,
cross-tenant isolation, and RPC execution grants. The SQL assertions also live in
`supabase/tests/contract_versions.sql` for disposable migrated databases.
