# Agent-layer contracts

This page is for the backend, ML and RAG teammates who build the services the agents call. Each
section is one `typing.Protocol` from
[`tracelayer_agents/interfaces/services.py`](../tracelayer_agents/interfaces/services.py). The models
are in [`tracelayer_agents/schemas.py`](../tracelayer_agents/schemas.py), and their docstrings have the full invariants.

**How to plug in.** Implement the methods, which can be a class or any object with them, then pass the
implementations to the facade:

```python
from tracelayer_agents import api
from tracelayer_agents.interfaces import Services

api.configure(Services(behavior=MyBehaviorScorer(), policy=MyRetriever()))  # the rest use mocks
```

**Rules that apply everywhere**
- Methods are **sync** for now. The agents call them in a worker thread, so blocking code is fine.
  If you need `async def`, tell us before writing it.
- Return the Pydantic models, or a dict that validates against them. Unknown fields are rejected so
  contract drift fails loudly. The only exception is `RetrievedChunk`, which ignores extra columns
  such as `embedding`.
- Money is a decimal **string** in JSON (`"9500.00"`). Floats are rejected. Timestamps are ISO 8601
  with a timezone, and naive datetimes are rejected.
- Score dicts are keyed by **real `transaction_id`s**, and a higher score means riskier.
- Never use AMLSim scenario or typology labels (`is_sar`, alert IDs, typology names) as model features.

Every JSON example below is validated against the real models by `tests/test_schemas.py`.

## Backend teammate

### `IngestionService.ingest(payload: bytes | str, source_asset_id: str) -> IngestionResult`
Loads one file and is idempotent per checksum. `accepted_count + rejected_count` must equal `row_count`.
`ingestion_run_id` is PRD `ingestion_runs.run_id`.

<!-- example: IngestionResult -->
```json
{
  "ingestion_run_id": "run_20261017_001",
  "transaction_batch_id": "batch_001",
  "source_asset_id": "amlsim_transactions_v1",
  "file_id": "file_7f3c",
  "status": "partial",
  "row_count": 1000,
  "accepted_count": 997,
  "rejected_count": 3,
  "started_at": "2026-10-17T18:00:00Z",
  "completed_at": "2026-10-17T18:00:04Z"
}
```

### `QualityService.run_contract(batch_id: str) -> QualityReport`
Any `critical` issue means `status="fail"`, and warnings only means `"qualified"`. Issues are governance
findings and are never risk signals.

<!-- example: QualityReport -->
```json
{
  "transaction_batch_id": "batch_001",
  "contract_version": "txn_contract_v1",
  "status": "qualified",
  "rows_checked": 1000,
  "checked_at": "2026-10-17T18:00:05Z",
  "issues": [
    {
      "check_id": "chk_0001",
      "rule_id": "currency_iso4217",
      "asset_id": "amlsim_transactions_v1",
      "severity": "warning",
      "affected_rows": 3,
      "sample_record_ids": ["row_17", "row_204", "row_811"],
      "owner_role": "data_owner",
      "steward_role": "data_steward",
      "remediation_status": "open",
      "field_name": "currency",
      "issue_type": "invalid_value"
    }
  ]
}
```

### `GraphService.build_snapshot(batch_id: str) -> GraphSnapshot`
The graph is time-respecting, and the same batch and feature version always give the same `graph_snapshot_id`.

<!-- example: GraphSnapshot -->
```json
{
  "graph_snapshot_id": "snap_batch_001_fv1",
  "transaction_batch_id": "batch_001",
  "feature_schema_version": "fv1",
  "node_count": 120,
  "edge_count": 997,
  "window_start": "2026-09-01T00:00:00Z",
  "window_end": "2026-09-30T23:59:59Z",
  "built_at": "2026-10-17T18:00:07Z"
}
```

### `GraphService.subgraph(tx_ids: list[str], hops: int) -> SubgraphJSON`
`hops` must be between 1 and 3. Queries are bounded and never generated as SQL or Cypher.

<!-- example: SubgraphJSON -->
```json
{
  "graph_snapshot_id": "snap_batch_001_fv1",
  "nodes": [{"account_id": "A1"}, {"account_id": "A2", "account_type": "checking"}],
  "edges": [
    {
      "transaction_id": "T100",
      "origin_account_id": "A1",
      "destination_account_id": "A2",
      "amount": "9500.00",
      "currency": "USD",
      "timestamp": "2026-09-03T14:12:00Z"
    }
  ]
}
```

