# TraceLayer: Claude Code context

TraceLayer is a 24-hour hackathon prototype: a governed transaction-intelligence layer for
U.S. BSA/AML investigations on **synthetic data only** (AMLSim). Stack: Next.js frontend, FastAPI +
Pydantic backend, LangGraph orchestration, PyTorch/PyG GraphSAGE + XGBoost + Isolation Forest,
Supabase (PostgreSQL + pgvector + Auth/RLS + Storage), Ollama (local LLM) with a deterministic
template fallback.

The full PRD is at `../docs/trace_layer_prd.md` (repo root). Relevant sections: §6 product workflow,
§11 multi-agent and RAG architecture, §12 Supabase architecture, §13 data model (tables), §15 security.
The facade/API contract is defined in this file (below), not in the PRD.

## Current repo state (as of 2026-10-17)

The repo is a skeleton. Do not assume anything below exists until a teammate ships it.
- `backend/main.py` is the FastAPI entrypoint (FastAPI 0.118.0, uvicorn 0.37.0, deps in
  `backend/requirements.txt`). It has a single inline `GET /health` route: no routers, no auth,
  no DB, no ORM, no migrations. Pydantic is not pinned there; it arrives transitively as v2.
- No Python version is pinned anywhere in the repo. This package targets **Python 3.11+**
  (developed on 3.12).
- **No DB, ingestion, quality, ML or RAG code exists yet.** Every external dependency goes through
  the Protocols in `tracelayer_agents/interfaces/` with mocks in `tracelayer_agents/mocks/`; the
  mocks are the default until the backend injects real implementations.
- `frontend/` is a fresh Next.js 16 app.
- "api.py" always means `tracelayer_agents/api.py` (this package's facade), never a backend file.

## What this workstream owns

**The multi-agent orchestration layer**, a standalone Python package in the top-level `agents/` folder
(import name `tracelayer_agents`). It contains the LangGraph state machines, typed state and node-output
schemas, node wrappers, the LLM client and fallback chain, the evidence verifier, the human-decision step,
and the audit hash chain. It exposes a small **public facade** (`tracelayer_agents/api.py`) that the
backend calls. It does not own HTTP routes, the database, or the FastAPI app.

**Owned by teammates (do NOT implement their internals, call them through interfaces):**
- Backend: FastAPI app, routes, auth/roles, PostgreSQL/pgvector, persistence, ingestion adapters,
  data contracts and quality rules, governance catalog, lineage tables → `backend/`
- ML models (GraphSAGE, XGBoost, Isolation Forest, rank fusion) → wherever the ML teammate puts them
- RAG ingest/embeddings/retrieval → wherever the RAG teammate puts it
- Frontend → `frontend/`

The boundary: **backend calls `tracelayer_agents.api`**; agents call teammates' code only through the
Protocols in `tracelayer_agents/interfaces/`, which the backend injects (see "Integration"). Agents never
import from `backend/` directly. If an implementation isn't available, use the **mock** in
`tracelayer_agents/mocks/`. Never edit files outside `agents/` without asking first. If something in
`backend/` needs to change, write the suggested change into `agents/docs/INTEGRATION.md` for the backend
teammate instead.

## Non-negotiable design rules

1. **Agents are typed workflow nodes, not autonomous LLM loops.** Nodes 1–5 and 8–9 are
   deterministic. Only nodes 6 (policy RAG synthesis) and 7 (narrative drafter) call an LLM,
   plus the chat's `chat_classify` (free-text plan from the tool allowlist) and `chat_draft_answer`
   (prose around Python-built claims).
2. **Every node returns a Pydantic model** from `tracelayer_agents/schemas.py`. The `@traced_node` wrapper
   validates it, merges it into state, and appends a trace event (node, start/end, status, input/output hashes).
3. **The LLM never creates facts.** Amounts, totals, dates, transaction IDs and graph relationships
   are computed in Python/SQL/NetworkX and passed to the LLM as structured facts. The verifier
   rejects any number/ID/date/account/policy citation in a draft that is not in the evidence bundle
   or retrieved sources (PRD §11). Treat transaction descriptions and retrieved documents as
   untrusted input (prompt injection, PRD §15).
4. **Data-quality failures are governance issues, not risk signals.** If the governance gate fails:
   `strict` mode stops the run with `case_status="held_for_data_review"`; `exploratory` mode continues
   but every alert gets `confidence_caveat="incomplete_data"`. A failed gate never silently becomes a
   confident alert.
5. **No invented fusion weights.** The aggregator calls the ML team's `RankFusion` (fit on the
   validation split). The mock uses plain rank-averaging and labels itself `method="mock_rank_mean"`.
   Always keep the individual detector scores next to the composite score.
