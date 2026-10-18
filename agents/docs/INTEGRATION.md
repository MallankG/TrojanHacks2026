# Integrating the agents package

This page is for the backend teammate wiring TraceLayer's FastAPI app to the agents, and for the
frontend teammate consuming those endpoints. The backend imports **one module**,
`tracelayer_agents.api` (also re-exported from `tracelayer_agents`). Everything else in the
package is internal.

## 1. Install

From `backend/`, in the backend's virtualenv (Python **3.11+**):

```bash
pip install -e ../agents
```

The package brings LangGraph, Pydantic v2, httpx, NetworkX and pandas. It works with the FastAPI
0.118 / Pydantic v2 already in `backend/requirements.txt`.

**Environment.** The agents read their settings from the backend process env. Copy
[`agents/.env.example`](../.env.example) into the backend's `.env`:

| var | default | meaning |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `template` = deterministic output, no LLM, no network |
| `OLLAMA_HOST` | `http://localhost:11434` | |
| `OLLAMA_MODEL` | `qwen3.5:9b` | team model; empty = first model Ollama lists |
| `LLM_TIMEOUT_S` | `90` | budget for the Ollama part of one LLM call, then the template answers |
| `TRACELAYER_LLM_NODE_TIMEOUT_S` | `LLM_TIMEOUT_S + 30` | must exceed `LLM_TIMEOUT_S` |
| `LLM_MAX_CALLS_PER_RUN` | `6` | |
| `TRACELAYER_USE_MOCKS` | unset | `1` forces every service to its mock |

**Ollama.** `ollama pull qwen3.5:9b` and keep `ollama serve` running. If Ollama is down or slow the
agents fall back to the deterministic template, so every endpoint still works (useful for demos and
CI: set `LLM_PROVIDER=template`).

## 2. Configure at startup

Call `api.configure()` once, in the FastAPI lifespan. Pass the real implementations you have; every
service left as `None` falls back to its mock (one warning in the log per mocked service).

```python
# backend/main.py
from contextlib import asynccontextmanager

from fastapi import FastAPI
from tracelayer_agents import Services, api


@asynccontextmanager
async def lifespan(app: FastAPI):
    api.configure(Services(
        # cases=SupabaseCaseRepository(db),    # add each one as it exists
        # audit=SupabaseAuditRepository(db),
        # policy=PgVectorPolicyRetriever(db),
    ))
    yield


app = FastAPI(lifespan=lifespan)
```

`configure` builds the services once and keeps them, so the case draft (`start_case`) and the
decision (`resume_case`) always share the same repositories. Calling it again replaces them. If
it's never called, the first facade call configures all mocks.

**Services and owners.** Signatures, invariants and JSON examples are in
[CONTRACTS.md](CONTRACTS.md).

| `Services` field | Protocol | implemented by |
|---|---|---|
| `ingestion` | `IngestionService` | backend |
| `quality` | `QualityService` | backend |
| `graph` | `GraphService` (`build_snapshot`, `subgraph`, `get_transactions`) | backend |
| `cases` | `CaseRepository` | backend (Supabase `cases`, `case_evidence`) |
| `audit` | `AuditRepository` | backend (Supabase `audit_events` + `prev_hash`/`hash`) |
| `graph_risk` | `GraphRiskScorer` (GraphSAGE) | ML |
| `behavior` | `BehaviorScorer` (XGBoost, IsolationForest, rules) | ML |
| `fusion` | `RankFusion` | ML |
| `flows` | `FlowInvestigator` | ML |
| `policy` | `PolicyRetriever` | RAG |

The data mocks share one in-memory store, so swap in the data services (`ingestion`, `quality`,
`graph`, scorers, `flows`) together, not one at a time.

## 3. Facade

| function | returns | statuses |
|---|---|---|
| `await run_analysis(batch_ref, mode="strict")` | `AnalysisResult` | `ok`, `halted`, `error` |
| `await start_case(alert_id)` | `CaseDraftResult` | `ok`, `error` |
| `await resume_case(case_id, decision)` | `CaseDecisionResult` | `ok`, `rejected`, `error` |
| `await copilot_query(question, alert_id=None)` | `CopilotResult` | `ok`, `error` |
| `await get_alert_graph(alert_id, hops=1)` | `CytoscapeGraph` | `ok`, `error` |
| `get_trace(trace_id)` | `list[TraceEvent]` | `[]` if unknown |
| `verify_audit_chain()` | `AuditVerification` | `ok: bool` |
| `await start_chat(case_id, actor_id, actor_role, tenant_id=None)` | `ChatResult` | `ok`, `denied`, `error` |
| `await send_message(chat_id, text=None, chip=None)` | `ChatTurnResult` | `ok`, `needs_clarification`, `denied`, `error` |
| `update_chat_context(chat_id, selected_tx_ids=None, edge_ids=None, confirm=False)` | `ContextChangeResult` | `preview`, `ok`, `rejected`, `denied`, `error` |
| `pin_evidence(chat_id, ids)` / `unpin_evidence(chat_id, ids)` | `PinResult` | `ok`, `rejected`, `error` |
| `get_chat(chat_id)` | `ChatResult` | `ok`, `denied`, `error` |
| `record_feedback(chat_id, turn_id, helpful, note=None)` | `FeedbackResult` | `ok`, `error` |

- The functions **never raise**. Failures come back as the result with `status` and a short `error`
  (no stack traces, no prompt text) plus the trace so far.
- `halted` is not an error. It means the strict governance gate stopped the run on bad data
  (`governance_status="fail"`, no alerts).
- `rejected` means the decision was invalid or the role isn't allowed to take it (e.g. an analyst
  dismissing a case whose draft `needs_revision`). The case stays paused, so a valid decision can
  be submitted.
- **The agents enforce RBAC.** `Decision.actor_role` (`analyst` | `supervisor`) and `actor_id` must
  come from your auth, never from the request body.
- **Case access.** Pass an `AccessPolicy` (`Services(access=...)`, see CONTRACTS.md) so the chat
  can ask your auth/RLS whether an actor may read a case. Without one the mock allows everything.
  `denied` → `403`; the body never contains the case's records.
