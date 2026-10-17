# Open questions for teammates

From the agents workstream (`tracelayer_agents`). These are only the questions that `agents/CLAUDE.md` and
the PRD don't already answer. Until we hear back, agents run against the mocks in
`tracelayer_agents/mocks/`, so nothing here blocks us. It does decide what the integration looks like.

**Already settled, no need to answer:**
- Every facade function in `tracelayer_agents/api.py` is `async`. Await it from async FastAPI routes.
- Persistence goes through `CaseRepository` / `AuditRepository`, which you implement and pass in with
  `api.configure(services)`. Agents never import from `backend/`.
- The Protocol signatures in CLAUDE.md ("Interfaces to teammates") are the contract.

## Backend

1. **Long runs.** A `run_analysis` call can take several seconds (graph build + scoring + LLM). Should
   the route await it inline, or run it as a FastAPI background task and have the frontend poll
   `get_trace(trace_id)`? (PRD §12 rules out a custom streaming stack.)
2. **Hash-chain columns.** The PRD §13 `audit_events` table has `event_id, actor_id, action, object_type,
   object_id, timestamp, metadata`. The audit chain also needs `prev_hash` and `hash` (or both stored in
   `metadata`). Can you add them? Who writes the genesis row?
3. **Append-only.** Can `audit_events` be insert-only in Supabase, with an RLS/policy that blocks
   UPDATE/DELETE, so we can actually claim the chain is tamper-evident?
4. **Roles.** `Decision` needs `actor_id` and `actor_role`. Which Supabase Auth claim or metadata field
   holds the role, and what are the exact role names (`investigator`, `reviewer`, ...)? You check
   authorization in the route; agents only record the role. Does that work for you?
5. **Who decides.** Which roles can accept, reject or escalate? Can a reviewer override an investigator?
   We'll enforce nothing in agents beyond "role and reason are required".
6. **IDs.** Who mints `trace_id`, `case_id` and `alert_id`: agents (uuid4) or the DB? Does `case_id` exist
   before `start_case`, or does `start_case(alert_id)` create the case row through `CaseRepository`?
7. **Checkpointer.** The case graph uses `MemorySaver` (it doesn't survive a restart). Is that OK for
   the demo, or should we use the LangGraph Postgres saver against Supabase? If so, which connection
   string or pooler should we use?
8. **Ingestion and quality output.** What exact fields do you return in `IngestionResult` and `QualityReport`?
   We need `ingestion_run_id`, `transaction_batch_id`, an issue list with `severity`, and a pass/qualified/fail
   verdict, or the rule you use to derive one.
9. **Graph service.** Is `GraphService` yours, or does it belong to ML? What is the `SubgraphJSON` shape
   (nodes/edges keys, edge attributes such as `transaction_id`, `amount`, `timestamp`)?
10. **Env.** Is it OK for the backend process to own `GROQ_API_KEY` / `LLM_PROVIDER` etc., and for our package to
    read them through `AgentConfig`?

## ML

1. **Module paths.** Where will `GraphRiskScorer`, `BehaviorScorer`, `RankFusion` and `FlowInvestigator`
   live, and what are the import paths? Will they be classes that match our Protocols, or functions we wrap?
2. **`DetectorOutput` shape.** Please confirm or correct this: `detector` name, `model_version`,
   `feature_schema_version`, per-alert `score` (what range? higher = riskier?), `rank`, top contributing
   features/rules, and supporting `transaction_ids`.
3. **Unit of scoring.** Do you score transactions, accounts or subgraphs? The aggregator needs to know what
   an "alert" points at (PRD §13 `alerts`: transaction/account/network reference).
4. **`RankFusion`.** Which method (for example rank-mean or a learned ranker), where are the fitted
   coefficients stored, and which `model_run_id` do they refer to? Our mock reports
   `method="mock_rank_mean"`.
5. **Sync or async, in-process or service?** Are calls in-process (heavy imports such as torch/PyG in the
   backend process) or HTTP to a model worker? Roughly how long does one batch take?
6. **Failures.** If GraphSAGE isn't stable (PRD §10 allows dropping it), do we get a missing
   detector or an output with `status="unavailable"`? Can the aggregator just skip it?
7. **Fixtures.** Can you share a small AMLSim sample (fan-out, pass-through, cycle) so our mock
   fixtures match your column names and typology labels?

## RAG

1. **Module path.** Where will `PolicyRetriever` live? Will `search(query, filters, top_k)` be a method we
   can call as-is?
2. **`RetrievedChunk` shape.** Please confirm: `chunk_id` (stable across re-indexing?), `source_title`,
   `source_url`, `section`, `effective_date`, `text`, `score` (what scale?), `indexed_at`. This follows PRD §13 `policy_chunks`.
3. **Filters.** What keys does `filters` accept (source, section, date, typology)? How is permission filtering
   applied (PRD §12)?
4. **Hybrid retrieval.** Keyword plus vector, or vector only for now? Is there a minimum-score cutoff,
   or do we always get `top_k` back?
5. **Retrieval only.** We assume `search` returns raw chunks and agents do the synthesis (node 6). Please
   confirm you're not generating answers on your side.
6. **Sync or async, and latency?** Does it hit Supabase pgvector on each call? How long does a call
   usually take?
7. **Corpus.** Which FFIEC/FinCEN documents are in v1? Can you send 10–20 real chunks so we can
   replace `mocks/fixtures/policy_chunks.json` with them?