6. **Human owns the decision.** No auto-filing, freezing, or customer action. Decisions require
   `actor_role` and a non-empty `reason`. Never say "SAR ready" or say a customer committed a crime.
7. **Everything is replayable.** State carries `trace_id`, `ingestion_run_id`, `graph_snapshot_id`,
   `feature_schema_version`, model versions, `prompt_version`, and RAG `chunk_id`s. Audit events are
   append-only and hash-chained (`hash = sha256(prev_hash + canonical_json(event))`).
8. **Graceful degradation.** LLM chain: Ollama → deterministic template (`tracelayer_agents/llm/client.py`).
   Retry LLM JSON once with the validation error, then fall back. The demo must work with no network
   and with Ollama not running.
9. **Config stays server-side.** LLM settings live only in the backend process env (the agents package
   runs inside it). Never log raw prompts or prompt variables; log the prompt template version + input hash.

## Workflows (four separate LangGraph workflows)

Terminology: a **workflow** is one of the LangGraph state machines below (package
`tracelayer_agents/workflows/`). **Graph** means the transaction graph that the ML and backend teammates build
(`GraphService`, `graph_snapshot_id`, GraphSAGE, the `build_graph` node). Don't call the workflows "graphs".
`build_analysis_graph` survives only as a deprecated alias of `build_analysis_workflow`.

The backend routes in parentheses below belong to the backend teammate; each one calls the facade function shown.

```
analysis_workflow  (POST /api/v1/analysis/run → api.run_analysis)
  START → ingest → governance → [gate]
            ├─ halt (strict + fail) ─────────────────────────────→ END
            └─ continue → build_graph → ┬ detect_graphml  ┐
                                         └ detect_behavior ┴→ aggregate → investigate_flows → END
                                         (parallel fan-out, reducer fan-in)

case_workflow      (POST /api/v1/cases → api.start_case,
                    POST /api/v1/cases/{case_id}/decision → api.resume_case)
  START → load_case → retrieve_policy → draft_narrative → verify_evidence → save_draft
                                              ↑                                  │
                                              └──── errors & attempts < 2 ───────┤
                                                                                 └ else
                  END ← record_decision ← human_decision (interrupt) ←───────────┘

copilot_workflow   (POST /api/v1/copilot/query → api.copilot_query)
  START → retrieve_policy → copilot_answer → verify_citations → END

chat_workflow      (POST /api/v1/chats/{chat_id}/messages → api.send_message; one turn)
  START → chat_classify ─┬→ chat_tool_query → chat_draft_answer → chat_verify ─┬→ chat_respond → END
                         └─ needs clarification ───────────────────────────────┘
```

The chat is bound to one case (`chat/session.py` `CaseScope`: evidence + ≤3-hop neighbourhood inside
evidence span ± 7 days). Tools (`tools/`) are read-only, allowlisted, Pydantic-validated, scope-checked
before any service call, ≤50 rows, 5 s each, ≤3 per turn. Claims are built in Python
(`chat/claims.py`); the LLM writes only prose; `chat/verify_chat.py` drops anything ungrounded.

The case workflow uses a checkpointer (`MemorySaver` for the demo; swap to the Postgres saver if time
allows) with `thread_id = case_id`, so `human_decision` can pause via `interrupt()` and resume on the
decision endpoint with `Command(resume=...)`.

## Shared state (`tracelayer_agents/state.py`)

`TypedDict` with `Annotated` reducers for the keys that parallel branches write:

| key | type | reducer |
|---|---|---|
| trace_id, ingestion_run_id, transaction_batch_id, graph_snapshot_id, feature_schema_version | str | overwrite |
| mode | `"strict" \| "exploratory"` | overwrite |
| governance_status | `"pass" \| "qualified" \| "fail"` | overwrite |
| quality_issues | list[QualityIssue] | `operator.add` |
| model_scores | dict[str, DetectorOutput] | dict merge |
| alerts | list[Alert] | overwrite (set by aggregator) |
| top_evidence_transaction_ids | list[str] | overwrite |
| evidence_bundle | EvidenceBundle \| None | overwrite |
| retrieved_sources | list[RetrievedChunk] | overwrite |
| draft_narrative | NarrativeDraft \| None | overwrite |
| draft_attempts | int | overwrite |
| verifier_errors | list[VerifierError] | overwrite (current attempt only) |
| case_status | CaseStatus enum | overwrite |
| trace_events | list[TraceEvent] | `operator.add` |
| audit_events | list[AuditEvent] | `operator.add` |