- Every result is a Pydantic model you can use as `response_model` directly. Money is a decimal
  string (`"4850.00"`), timestamps are ISO 8601 UTC.
- `batch_ref` is passed to `IngestionService.ingest` as the payload (file bytes or a storage
  path). With the mocks it's a fixture name: `fan_out`, `pass_through`, `cycle`, `bad_batch`.

## 4. Router snippet

Copy into `backend/` (for example `backend/routers/agents.py`) and adapt `current_user` to your
auth. It's a suggestion: the agents package doesn't ship backend files.

```python
from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from tracelayer_agents import (
    AnalysisResult,
    AuditVerification,
    CaseDecisionResult,
    CaseDraftResult,
    ContextChangeResult,
    CopilotResult,
    CytoscapeGraph,
    Decision,
    TraceEvent,
    api,
)

router = APIRouter(prefix="/api/v1")


class User(BaseModel):  # replace with your auth's user model
    id: str
    role: Literal["analyst", "supervisor"]
    tenant_id: str | None = None


def current_user() -> User:  # replace with your auth dependency (Supabase JWT, ...)
    raise NotImplementedError


class AnalysisRunBody(BaseModel):
    batch_ref: str
    mode: Literal["strict", "exploratory"] = "strict"


class CaseCreateBody(BaseModel):
    alert_id: str


class DecisionBody(BaseModel):  # no role or actor here: those come from auth
    action: Literal["escalate", "dismiss", "request_more_data"]
    reason: str
    edited_summary: str | None = None


class CopilotBody(BaseModel):
    question: str
    alert_id: str | None = None


class ChatContextBody(BaseModel):
    selected_tx_ids: list[str] = []
    edge_ids: list[str] = []
    confirm: bool = False


# ok/halted/preview/needs_clarification -> 200, rejected -> 422, denied -> 403, error -> 400.
HTTP_CODES = {"ok": 200, "halted": 200, "preview": 200, "needs_clarification": 200,
              "rejected": 422, "denied": 403, "error": 400}


@router.post("/analysis/run", response_model=AnalysisResult)
async def run_analysis(body: AnalysisRunBody, response: Response, user: User = Depends(current_user)):
    result = await api.run_analysis(body.batch_ref, body.mode)
    response.status_code = HTTP_CODES[result.status]
    return result


@router.get("/alerts/{alert_id}/graph", response_model=CytoscapeGraph)
async def alert_graph(alert_id: str, response: Response, hops: int = 1,
                      user: User = Depends(current_user)):
    result = await api.get_alert_graph(alert_id, hops)
    response.status_code = HTTP_CODES[result.status]
    return result


@router.post("/cases", response_model=CaseDraftResult)
async def create_case(body: CaseCreateBody, response: Response, user: User = Depends(current_user)):
    result = await api.start_case(body.alert_id)  # LLM call: 15-45 s, see below
    response.status_code = HTTP_CODES[result.status]
    return result


@router.post("/cases/{case_id}/decision", response_model=CaseDecisionResult)
async def decide(case_id: str, body: DecisionBody, response: Response,
                 user: User = Depends(current_user)):
    decision = Decision(
        action=body.action,
        reason=body.reason,
        edited_summary=body.edited_summary,
        actor_role=user.role,  # from auth: the agents enforce who may do what
        actor_id=user.id,
    )
    result = await api.resume_case(case_id, decision)
    response.status_code = HTTP_CODES[result.status]
    return result


@router.post("/copilot/query", response_model=CopilotResult)
async def copilot(body: CopilotBody, response: Response, user: User = Depends(current_user)):
    result = await api.copilot_query(body.question, body.alert_id)  # LLM call
    response.status_code = HTTP_CODES[result.status]
    return result


@router.post("/chats/{chat_id}/context", response_model=ContextChangeResult)
async def chat_context(chat_id: str, body: ChatContextBody, response: Response,
                       user: User = Depends(current_user)):
    # Chats keep their own actor (from start_chat); also check here that `user` owns the chat.
    result = api.update_chat_context(chat_id, body.selected_tx_ids, body.edge_ids, body.confirm)
    response.status_code = HTTP_CODES[result.status]
    return result


@router.get("/runs/{trace_id}/trace", response_model=list[TraceEvent])
async def trace(trace_id: str, user: User = Depends(current_user)):
    return api.get_trace(trace_id)


@router.get("/audit/verify", response_model=AuditVerification)
async def audit_verify(user: User = Depends(current_user)):
    return api.verify_audit_chain()
```

Note: building `Decision(...)` with a blank `reason` raises a Pydantic `ValidationError` in your
route (FastAPI turns it into a 500). Either validate `reason` in `DecisionBody`, or pass a dict
instead (`api.resume_case(case_id, {...})`): the facade then returns `status="rejected"` with the
reason.

**Latency.** With `qwen3.5:9b` on a laptop, one LLM call takes about **15–45 s**. `POST /cases`
(narrative draft, up to 2 drafts if the verifier finds problems) and `POST /copilot/query` call the
LLM. Each call is capped at `LLM_TIMEOUT_S` (90 s) before the template answers, so a case draft
can take up to ~4 minutes in the worst case. Either:
- give those two endpoints a long timeout (proxy, `fetch` in the frontend, ≥ 240 s), or
- run them as a FastAPI `BackgroundTasks` job: return a job id immediately, store the result when
  it's done, and let the frontend poll.

Everything else returns in milliseconds.

## 5. Request and response examples

Generated by running the facade on the `fan_out` and `bad_batch` mock fixtures with
`LLM_PROVIDER=template`. Long lists are cut to one or two items (`…`); ids and hashes differ per run.

### `POST /api/v1/analysis/run`

Request:
```json
{"batch_ref": "fan_out", "mode": "strict"}
```