### `GraphService.get_transactions(tx_ids: list[str]) -> list[EvidenceTransaction]`
Returns the full canonical rows for evidence bundles, one per ID in request order, with the real
`source_row_ref`. It raises `KeyError` on any unknown ID rather than returning partial evidence.
Added by the agents workstream.

<!-- example: EvidenceTransaction -->
```json
{
  "transaction_id": "T100",
  "source_row_ref": "row_0017",
  "timestamp": "2026-09-03T14:12:00Z",
  "amount": "9500.00",
  "currency": "USD",
  "channel": "wire",
  "direction": "outbound",
  "origin_account_id": "A1",
  "destination_account_id": "A2",
  "transaction_status": "posted"
}
```

### `CaseRepository` and `AuditRepository`
These store data on Supabase, and agents ship in-memory versions. `CaseRepository` covers `save_alerts`,
`get_alert`, `save_evidence`, `get_evidence`, `create_case`, `get_case`, `update_case` and `save_draft`
(keeps every draft version). `AuditRepository` is **append-only**: `append(event)` stores an event whose
`prev_hash` and `hash` the agents already computed, and it must reject the event if `prev_hash` is not
`last_hash()`. The PRD `audit_events` table needs `prev_hash` and `hash` columns for this, which is an open question.

<!-- example: AuditEvent -->
```json
{
  "event_id": "evt_0002",
  "actor_id": "user_42",
  "actor_role": "reviewer",
  "action": "case.decision.escalate",
  "object_type": "case",
  "object_id": "case_001",
  "before_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "after_hash": "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb",
  "prev_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "hash": "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb",
  "model_versions": {"xgboost": "xgb_v3"},
  "prompt_version": "narrative_v1",
  "timestamp": "2026-10-17T19:30:00Z",
  "reason": "Funnel pattern into A2 across 6 accounts; needs senior review.",
  "metadata": {"draft_attempts": 1}
}
```

### `AccessPolicy.can_access(actor_id: str, actor_role: str, case_id: str, tenant_id: str | None) -> bool`
New in PRD 8.2. This is your auth/RLS answer to "may this actor read this case?". Pass it as
`Services(access=...)`.
- The chat asks before `start_chat`, before every `send_message`, `update_chat_context`, pin and
  `get_chat`, and before every tool call. Access revoked mid-chat stops the next call.
- `False` → the facade returns `status="denied"` with no case data, and writes a
  `chat.request_denied` audit event (`reasons=access_denied`, never the records).
- The call is synchronous and on the request path, so keep it fast (a cached membership lookup).
- It must not raise for an unknown case: return `False`. An exception is treated as `False`
  (fail closed).
- `tenant_id` comes from your auth and is passed through `start_chat(..., tenant_id=...)`. It is
  `None` in a single-tenant deployment.
- The mock `MockAccessPolicy(denied_case_ids=..., denied_tenant_ids=...)` allows everything else.

```python
class SupabaseAccessPolicy:
    def can_access(self, actor_id, actor_role, case_id, tenant_id) -> bool:
        return case_id in membership_cache.cases_for(actor_id, tenant_id)
```

## ML teammate

### `GraphRiskScorer.score(snapshot_id: str) -> DetectorOutput`
### `BehaviorScorer.score(snapshot_id: str) -> list[DetectorOutput]`
Each detector returns one `DetectorOutput` with a unique `detector_name`. Keys of `top_factors` and
`typology_flags` must also be keys of `scores`. If a model isn't available, return
`"status": "unavailable"` with empty scores. Don't raise, and don't fake scores.

<!-- example: DetectorOutput -->
```json
{
  "detector_name": "xgboost",
  "model_version": "xgb_v3",
  "status": "ok",
  "scores": {"T100": 0.91, "T101": 0.12},
  "top_factors": {
    "T100": [
      {"name": "in_out_ratio_24h", "kind": "feature", "value": 0.98, "contribution": 0.31},
      {"name": "rapid_in_out", "kind": "rule", "value": "true"}
    ]
  },
  "feature_snapshot_id": "snap_batch_001_fv1",
  "typology_flags": {"T100": ["rapid_in_out"]}
}
```

### `RankFusion.fuse(outputs: dict[str, DetectorOutput]) -> FusionResult`
Fit it on the validation split and record the method and coefficients. Never use hand-picked weights.
The mock reports `method="mock_rank_mean"`.