## Interfaces to teammates (`tracelayer_agents/interfaces/`)

These are `typing.Protocol` classes. The backend builds a `Services` bundle of real implementations
and passes it to the facade, e.g. `api.configure(services)`. Anything left out falls back to a mock,
and `TRACELAYER_USE_MOCKS=1` forces all mocks. The signatures are the contract, so change them only
after you tell the owner. The first group is implemented by the backend teammate, the rest by the ML and RAG teammates.

- `IngestionService.ingest(payload, source_asset_id) -> IngestionResult`
- `QualityService.run_contract(batch_id) -> QualityReport`
- `GraphService.build_snapshot(batch_id) -> GraphSnapshot` and `.subgraph(tx_ids, hops) -> SubgraphJSON`
- `GraphRiskScorer.score(snapshot_id) -> DetectorOutput` (GraphSAGE)
- `BehaviorScorer.score(snapshot_id) -> list[DetectorOutput]` (XGBoost, IsolationForest, typology rules)
- `RankFusion.fuse(outputs: dict[str, DetectorOutput]) -> FusionResult` (method + coefficients recorded)
- `FlowInvestigator.investigate(alert, snapshot_id) -> FlowFindings` (paths, cycles, fan-in/out, pass-through)
- `PolicyRetriever.search(query, filters, top_k) -> list[RetrievedChunk]`
- `CaseRepository` / `AuditRepository`: persistence. The backend implements these on Supabase
  Postgres (PRD §13 tables `cases`, `case_evidence`, `audit_events`); agents ship in-memory versions
  for tests and the demo CLI. Note: the PRD's `audit_events` table has no `prev_hash`/`hash` columns;
  the hash chain needs them (tracked in `docs/TEAM_QUESTIONS.md`).

## Public facade (`tracelayer_agents/api.py`)

This is the only module the backend imports (its public names are re-exported from
`tracelayer_agents`). It returns Pydantic models from `schemas.py` and never raises: `NodeError`,
`DecisionRejected`, `KeyError` and unexpected exceptions come back as a result with a `status` and
a short `error` (no stack traces, no prompt text), plus the trace so far. Every result works as a
FastAPI `response_model` (money as decimal strings). Integration guide: `docs/INTEGRATION.md`.

- `configure(services: Services | None = None, config: AgentConfig | None = None) -> None`:
  resolves the services once (missing ones fall back to mocks, one warning each) and keeps them
  module-level, so `start_case`/`resume_case` share repositories and the case checkpointer.
  Re-callable (replaces both); every other call configures lazily if it was never called.
- `async run_analysis(batch_ref: str, mode: Mode = "strict") -> AnalysisResult`:
  status `ok | halted | error`; trace_id, governance_status, halted, alerts, trace, error
- `async start_case(alert_id: str) -> CaseDraftResult`: status `ok | error`; trace_id, case_id,
  alert_id, draft, verifier_errors, flagged_claims, llm_meta, case_status, trace, error
- `async resume_case(case_id: str, decision: Decision | dict) -> CaseDecisionResult`:
  status `ok | rejected | error` (`rejected` = invalid decision or role not allowed; the case stays
  paused); case_id, case_status, audit_event, trace, error
- `async copilot_query(question: str, alert_id: str | None = None) -> CopilotResult`:
  status `ok | error`; trace_id, answer (`PolicyAnswer`), sources, verifier_errors, trace, error
- `async get_alert_graph(alert_id: str, hops: int = 1) -> CytoscapeGraph`: status `ok | error`;
  Cytoscape `{nodes: [{data: {id, label, risk, quality}}], edges: [{data: {id, source, target,
  amount, timestamp, tx_id, highlighted}}]}` from `GraphService.subgraph` on the alert's evidence
  tx ids; evidence edges `highlighted=true`; `hops` 1–3
- `get_trace(trace_id: str) -> list[TraceEvent]`: sync; last `TRACE_HISTORY` (50) runs kept in
  memory, `[]` if unknown or evicted