Response `200`:
```json
{
  "status": "ok",
  "error": null,
  "trace_id": "tr_f5ed07a6267648c0bcf464febd7e5698",
  "governance_status": "pass",
  "halted": false,
  "alerts": [
    {
      "alert_id": "alrt_a812f90d1b25",
      "tx_ids": ["TX-51001"],
      "composite_score": 0.932203,
      "component_scores": {
        "mock_velocity": 0.727273,
        "mock_typology_rules": 0.7,
        "mock_graph_risk": 0.75
      },
      "typology_tags": ["fan_out"],
      "tier": "high",
      "quality_status": "pass",
      "confidence_caveat": null,
      "graph_snapshot_id": "snap_60742743c1f42dee",
      "model_versions": {
        "mock_velocity": "mock-0",
        "mock_typology_rules": "mock-0",
        "mock_graph_risk": "mock-0"
      },
      "rank": 1,
      "model_run_id": null
    }
  ],
  "trace": [
    {
      "trace_id": "tr_f5ed07a6267648c0bcf464febd7e5698",
      "node": "ingest",
      "status": "ok",
      "started_at": "2026-10-17T23:44:32.854285Z",
      "ended_at": "2026-10-17T23:44:32.855859Z",
      "duration_ms": 1.575,
      "input_hash": "64148b193198a3717aae378c3c1bc5032ee40dc0e397ecfb3cbedc55232b3890",
      "output_hash": "f6a036ce4d5a0887e683ced27b3579aa7d05e29e51608b6af38b226f7512454c",
      "error": null
    }
  ]
}
```

Bad data in strict mode (`{"batch_ref": "bad_batch", "mode": "strict"}`), response `200`:
```json
{
  "status": "halted",
  "error": null,
  "trace_id": "tr_b50afb5db6834272a455c616cd9d2c27",
  "governance_status": "fail",
  "halted": true,
  "alerts": [],
  "trace": ["… ingest, governance …"]
}
```

### `GET /api/v1/alerts/{alert_id}/graph?hops=1`

Cytoscape.js elements. `highlighted: true` marks the alert's evidence transactions; `risk` is the
alert's composite score on accounts touching evidence (else `null`); `quality` is the alert's
governance status.

Response `200`:
```json
{
  "status": "ok",
  "error": null,
  "nodes": [
    {"data": {"id": "ACC-3001", "label": "ACC-3001", "risk": 0.932203, "quality": "pass"}},
    {"data": {"id": "ACC-3101", "label": "ACC-3101", "risk": 0.932203, "quality": "pass"}}
  ],
  "edges": [
    {
      "data": {
        "id": "TX-51001",
        "source": "ACC-3001",
        "target": "ACC-3101",
        "amount": "4850.00",
        "timestamp": "2026-09-04T13:05:00Z",
        "tx_id": "TX-51001",
        "highlighted": true
      }
    }
  ]
}
```

Frontend: `cy.add([...graph.nodes, ...graph.edges])`.

### `POST /api/v1/cases`

Request:
```json
{"alert_id": "alrt_a812f90d1b25"}
```

Response `200`. `case_status` is `awaiting_review`, or `needs_revision` when the verifier still
flags claims: show the claims at the indexes in `flagged_claims` as unverified.
```json
{
  "status": "ok",
  "error": null,
  "trace_id": "tr_ca91d5e8d19d456cb721decff2a415f6",
  "case_id": "case_8aa14eea7752",
  "alert_id": "alrt_a812f90d1b25",
  "draft": {
    "summary": "The evidence bundle contains 9 transactions totaling $43,855.00 across 10 accounts, dated 2026-09-04 (a span of 2.27 hours). 1 flow pattern(s) were found around the alert. Deterministic fallback draft (no model synthesis); a reviewer must assess it.",
    "observed_facts": [
      {
        "text": "Transaction TX-51001 moved $4,850.00 from ACC-3001 to ACC-3101 on 2026-09-04.",
        "claim_type": "fact",
        "transaction_ids": ["TX-51001"],
        "account_ids": ["ACC-3001", "ACC-3101"],
        "amounts": ["4850.00"],
        "dates": ["2026-09-04"],
        "citation_chunk_ids": []
      }
    ],
    "uncertainties": [
      "No KYC, customer profile or stated business purpose was provided, so the economic purpose of these transfers is unknown."
    ],
    "follow_up_questions": [
      "What is the stated business purpose of the activity involving ACC-3001?"
    ],
    "citations": ["mock_ffiec_red_flags_003", "mock_cfr_sar_thresholds_004"],
    "evidence_transaction_ids": ["TX-51001", "TX-51002"],
    "provider": "template",
    "model": "template",
    "prompt_version": "narrative_v4",
    "fallback_used": true,
    "sections": {
      "observed_facts": [{"text": "Transaction TX-51001 moved $4,850.00 from ACC-3001 to ACC-3101 on 2026-09-04.",
                          "factual": true, "evidence_ids": ["TX-51001"]}, "…"],
      "model_rule_inferences": [{"text": "The alert's composite score is 0.9322 (tier high, rank 1). Scores rank activity for review; they are not findings of wrongdoing.",
                                 "factual": true, "evidence_ids": ["TX-51001"]}, "…"],
      "analyst_hypotheses": [{"text": "Hypothesis: the fan-out activity is consistent with a fan-out typology. It may also have a legitimate explanation; this requires reviewer assessment.",
                              "factual": false, "evidence_ids": ["TX-51001", "…"]}],
      "contradictory_evidence": [{"text": "9 evidence transactions from ACC-3001 go to 9 recipients on one day with amounts within 10% of each other, which may be consistent with a payroll-like or vendor batch, pending verification.",
                                  "factual": true, "evidence_ids": ["TX-51001", "…"]}],
      "data_gaps": [{"text": "No KYC or customer profile is available to the agents.",
                     "factual": true, "evidence_ids": ["not_available:kyc_profile"]}, "…"],
      "relevant_policy": [{"text": "FFIEC BSA/AML Examination Manual (…) sets out review expectations relevant to this activity.",
                           "factual": true, "evidence_ids": ["mock_ffiec_red_flags_003"]}, "…"],
      "recommended_next_steps": [{"text": "What is the stated business purpose of the activity involving ACC-3001?",
                                  "factual": false, "evidence_ids": []}, "…"]
    },
    "version": 1,
    "parent_draft_hash": null,
    "edited_by": null
  },
  "verifier_errors": [],
  "flagged_claims": [],
  "llm_meta": {
    "provider": "template",
    "model": "template",
    "prompt_version": "narrative_v4",
    "input_hash": "862e8331525f3a5e5a6a77aa42bf28569080c9d7066effea3e61503e50a94901",
    "attempts": 0,
    "fallback_used": true,
    "latency_ms": 0.045,
    "errors": []
  },
  "case_status": "awaiting_review",
  "trace": ["… load_case, retrieve_policy, draft_narrative, verify_evidence, save_draft …"],
  "draft_hash": "f26bf5e3a93e8b37e704f260fe3380e806daab2341ea92f1154a691c59b0753e",
  "precomputed": false
}
```