<!-- example: FusionResult -->
```json
{
  "method": "logistic_stacker",
  "coefficients": {"graphsage": 0.8, "xgboost": 1.4, "isolation_forest": 0.3},
  "composite": {"T100": 0.88, "T101": 0.09},
  "model_run_id": "mr_2026_10_17_01"
}
```

### `FlowInvestigator.investigate(alert: Alert, snapshot_id: str) -> FlowFindings`
Return only real transaction IDs. Every pattern's `transaction_ids` must appear in `evidence_tx_ids`,
paths must follow time order, and search depth is bounded.

<!-- example: FlowFindings -->
```json
{
  "alert_id": "alrt_001",
  "graph_snapshot_id": "snap_batch_001_fv1",
  "fan_in": [
    {
      "account_ids": ["A3", "A4", "A5", "A2"],
      "transaction_ids": ["T097", "T098", "T099"],
      "total_amount": "28400.00",
      "currency": "USD",
      "window_start": "2026-09-03T09:00:00Z",
      "window_end": "2026-09-03T13:00:00Z"
    }
  ],
  "pass_through": [
    {"account_ids": ["A2", "A9"], "transaction_ids": ["T100"], "total_amount": "9500.00", "currency": "USD"}
  ],
  "evidence_tx_ids": ["T097", "T098", "T099", "T100"]
}
```

## RAG teammate

### `PolicyRetriever.search(query: str, filters: dict, top_k: int) -> list[RetrievedChunk]`
Return at most `top_k` chunks from the curated corpus only, sorted by descending score. `excerpt` is
verbatim source text, and `chunk_id` must stay stable across re-indexing. Return passages only, no
generated answers. PRD `policy_chunks` column names (`source_title`, `source_url`, `text`) are accepted
as aliases. The supported `filters` keys are still open, so propose them.

<!-- example: RetrievedChunk -->
```json
{
  "chunk_id": "ffiec_bsa_aml_ctr_004",
  "source_title": "FFIEC BSA/AML Examination Manual",
  "section": "Currency Transaction Reporting",
  "source_url": "https://bsaaml.ffiec.gov/manual",
  "text": "<placeholder: verbatim passage from the indexed manual section>",
  "score": 0.83,
  "effective_date": "2021-02-25",
  "indexed_at": "2026-10-17T12:00:00Z",
  "embedding": [0.01, -0.02]
}
```

## How the investigation chat uses these services

The chat (`api.send_message`) calls these methods read-only, inside one case's scope, each call
capped at **5 s** and its output at 50 rows. The only new Protocol is `AccessPolicy` (above):

- `CaseRepository.get_case / get_alert / get_evidence`: once per chat, to bind it to the case.
- `GraphService.subgraph(tx_ids, hops)`: hops 1–3 around the case evidence; edges outside the case
  window (evidence span ± 7 days) are dropped. Please keep it fast (it runs on most chat turns).
- `GraphService.get_transactions(tx_ids)`: at most 50 ids, all from the case scope.
- `GraphRiskScorer.score(snapshot_id)` / `BehaviorScorer.score(snapshot_id)`: re-read for the
  alert's top factors (`model_explanation`). **ML:** if scoring a snapshot takes more than a
  second or two, please cache outputs per `snapshot_id`; a timeout shows as "not checked".
- `PolicyRetriever.search(query, {}, top_k)`: the analyst's query (≤ 300 chars) or the alert's
  typology query.

Two chat tools are mocks over fixtures until a real service exists:
- `similar_cases` (`mocks/fixtures/case_library.json`, labelled `synthetic_case_library_v1`):
  - Ten synthetic closed cases, filtered by typology and closing time.
  - Returns the top 5, scored as the plain mean of motif overlap, rail match, flow-shape match,
    graph-feature similarity and text overlap. The equal weights are a placeholder.
  - Each result carries its prior disposition and "Similarity is not proof; prior decisions can
    be wrong."
  - It never returns another real case's data.
- `shared_token_search` (`mocks/fixtures/shared_tokens.json`): devices, IP addresses and payment
  tokens shared by at least two of the case's accounts. Accounts outside the case are only
  counted. A real implementation needs a backend source for these links.

No chat tool runs model-authored SQL or Cypher. A free-text plan is only a list of allowlisted
`ToolCall`s, and each one's args must validate against that tool's `extra="forbid"` input model. Chat models (`ChatContext`, `ChatTurn`, `ChatClaim`, `ChatGap`,
`ToolResult`, …) are in `schemas.py` under "investigation chat"; the facade and JSON examples are
in `INTEGRATION.md`.