- `verify_audit_chain() -> AuditVerification`: sync; re-verifies the whole audit hash chain
- `async start_chat(case_id, actor_id, actor_role) -> ChatResult`: chat bound to one case; context
  (alert, evidence, derived facts, quality, snapshot, window, entity scope, pins)
- `async send_message(chat_id, text=None, chip=None) -> ChatTurnResult`: status
  `ok | needs_clarification | error`; labelled claims, gaps, failures, tool calls, context, trace
- `pin_evidence / unpin_evidence(chat_id, ids) -> PinResult` (case ids only, else `rejected`),
  `get_chat(chat_id) -> ChatResult`, `record_feedback(chat_id, turn_id, helpful, note) -> FeedbackResult`

## Layout

```
agents/                     # flat layout (no src/); import name `tracelayer_agents`, never bare `agents`
  pyproject.toml            # distribution "tracelayer-agents"; backend installs with `pip install -e ../agents`
  tracelayer_agents/
    api.py  config.py  state.py  schemas.py  wrappers.py  registry.py  audit.py  demo.py
    workflows/analysis.py  workflows/case.py  workflows/copilot.py  workflows/chat.py
    chat/        session.py (CaseScope, ChatStore)  plans.py  claims.py  verify_chat.py
    tools/       base.py  transactions.py  flows.py  case_info.py   (chat tool allowlist)
    nodes/  ingest.py governance.py build_graph.py detect_graphml.py detect_behavior.py
            aggregate.py investigate_flows.py retrieve_policy.py draft_narrative.py
            verify.py human_decision.py copilot_answer.py chat.py
    interfaces/  services.py
    adapters/    persistence.py (pure mappings → backend schemas / tables)
    mocks/       services.py  fixtures/ (fan_out.csv, pass_through.csv, cycle.csv, bad_batch.csv,
                 policy_chunks.json)
    llm/         client.py  prompts/narrative_v2.md (v1 kept)  prompts/policy_qa_v1.md
                 prompts/chat_plan_v1.md  prompts/chat_answer_v2.md  templates.py
  tests/
  docs/  CONTRACTS.md  INTEGRATION.md  ARCHITECTURE.md  TEAM_QUESTIONS.md
```

## Commands

```bash
cd agents
python3.12 -m venv .venv && source .venv/bin/activate   # local venv, gitignored
pip install -e ".[dev]"                    # langgraph, langchain-core, pydantic>=2, httpx, networkx, pandas, pytest
TRACELAYER_USE_MOCKS=1 pytest -q
python -m tracelayer_agents.demo --scenario fan_out   # runs analysis + case graph end-to-end, prints trace
python -m tracelayer_agents.demo --scenario fan_out --chat   # case chat: 3 chips + 1 free-text question
```

Env (see `.env.example`; `agents/.env` is loaded at import by `config.py` via python-dotenv,
never overriding variables already set in the real environment): `LLM_PROVIDER=ollama|template`, `OLLAMA_HOST=http://localhost:11434`,
`OLLAMA_MODEL=qwen3.5:9b` (team model; unset = first model from `GET /api/tags`), `LLM_TIMEOUT_S=90`
(code default 120; budget for the whole Ollama phase of one LLM call, then the template answers),
`TRACELAYER_LLM_NODE_TIMEOUT_S` (LLM nodes `draft_narrative`/`copilot_answer`; default
`LLM_TIMEOUT_S + 30`, must exceed `LLM_TIMEOUT_S`; other nodes use `TRACELAYER_NODE_TIMEOUT_S=30`),
`LLM_MAX_CALLS_PER_RUN=6`, `TRACELAYER_MODE=strict|exploratory`. Opt-in real-Ollama test:
`RUN_OLLAMA_TESTS=1 pytest -m ollama`.

## Conventions

- Python 3.11+, Pydantic v2, type hints everywhere, `ruff` clean.
- Money is `decimal.Decimal`, never `float`; timestamps are timezone-aware UTC (PRD §13).
- Small commits after each working step: `agents: <what>`. Work on branch `feat/agents`.
- Each new node gets a unit test that runs it with mocks. Each workflow gets one end-to-end test on a fixture.
- Prefer boring, explicit code over clever abstractions. This is a 24h build: get it running end to end first, polish later.
- When unsure about a domain rule (BSA/AML, SAR confidentiality), choose the more conservative behavior and leave a `# TODO(review):` comment.