**Narrative sections (`narrative_v4`, PRD 8.2).** Render `draft.sections` as the narrative, one
heading per key, in this order:
- `observed_facts`, `model_rule_inferences`, `analyst_hypotheses`, `contradictory_evidence`,
  `data_gaps`, `relevant_policy`, `recommended_next_steps`.
- Each item is `{text, factual, evidence_ids}`. Every `factual` item cites ids: transactions,
  accounts, chunks, quality-check ids, lineage refs, `DF-n`, `model_run:<id>`,
  `graph_snapshot:<id>` or `not_available:<item>`. Make them clickable like chat claim ids.
- The verifier flags a factual item without ids, or one citing an unknown id, as
  `SECTION_UNCITED`. Hypotheses and next steps are not factual.
- Drafts from before v4 (and the precomputed golden draft if it predates v4) still get sections:
  they are rebuilt from the claims.
- `draft_hash` (`sha256` of the draft) is also in the `case.draft_generated` audit event.

Unknown alert, response `400`:
```json
{
  "status": "error",
  "error": "unknown alert_id 'alrt_missing'",
  "trace_id": null,
  "case_id": null,
  "alert_id": "alrt_missing",
  "draft": null,
  "verifier_errors": [],
  "flagged_claims": [],
  "llm_meta": null,
  "case_status": null,
  "trace": []
}
```

### `POST /api/v1/cases/{case_id}/decision`

Request (role and actor come from auth):
```json
{"action": "escalate", "reason": "Fan-out to new accounts within hours; escalate for review."}
```

**Human edits.** With `"edited_summary": "..."` in the decision, the agents save the reviewer's
text as a new draft version before the decision is recorded:
- The new version has `version: 2`, `parent_draft_hash` = the drafted `draft_hash`, and
  `edited_by` = the actor.
- It is audited as `case.narrative_edited`, with `parent_draft_hash`, `draft_hash`, `diff_hash`
  (sha256 of the unified diff of the summaries), `draft_version` and verifier error count/codes.
  The text itself is never audited.
- The response adds `edited_draft`, `edited_draft_hash` and `edited_verifier_errors`. The edited
  version is re-verified for information only; errors do not block, because the reviewer owns
  the text.
- `case.decision` metadata gains `draft_hash` and `draft_version` (the version that was decided).

Response `200`. Escalation is an internal review step: nothing is filed, frozen or sent.
```json
{
  "status": "ok",
  "error": null,
  "case_id": "case_8aa14eea7752",
  "case_status": "escalated",
  "audit_event": {
    "event_id": "evt_98de1660092a4e1aaad09c2f7265fef0",
    "actor_id": "user_123",
    "actor_role": "analyst",
    "action": "case.decision",
    "object_type": "case",
    "object_id": "case_8aa14eea7752",
    "before_hash": "d755605b29b9384130e18c87a2021708126cbaddc8a8d162c5c4bf3a4ee8d98a",
    "after_hash": "fcf8f7ed7677c3892b327a0ed2bb19987498dd7b6a25a4b3cc79dec2a3f681c1",
    "prev_hash": "11abba7fe638f172425e3b09b1c67558e967ebb94c906bf37caaf62ce0ebd1c0",
    "hash": "bb10c42035e4e965f808d04d2b185849b1fb0cb5a682d5974d03ecf222ac184e",
    "model_versions": {
      "mock_velocity": "mock-0",
      "mock_typology_rules": "mock-0",
      "mock_graph_risk": "mock-0"
    },
    "prompt_version": "narrative_v4",
    "timestamp": "2026-10-17T23:44:32.885582Z",
    "reason": "Fan-out to new accounts within hours; escalate for review.",
    "metadata": {
      "trace_id": "tr_ca91d5e8d19d456cb721decff2a415f6",
      "alert_id": "alrt_a812f90d1b25",
      "action": "escalate",
      "previous_status": "awaiting_review",
      "case_status": "escalated",
      "bundle_hash": "2fb828287f67bf7c7c9ba54d68510cff86752fb0b0190d4889dd701587ab72ee",
      "graph_snapshot_id": "snap_60742743c1f42dee",
      "draft_hash": "f26bf5e3a93e8b37e704f260fe3380e806daab2341ea92f1154a691c59b0753e",
      "draft_version": 1
    }
  },
  "trace": ["… the full case trace, ending with human_decision, record_decision …"],
  "edited_draft": null,
  "edited_draft_hash": null,
  "edited_verifier_errors": []
}
```

Refused decision (an analyst dismissing a `needs_revision` case), response `422`:
```json
{
  "status": "rejected",
  "error": "only a supervisor may dismiss a case whose draft still needs revision (actor_role='analyst')",
  "case_id": "case_8aa14eea7752",
  "case_status": null,
  "audit_event": null,
  "trace": []
}
```

### `POST /api/v1/copilot/query`

Request:
```json
{"question": "What should be documented when escalating an alert?", "alert_id": "alrt_a812f90d1b25"}
```

