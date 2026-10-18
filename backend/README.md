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
| Cases | `POST/GET /cases`, `GET/PATCH /cases/{id}`, `POST /cases/{id}/alerts`, `POST /cases/{id}/evidence`, `GET /cases/{id}/evidence-bundle`, `POST/GET /cases/{id}/decisions`, `GET /cases/{id}/export-url` |
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

## Policy corpus note

FFIEC's BSA/AML manual site is behind a CAPTCHA, so it can't be scraped. Paste section text from the
official pages into `POST /policy/documents` with the real `source_url`. FinCEN pages such as
https://www.fincen.gov/resources/statutes-regulations/guidance/frequently-asked-questions-regarding-suspicious-activity
can be fetched normally.