Response `200`. Every claim cites only `chunk_id`s in `sources`. `no_answer: true` means the
corpus doesn't cover the question.
```json
{
  "status": "ok",
  "error": null,
  "trace_id": "tr_5ef4da84d770457cace5e6360c2bdf5c",
  "answer": {
    "question": "What should be documented when escalating an alert?",
    "answer": "Deterministic fallback (no model synthesis): the 2 most relevant retrieved policy passages are quoted below. Read them against the question.",
    "claims": [
      {
        "text": "FICTIONAL: Example Community Bank Alert Triage Procedure v1 (synthetic, not a real institution): … records the evidence reviewed and a written rationale, and either dismisses it, requests more data, or escalates it to a second-level reviewer. …",
        "citation_chunk_ids": ["mock_fictional_triage_007"]
      }
    ],
    "unknowns": ["Excerpts were not checked for relevance by a model; confirm they apply."],
    "citations": ["mock_ffiec_sar_program_001", "mock_fictional_triage_007"],
    "no_answer": false,
    "provider": "template",
    "model": "template",
    "prompt_version": "policy_qa_v1",
    "fallback_used": true
  },
  "sources": [
    {
      "chunk_id": "mock_fictional_triage_007",
      "doc_title": "FICTIONAL: Example Community Bank Alert Triage Procedure v1 (synthetic, not a real institution)",
      "section": "Triage and escalation",
      "source_uri": "urn:tracelayer:fictional:example-bank:alert-triage-v1",
      "excerpt": "FICTIONAL PROCEDURE FOR DEMO USE ONLY. …",
      "score": 0.333333,
      "effective_date": null,
      "indexed_at": null
    }
  ],
  "verifier_errors": [],
  "trace": ["… retrieve_policy, copilot_answer, verify_citations …"]
}
```

### `GET /api/v1/runs/{trace_id}/trace`

Works for analysis, case and copilot `trace_id`s. Response `200` (`[]` if unknown or evicted):
```json
[
  {
    "trace_id": "tr_f5ed07a6267648c0bcf464febd7e5698",
    "node": "ingest",
    "status": "ok",
    "started_at": "2026-10-17T23:44:32.854285Z",
    "ended_at": "2026-10-17T23:44:32.855859Z",
    "duration_ms": 1.575,
    "input_hash": "64148b193198a3717aae378c3c1bc5032ee40dc0e397ecfb3cbedc55232b3890",
    "output_hash": "f6a036ce4d5a0887e683ced27b3579aa7d05e29e51608b6af38b226f7512454c",
    "error": null
  }
]
```

### `GET /api/v1/audit/verify`

Response `200`:
```json
{"ok": true, "checked": 19, "first_bad_index": null, "reason": null}
```

### Investigation chat (case-scoped)

A chat is bound to **one case** (opened by `POST /api/v1/cases`). It answers with read-only,
allowlisted tools inside that case's scope: the evidence bundle plus the ≤3-hop graph
neighbourhood around it, clipped to the case window (evidence span ± 7 days). It can't read
another case: switching case means a new chat. `actor_id`/`actor_role` come from your auth.

Suggested routes (each calls the facade function shown; status mapping as above, plus
`needs_clarification` → 200, `preview` → 200, `denied` → 403):

| route | facade |
|---|---|
| `POST /api/v1/cases/{case_id}/chats` | `await api.start_chat(case_id, user.id, user.role, user.tenant_id)` → `ChatResult` |
| `POST /api/v1/chats/{chat_id}/messages` body `{"text": "..."}` or `{"chip": "trace_forward"}` | `await api.send_message(chat_id, text=..., chip=...)` → `ChatTurnResult` |
| `POST /api/v1/chats/{chat_id}/context` body `{"selected_tx_ids": [...], "edge_ids": [...], "confirm": false}` | `api.update_chat_context(chat_id, selected_tx_ids, edge_ids, confirm)` → `ContextChangeResult` |
| `POST /api/v1/chats/{chat_id}/pins` body `{"ids": [...]}` | `api.pin_evidence(chat_id, ids)` → `PinResult` |
| `DELETE /api/v1/chats/{chat_id}/pins` body `{"ids": [...]}` | `api.unpin_evidence(chat_id, ids)` → `PinResult` |
| `GET /api/v1/chats/{chat_id}` | `api.get_chat(chat_id)` → `ChatResult` |
| `POST /api/v1/chats/{chat_id}/turns/{turn_id}/feedback` body `{"helpful": true, "note": "..."}` | `api.record_feedback(chat_id, turn_id, helpful, note)` → `FeedbackResult` |

Chips (`available_chips`): `explain_alert`, `trace_backward`, `trace_forward`, `why_connected`,
`evidence_per_signal`, `benign_explanations`, `policy_guidance`, `missing_data`, `similar_cases`,
`next_steps`, `shared_identifiers` (new). Send exactly one of `text` (≤ 2000 chars) or `chip`.

**For the frontend**
- Show `context` (alert, evidence, window, pins) with every answer; it is in every response.
- Render `turn.claims` grouped or badged by `label`: `observed_fact`, `model_inference`, `policy`,
  `hypothesis`, `missing_evidence`. Each claim's ids (`transaction_ids`, `account_ids`, `edge_ids`
  to highlight on the graph, `chunk_ids` for policy sources, `evidence_ids`) are clickable refs.
- `turn.gaps` are claims the verifier **dropped**: show the `detail` and `suggested_query` (as a
  "try this" prompt), never the dropped text (only its hash is kept).
- `turn.failures` / `missing_evidence` claims say which tool failed; nothing from a failed tool
  is presented as checked. `turn.llm_meta[].fallback_used=true` means the deterministic template
  wrote the prose.
- `status="needs_clarification"`: no tool ran; show `turn.answer` and `turn.suggested_chips`.
- Pins (`pin_evidence`) accept transaction ids, account ids and derived-fact ids (`DF-n`) of this
  case only; chips then start from the pinned ids. Any other id → `status="rejected"` with
  `rejected_ids`, nothing changed.
- **Answer metadata (PRD 8.2)**: every turn has `turn.metadata`. Show it as the answer's
  provenance line:
  - `as_of`: data freshness, the latest evidence timestamp the answer could see
    (`as_of_basis="latest_evidence_timestamp"`).
  - `graph_snapshot_id`, and `context_version`: which version of the chat context (selection,
    pins) the answer used.
  - `retrieved_sources`: the policy chunks retrieved for this answer (`chunk_id`, `doc_title`,
    `section`, `source_uri`).
  - `tool_actions`: the same list as `turn.tool_calls`.
  - `coverage`: `{verified, partial, unsupported}` counts. There is deliberately **no single
    confidence number**. `partial` counts hypotheses and claims from truncated or warned tool
    results; `unsupported` counts dropped parts plus missing-evidence statements.
- **Selection (`update_chat_context`)**: when the reviewer selects edges or transactions on the
  graph, call it first without `confirm` to get a `preview` (`added`, `removed`,
  `out_of_scope_count`, `next_context_version`). Show it, then call again with `confirm=true`. The
  change is all-or-nothing: any id outside the case → `rejected`, nothing changed, and the
  out-of-scope ids are never echoed. A confirmed change bumps `context.context_version`, sets
  `context.selected_ids` (chips then start from them, before pins) and is audited as
  `chat.context_changed`. Pins also bump `context_version`.
- **Narrowed requests**: an unbounded question (more than 3 hops, "all accounts", "all time")
  still runs, but inside the case bounds. `turn.bounds_applied` lists the bounds in plain
  language; show it above the answer.
- **Denied requests**: `status="denied"`.
  - A question that names another case's records (or another tenant/bank) returns a `turn` with
    `status="denied"`, an explanation in `turn.answer`, no tool calls, no claims and
    `question=null`.
  - When the access policy refuses the case itself, `turn` and `context` are `null`.
- `similar_cases` returns synthetic library cases with a score breakdown and the caveat
  "Similarity is not proof; prior decisions can be wrong." Show the caveat with the results.

Latency: chips call the LLM once (answer prose), free text twice (plan + prose); with the
template it is milliseconds, with Ollama see the latency note above. Each tool is capped at 5 s.

#### `POST /api/v1/cases/{case_id}/chats`

Response `200` (`ChatResult`):
```json
{
  "status": "ok", "error": null,
  "chat": {
    "chat_id": "chat_fc646ac2f7c2", "case_id": "case_5ea02560904e",
    "alert_id": "alrt_a812f90d1b25", "actor_id": "user_1", "actor_role": "analyst",
    "created_at": "2026-10-18T03:18:00.838014Z",
    "context": {
      "case_id": "case_5ea02560904e", "alert_id": "alrt_a812f90d1b25",
      "alert": {"alert_id": "alrt_a812f90d1b25", "tier": "high", "composite_score": 0.932203, "…": "…"},
      "evidence_bundle": {"alert_id": "alrt_a812f90d1b25", "transactions": ["…"], "bundle_hash": "2fb8…"},
      "derived_facts": [{"fact_id": "DF-1", "label": "evidence transaction count", "kind": "count",
                         "value": "9", "currency": null, "transaction_ids": ["TX-51001", "…"],
                         "account_ids": []}, "…"],
      "quality_issues": [], "graph_snapshot_id": "snap_60742743c1f42dee",
      "window_start": "2026-08-28T13:05:00Z", "window_end": "2026-09-11T15:21:00Z",
      "entity_scope": ["ACC-3001", "ACC-3101", "…"], "scope_transaction_count": 9,
      "pinned_evidence_ids": [], "scope_notes": [], "selected_ids": [], "context_version": 1
    },
    "tenant_id": "tenant_demo",
    "turns": []
  },
  "available_chips": ["explain_alert", "trace_backward", "trace_forward", "…"]
}
```

#### `POST /api/v1/chats/{chat_id}/messages`

Request: `{"chip": "benign_explanations"}` (or `{"text": "Where did the money go?"}`).
Response `200` (`ChatTurnResult`):
```json
{
  "status": "ok", "error": null, "chat_id": "chat_fc646ac2f7c2",
  "turn": {
    "turn_id": "turn_569320d7329d", "trace_id": "tr_1953…", "created_at": "2026-10-18T03:18:00.844806Z",
    "chip": "benign_explanations", "question": null, "question_hash": "50ef…",
    "status": "ok", "intent": "benign_explanations",
    "answer": "Checked time_window_aggregate, trace_forward, transaction_lookup. Each statement below cites its source ids. Being connected to an account in the graph is not evidence of wrongdoing. Deterministic answer (no model synthesis); a reviewer must assess it.",
    "claims": [
      {"text": "From 2026-08-28 to 2026-09-11, ACC-3001 sent $43,855.00 across 9 transactions.",
       "label": "observed_fact", "transaction_ids": ["TX-51001", "…"], "account_ids": ["ACC-3001"],
       "edge_ids": [], "evidence_ids": [], "chunk_ids": [], "model_run_id": null,
       "amounts": ["43855.00"], "source_tools": ["time_window_aggregate"], "benign_kind": null},
      {"text": "Forward trace from ACC-3001: ACC-3001 → ACC-3101 via TX-51001, totaling $4,850.00, on 2026-09-04.",
       "label": "observed_fact", "transaction_ids": ["TX-51001"], "account_ids": ["ACC-3001", "ACC-3101"],
       "edge_ids": ["TX-51001"], "evidence_ids": [], "chunk_ids": [], "model_run_id": null,
       "amounts": ["4850.00"], "source_tools": ["trace_forward"], "benign_kind": null},
      {"text": "Benign hypothesis: the activity is consistent with a payroll-like disbursement (one payer, several recipients, one day, similar amounts). This needs confirmation and reviewer assessment.",
       "label": "hypothesis", "transaction_ids": ["TX-51001", "…"], "account_ids": ["ACC-3001", "…"],
       "edge_ids": [], "evidence_ids": [], "chunk_ids": [], "model_run_id": null, "amounts": [],
       "source_tools": ["time_window_aggregate", "trace_forward"], "benign_kind": "payroll"},
      "…"
    ],
    "gaps": [],
    "failures": [],
    "next_steps": ["Obtain the KYC profile and stated business purpose for ACC-3001.", "…"],
    "suggested_chips": [],
    "tool_calls": [
      {"tool": "time_window_aggregate", "args": {"account_ids": ["ACC-3001"], "group_by": "account"},
       "status": "ok", "row_count": 1, "truncated": false, "duration_ms": 0.245, "error": null},
      {"tool": "trace_forward", "args": {"account_id": "ACC-3001", "hops": 1},
       "status": "ok", "row_count": 9, "truncated": false, "duration_ms": 0.22, "error": null},
      "…"
    ],
    "verification_status": "verified",
    "llm_meta": [{"provider": "template", "model": "template", "prompt_version": "chat_answer_v2",
                  "input_hash": "b38d…", "attempts": 0, "fallback_used": true, "latency_ms": 0.026,
                  "errors": []}],
    "feedback": null,
    "bounds_applied": [],
    "metadata": {
      "as_of": "2026-09-04T15:21:00Z", "as_of_basis": "latest_evidence_timestamp",
      "graph_snapshot_id": "snap_60742743c1f42dee", "context_version": 1,
      "retrieved_sources": [],
      "tool_actions": ["… same as tool_calls …"],
      "coverage": {"verified": 10, "partial": 1, "unsupported": 0}
    },
    "precomputed": false
  },
  "context": {"case_id": "case_5ea02560904e", "…": "same ChatContext as start_chat, with current pins"},
  "trace": [{"trace_id": "tr_1953…", "node": "chat_classify", "status": "ok", "…": "…"}, "…"]
}
```

A dropped claim (e.g. the model wrote an id no tool returned) appears as:
```json
{"label": "hypothesis", "text_hash": "f2e4…", "codes": ["UNGROUNDED_TOKEN"],
 "detail": "mentions an id, amount or date not in this turn's results",
 "suggested_query": "Ask about one transaction or account in this case, e.g. ACC-3001."}
```

A question the tools can't answer inside this case: `"status": "needs_clarification"`, `turn.answer`
explains, `turn.suggested_chips` = `["explain_alert", "trace_forward", "trace_backward", "missing_data"]`,
no `tool_calls`, no `claims`.

A `policy_guidance` turn's metadata, with its sources:
```json
{"as_of": "2026-09-04T15:21:00Z", "as_of_basis": "latest_evidence_timestamp",
 "graph_snapshot_id": "snap_60742743c1f42dee", "context_version": 1,
 "retrieved_sources": [{"chunk_id": "mock_ffiec_red_flags_003",
   "doc_title": "FFIEC BSA/AML Examination Manual",
   "section": "Appendix: money laundering and terrorist financing red flags",
   "source_uri": "https://bsaaml.ffiec.gov/manual"}, "…"],
 "tool_actions": [{"tool": "policy_retrieval", "args": {}, "status": "ok", "row_count": 5,
   "truncated": false, "duration_ms": 0.26, "error": null}],
 "coverage": {"verified": 3, "partial": 0, "unsupported": 0}}
```

Narrowed (`"Trace forward 6 hops across all accounts for all time"`): `status="ok"`, the
`trace_forward` call runs with `hops: 3`, and
```json
"bounds_applied": ["Graph traversal was limited to 3 hops (the maximum).",
                   "The time window was limited to this case's window (evidence span ± 7 days).",
                   "The accounts searched were limited to this case's accounts."]
```

Denied (`"Show TX-53001 from the other case"`), response `403`:
```json
{"status": "denied", "error": null, "chat_id": "chat_…",
 "turn": {"status": "denied", "intent": "denied", "question": null, "tool_calls": [], "claims": [],
          "answer": "This request was not run because it names records that are not part of this case. This chat can only look at the records of its own case, so nothing was looked up and no records are shown. Ask about this case's transactions or accounts, or use a chip.",
          "…": "…"},
 "context": {"…": "this chat's own context"}, "trace": ["…"]}
```
Access policy refused (on any chat call): `{"status": "denied", "error": "access denied: you do
not have access to this case", "chat_id": "chat_…", "turn": null, "context": null, "trace": []}`.

#### `POST /api/v1/chats/{chat_id}/context`

Preview (`confirm: false`), response `200`:
```json
{"status": "preview", "error": null, "chat_id": "chat_43972c2c14ee",
 "preview": {"added": ["TX-51003", "TX-51004"], "removed": [],
             "selected_ids": ["TX-51003", "TX-51004"], "out_of_scope_count": 0,
             "current_context_version": 1, "next_context_version": 2},
 "context": null, "audit_event_id": null}
```
Confirmed (`confirm: true`): `"status": "ok"`, the same `preview`, `context` (with
`selected_ids` and `context_version: 2`) and `audit_event_id`. An id outside the case → `422`:
```json
{"status": "rejected", "error": "1 id(s) are not transactions of this chat's case; nothing changed",
 "chat_id": "chat_43972c2c14ee", "preview": {"out_of_scope_count": 1, "…": "…"},
 "context": null, "audit_event_id": null}
```

#### `POST /api/v1/chats/{chat_id}/pins`

Request `{"ids": ["ACC-3105", "DF-1"]}` → `{"status": "ok", "error": null, "chat_id": "chat_…",
"pinned_evidence_ids": ["ACC-3105", "DF-1"], "rejected_ids": []}`. An id from another case →
`{"status": "rejected", "error": "ids must be transactions, accounts or derived facts of this
chat's case", "pinned_evidence_ids": [], "rejected_ids": ["TX-13007"], …}`.

#### `POST /api/v1/chats/{chat_id}/turns/{turn_id}/feedback`

Request `{"helpful": true, "note": "useful"}` → `{"status": "ok", "error": null, "chat_id": "chat_…",
"turn_id": "turn_…", "audit_event_id": "evt_…"}`. The note is audited as a hash and length only.

**Audit.** Every chat writes to the case's audit trail (`object_type="case"`, `object_id=case_id`,
actor = the chat's user): `chat.started`, `chat.turn` (question hash, chip, intent, tool calls
`tool:status:rows`, tool args as canonical JSON with free text hashed, evidence refs, chunk ids,
prompt versions/provider/model, verification status, kept/dropped claim counts, bundle hash),
`chat.pin`, `chat.unpin`, `chat.feedback`, and (PRD 8.2):
- `chat.context_changed`: context version before and after, added/removed ids, before/after
  hash of the selection.
- `chat.request_denied`: `reasons` (`access_denied`, `out_of_scope_ids`, `other_tenant`),
  `request`, `tenant_id`, question hash or out-of-scope count. Never the ids that were refused.
- `chat.request_narrowed`: `bounds` (`hops_capped`, `window_limited`, `entities_limited`),
  `requested_hops`, question hash.

References only, never raw rows or question text.

## 6. Persisting agent output

`tracelayer_agents.adapters.persistence` maps facade results onto your request bodies in
`backend/app/schemas.py` and the `audit_events` table. Its functions are pure (no DB, no HTTP), and
each one returns a JSON-ready dict. The tests validate every dict against your Pydantic models.
Call them in this order:

| # | facade result | mapping | your endpoint / table |
|---|---|---|---|
| 1 | `start_case` → `CaseDraftResult` | `agent_run_create(result)` | `POST /cases/{case_id}/agent-runs` → `agent_runs` |
| 2 | `CaseDraftResult.trace`, `.case_status` | `agent_run_update(trace, case_status)` | `PATCH /agent-runs/{run_id}` (`append_agent_steps`) |
| 3 | the alert's `EvidenceBundle` (`CaseRepository.get_evidence`) | `case_evidence_rows(bundle)` | `POST /cases/{case_id}/evidence`, one call per row → `case_evidence` |
| 4 | `CaseDraftResult.draft` | `narrative_create(result, agent_run_uuid)` | `POST /cases/{case_id}/narratives` → `case_narratives`, `narrative_claims`, `narrative_citations` |
| 4b | `CaseDecisionResult.edited_draft` (when the reviewer edited the summary) | `narrative_edit_create(result, narrative_uuid(draft_hash))` | `POST /cases/{case_id}/narratives` with `parent_narrative_id` = the drafted narrative |
| 5 | `CaseDraftResult.verifier_errors`, `.flagged_claims` | `narrative_verification(result)` | `PUT /narratives/{narrative_id}/verification` |
| 6 | `resume_case` → `Decision` + `CaseDecisionResult` | `case_decision_row(decision, result, narrative_uuid)` | `POST /cases/{case_id}/decisions` → `case_decisions` |
| 7 | `CaseDecisionResult.trace`, `.case_status` | `agent_run_update(...)` (status `completed`) | `PATCH /agent-runs/{run_id}` |
| 8 | every `AuditEvent` (`AuditRepository.list_events`) | `audit_event_row(event, tenant_id)` | `audit_events` |

**IDs.** Your id columns are uuid; ours are strings such as `TX-…`, `ACC-…` and chunk ids, and
they stay that way. `to_uuid(kind, id)` gives a deterministic uuid5 with one namespace per kind
(`transaction`, `account`, `alert`, `case`, `chunk`, `agent_run`, `narrative`, `evidence`,
`actor`, `audit_event`). The original id is also kept in a text or jsonb field: `config` in
`agent_runs`, `referenced_values` in `narrative_claims`, `payload` and `source_ref` in
`case_evidence`, and `metadata` in `audit_events`. `audit_events.object_id` is text, so our id
goes there unchanged.

**Claim mapping.** Claim `i` is `draft.observed_facts[i]`, so `claim_index` matches our
`flagged_claims` and `VerifierError.claim_index`.
- `claim_kind`: fact → `fact`, hypothesis → `hypothesis`, policy → `fact` (with cited chunks).
  Each uncertainty becomes an extra `unknown` claim at the end.
- Per-claim verification: a flagged claim is `unsupported` (the error codes go in
  `verifier_notes`). Otherwise a fact or policy claim is `supported`, and a hypothesis or
  unknown is `not_applicable`.
- Narrative status: no errors → `passed`; any error or `needs_revision` → `flagged`; error
  result → `failed`.

**Decision mapping** (`review_decision` + `disposition`):
- escalate → `escalate` + `escalate_for_sar_consideration`
- dismiss → `reject` + `no_further_action`
- request_more_data → `edit` + `needs_more_information`

`edited_summary` is appended to `notes`, and the edited version is its own narrative (row 4b).

**Sections and versions.** Your `NarrativeCreate` has no column for the v4 sections, `draft_hash`
or version:
- `content` renders the summary plus one `## <Section>` block per section, each item followed by
  its evidence ids.
- Each claim's `referenced_values` carries `section`, `draft_hash` and `draft_version`.
- `narrative_uuid(draft_hash)` gives the stable narrative uuid for a version, so the edit's
  `parent_narrative_id` resolves.
- Suggested backend change: add `draft_hash text`, `version int` and `sections jsonb` to
  `case_narratives` (TEAM_QUESTIONS backend).

**Where your schema cannot hold our original id** (`UNHOLDABLE_ORIGINAL_IDS`):
- `CitationIn.policy_chunk_id`: there is no text field, and it is an FK to `policy_chunks`.
- `NarrativeCreate.agent_run_id` / `parent_narrative_id` and `DecisionIn.narrative_id`: there is
  no text field.
- `EvidenceIn.alert_id` / `account_id` / `policy_chunk_id`: the originals go in `payload`, but
  the uuid columns are FKs, so a uuid5 only resolves if your ingestion uses the same mapping.
- Route params `case_id` / `run_id` / `narrative_id`.

See TEAM_QUESTIONS.md, Backend 13–19.

## 7. Limitations

- **In-memory until the backend provides repositories.** With the default mocks, alerts, evidence,
  cases, drafts and audit events live in process memory and are lost on restart. Pass real
  `CaseRepository` / `AuditRepository` implementations to fix that.
- **Paused cases are in memory.** The case checkpointer is LangGraph's `MemorySaver`, so a case
  drafted before a restart can't be decided after it (TEAM_QUESTIONS backend Q7).
- **Traces** for `get_trace` are kept in memory for the last 50 runs only.
- **Chat sessions are in memory** (lost on restart and on `api.configure()`); the case audit trail
  is the durable record of every turn (TEAM_QUESTIONS backend Q on chat persistence).
- **Single process.** Run one uvicorn worker. Multiple workers would each hold their own services,
  checkpointer and traces, and the audit chain doesn't support concurrent writers.
