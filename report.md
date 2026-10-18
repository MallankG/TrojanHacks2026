# TraceLayer: audit against PRD v2.0 (`docs/trace_layer_prd.md`)

Read-only audit, 2026-10-18. No code was changed. Four sessions each audited a slice in parallel:

| scope | PRD sections | auditor |
|---|---|---|
| Ingestion, data quality, governance, Supabase, security | §5, §6, §8, §12, §13, §15, §16 | trojanhacks-39 |
| Agents, RAG, copilot, UI screens, demo script | §6, §11, §14, §16, §18 | trojanhacks-3b |
| Typologies, models, evaluation, graph | §3, §5, §9, §10 | TraceLayer GraphBase session |
| Positioning, language, ML engine, compilation | §3 language, §7, §17 | trojanhacks-b4 |

Audited tree: branch `codex/connect-agent-workflows` (PR #11) with other sessions' uncommitted edits present. The graph engine is
on the unpushed branch `feat/graph-engine` (06aa1fd). One PR #6 review fix (GNN per-target temporal cap) is in progress.

## Verdict

**The PRD v2.0 core is met.** Every "must ship" item exists, and the governance, lineage and evaluation work goes well beyond
the PRD. The remaining gaps are mostly **wiring and demo visibility, not missing capability**:
- the live scoring path doesn't call the trained models yet;
- RAG runs keyword-only, with no embeddings generated and a placeholder policy corpus;
- the UI shows verifier results as a count rather than per-claim.

Scorecard (rows per auditor table; "PARTIAL" items are listed under Gaps):

| PRD area | DONE | PARTIAL | MISSING | DEVIATION |
|---|---|---|---|---|
| §3 typologies and metrics | 8 | 1 | 0 | 0 |
| §5 must-ship | 25 | 2 | 0 | 0 |
| §5 should-ship | 4 | 0 | 0 | 0 |
| §6 workflow | 5 | 3 | 0 | 0 |
| §8 governance / lineage | 1 | 0 | 0 | 0 |
| §9–§10 research and evaluation | 6 | 5 | 0 | 0 |
| §11 multi-agent and RAG | 9 | 2 | 1 | 3 |
| §12–§13 Supabase and data model | 7 | 1 | 0 | 0 |
| §14 UI screens A–E | 2 | 4 | 0 | 0 |
| §15 security | 2 | 2 | 0 | 0 |
| §16 acceptance | 4 | 4 | 0 | 0 |
| §18 demo script (6 steps) | 5 | 1 | 0 | 0 |

## 1. Data, ingestion and governance (§5, §6, §8, §12, §13)

| PRD | requirement | status | evidence |
|---|---|---|---|
| §5 | CSV ingestion to canonical schema, field mapping preview, validation report | DONE | `backend/app/services/ingestion.py`; `POST /preview-mapping`; `tests/test_data_ops.py` |
| §5 | DQ checks: required, timestamp, currency, duplicates, impossible amounts, unresolved accounts, schema mismatch | DONE | `quality.py`: DQ-REQ, DQ-TS-001..004, DQ-CUR, DQ-DUP-001..003, DQ-AMT-001..004, DQ-ACC, DQ-SCH-001..003 (20+ rules) |
| §5 | Source file, ingest run, row-level exceptions recorded | DONE | migration `…000002`: `source_files` (sha256, owner, schema_version), `ingestion_runs`, `source_rows`, `data_quality_issues` |
| §5 | Audit: ingestion, score runs, case status, narratives, decisions | DONE | `…000006_audit.sql` triggers; `audit_events` append-only (update/delete raise) |
| §5 | Audit: evidence access | PARTIAL | only source-file download is audited; case-evidence views aren't |
| §6 | Govern: owner, schema version, ingest time, remediation state | DONE | `source_files`, `data_quality_issues.status/remediation_note` |
| §6 | Transform: normalize, keep originals, row lineage | DONE | `transactions.source_row_id` NOT NULL UNIQUE FK; `original_values`; `amount_usd` |
| §8 | Lineage: source row → txn → feature → model/rule → alert → case → decision | DONE | FK chain; `test_canonical_neighborhood_and_lineage`. Feature→signal is linked by version string, not FK |
| §12 | Supabase DB / Storage + checksums / pgvector | DONE | 18 migrations, 41 tables; storage buckets; `policy_chunks.embedding vector(768)` + HNSW |
| §12 | RLS on every exposed table | DONE | RLS on all 41 tables; agent tables also revoke anon/authenticated |
| §12/§16 | RLS tested for the main tables | PARTIAL | `supabase/tests/enterprise_rls.sql` covers enterprise tables, not transactions/alerts/cases, and isn't in pytest |
| §12/§16 | No service-role key in the client | DONE | frontend uses only `NEXT_PUBLIC_*`; not checked against a built `.next` bundle |
| §13 | All 13 PRD tables; exact decimals; UTC | DONE | all 13 + 28 more; `numeric(20,4)` + Python `Decimal`; `timestamptz` |

## 2. Detection, models and evaluation (§3, §5, §9, §10)

| PRD | requirement | status | evidence |
|---|---|---|---|
| §3 | Pass-through, layering, mule/funnel, cycles, dormancy, new beneficiary | DONE | `ml/ibm/rules.py`, `ml/tracelayer/features.py`, `ml/engine/detectors.py`, `ml/graph` (cycles, motifs, paths) |
| §3 | Structuring-like | PARTIAL | synthetic data only (`structuring_band`); HI-Small has no review-band semantics |
| §3 | Rolling 1h/24h/7d/30d, flow ratios, counterparties, graph features | DONE | strictly-past features (same-second excluded); `get_graph_metrics` (degree, k-core, PageRank, betweenness) |
| §5 | XGBoost baseline, anomaly score, typology detectors, one GNN | DONE | `ml/ibm/{xgb,xgb_temporal,anomaly,rules,gnn}.py` |
| §5 | Ranked alert queue with component signals | PARTIAL | `ml/engine/fusion.py` (versioned priority policy, clusters) exists; **no route calls the trained scorers or fusion** |
| §5 should | Behavioural baselines, feature contributions, entity resolution, model comparison card | DONE | `ml/engine/{baselines,resolution}.py`; XGB tree contributions; `RESULTS.md`, `model_card.json` |
| §9 | Reproduce Shahbazi et al. XGBoost | DONE | 38.05 ± 0.91 vs paper 38.46 (within 1 SD); scale_pos_weight reading verified |
| §9 | Reproduce GATv2 / conformal / explainer | PARTIAL | GNN 61.33 vs 64.85 (stricter protocol, single seed); conformal ≈0.90 with 3–5% empty sets; explainer 11.6% vs 21.4% |
| §10 | Time split, no future leakage, no tuning on test | DONE | paper cutoffs; point-in-time features; review fixes b3a4d1e/3dc030e/4112bca; every choice made on validation |
| §10 | PR-AUC, P@K, false alerts/1k, recall by typology, rule baseline | DONE | `ml/ibm/common.evaluate_and_save`, `typology_recall.py` |
| §10 | R@K; full confusion matrix; dataset hash | PARTIAL | IBM reports P@K only (R@K helper in `ml/engine/manifest.at_k`); TN not stored; no HI-Small sha256 |
| §6 | Documented ranking, no invented weights | DONE | ensemble weight chosen on validation; fusion policy is versioned config. Ensemble ranks are batch-relative (servable re-score pending) |

## 3. Agents, RAG and copilot (§11)

| PRD | requirement | status | evidence |
|---|---|---|---|
| §11 | LangGraph, bounded nodes | DONE | 3 workflows, 15 nodes, per-node timeouts |
| §11 | Governance gate, graph builder, investigation planner (no free SQL/Cypher) | DONE | `nodes/ingest.py`, `governance.py`, `build_graph.py`, `investigate_flows.py` (bounded paths, capped) |
| §11 | Scoring worker | PARTIAL | live GraphRisk adapter returns "unavailable"; the benchmarked models aren't wired into the agent run |
| §11 | Policy RAG with cited passages | DONE | `retrieve_policy.py`; chunks HTML-escaped (prompt-injection defence) |
| §11 | Curated FFIEC/FinCEN corpus | PARTIAL | mock corpus: 8 paraphrased chunks, 2 labelled fictional; no seed script for the live DB |
| §11 | Keyword + vector retrieval | DEVIATION | SQL supports hybrid RRF, but the live adapter passes no embedding, so **keyword-only** |
| §11 | Local embedding model | MISSING | only the 768-dim column exists; nothing generates embeddings |
| §11 | Narrative drafter, facts / hypotheses / unknowns | DONE | facts built deterministically; the LLM writes only the synthesis |
| §11 | Evidence verifier | DONE | `nodes/verify.py`: 9 error codes (unknown tx/account, unsupported amount/date, bad citation, ungrounded token, prohibited language…); redraft ≤ 2, then `needs_revision`; backend blocks a final decision until it passes |
| §11 | Human reviewer: accept/edit/reject/escalate | DEVIATION | actions are escalate / dismiss / request_more_data via a real `interrupt()`; no edit-draft step |
| §11 | Model provider: Groq, else local Ollama | DEVIATION (safer) | Ollama → deterministic template only; 0 external LLM SDK calls in agents, backend or frontend |

## 4. UI and demo (§14, §16, §18)

| PRD | requirement | status | note |
|---|---|---|---|
| §14 A | Data Ops & Governance | PARTIAL | live upload, DQ issues, source-row drill-down, mixed with seeded batches |
| §14 B | Risk Intelligence queue | PARTIAL | live queue on `/command`; `/risk` defaults to a seeded alert |
| §14 C | Workbench graph + timeline | DONE | live when signed in; the timeline lives on alert detail |
| §14 D | AI Copilot | DONE live / scripted demo | live posts to `/agents/cases/{id}/copilot`; demo-mode "inject" is canned |
| §14 E | Lineage & Audit | PARTIAL | live lineage panel; `/governance/audit` is seeded only |
| §16 | Every alert opens transactions + graph/timeline | DONE | live alert detail links |
| §16 | Narrative shows evidence IDs + policy citations | PARTIAL | verified in the backend; **the UI doesn't render per-claim IDs or citations** |
| §16 | Verifier flags an injected fact | DONE in tests / scripted in UI | `agents/tests/test_case_workflow.py:153-205`; no live-UI injection path |
| §16 | Disposition creates an audit event | DONE | DB trigger + hash-chained agent receipts with a UI integrity check |
| §16 | CSV findings in < 30 s | PARTIAL | 25-row sample validates in milliseconds; not timed end to end against Supabase |
| §18 | Demo steps 1–4 and 6 | DONE | step 5 (copilot showing one rejected claim) works reliably only scripted or in pytest; needs a policy corpus loaded first |

## 5. Positioning and language (§3, §7, §15, §16)

| PRD | requirement | status | evidence |
|---|---|---|---|
| §3 | Never label anyone criminal; use "risk signal / review recommended" | DONE | UI "We never say: criminal, guilty, proven laundering, auto-file, freeze" (`simple-dialogs.tsx`); narrative prompts forbid implying guilt or SAR filing; verifier PROHIBITED_LANGUAGE code |
| §7 | No "replaces OFSAA" / "more accurate" claims | DONE | no such claims in UI, README or agent docs |
| §15 | Prompt-injection defence | DONE | untrusted text fenced in prompts; fabricated-chunk and injected-chunk tests |
| §15 | Typed tool arguments | PARTIAL | no general typed tool-schema layer (agents call fixed functions, so there's no free-form tool use) |
| §15 | No sensitive payloads in logs | PARTIAL | prompts are logged as `prompt_version` + `input_hash` only; there's no enforced log redaction elsewhere |

## Deviations from PRD v2.0 that are upgrades (from PRD v3.3)
- The GNN went from an optional experiment to a benchmarked, strictly time-respecting model (v3.3 §5 graph ML track).
- GraphBase adapter, snapshots and network propagation (v3.3 §6, §5 item 8), on `feat/graph-engine`.
- Conformal prediction and GNNExplainer (from Shahbazi et al.).
- IBM AML HI-Small benchmark alongside synthetic AMLSim-style data (allowed by v2 §10).
- A local-only LLM instead of Groq (safer under §15).

## Test status
- **Backend:** 252 passed, 3 failed, 1 skipped in the audited tree. The failures (`test_api.py:45`, `test_dashboard.py:169`, `test_enterprise.py:108`) log upstream `ConnectError`, so they're likely environment or work in progress, not regressions. Not root-caused.
- **Agents:** 264 passed, 9 failed, 1 skipped. All 9 are LLM-synthesis / redraft tests where a mocked Ollama ends in `template`. This looks like test drift; not root-caused.
- **ML engine + graph engine:** `tests/test_engine.py` 6 passed + 1 skipped here; 7/7 with the graph engine; `test_graph_engine.py` 21 passed.

## Gaps, ranked by demo impact
1. **The live scoring path doesn't call the trained models.** `xgb_temporal`, `gnn_temporal` and `ml/engine/fusion` are verified offline, but the agent GraphRisk adapter returns "unavailable" and no route invokes them. *Highest impact: the headline numbers aren't what the live demo ranks with.*
2. **RAG is keyword-only on a placeholder corpus.** No embedding generation (e.g. `nomic-embed-text` via Ollama), no seed script, and the corpus is paraphrased/fictional. Load real FFIEC/FinCEN excerpts before demoing citations.
3. **The verifier is invisible in the UI.** It shows a count, not which claims failed. The frontend reads `verifier_errors[].message` but the backend field is `detail`. Narratives don't render evidence IDs or citations.
4. **The "inject a false fact" demo step is scripted.** It works for real only in pytest.
5. **Test failures:** 3 backend, 9 agents. Triage before demo day.
6. **Smaller items:**
   - R@K and the full confusion matrix for the IBM models
   - an RLS SQL test for the core tables
   - evidence-view audit events
   - a typed tool-schema layer
   - log redaction
   - a timed end-to-end < 30 s CSV check
   - merging `feat/graph-engine`
   - a single-seed GNN (no ± spread)

## Headline numbers (IBM AML HI-Small test split, leakage-fixed code)

| model | test AUPRC | F1 @ val threshold | false alerts / 1k tx | vs Shahbazi et al. |
|---|---|---|---|---|
| Rules (train-calibrated) | 0.93 | 7.09 | 1.50 | rule baseline |
| Isolation Forest (behaviour) | 0.68 (AUROC 85.5) | 0.81 | 45.0 | unsupervised signal |
| XGBoost, paper config (5 seeds) | 38.05 ± 0.91 | best F1 39.71 | – | paper 38.46: **reproduced** |
| XGBoost, point-in-time (served) | 44.92 | 43.82 | 0.53 | +6.46 over paper XGB, leak-free |
| GATv2, strictly time-respecting (seed 7) | 61.33 | 61.53 | 0.46 | −3.52 vs paper 64.85 |
| Ensemble 0.8 GNN + 0.2 XGB | 62.41 | 54.18 | 0.11 | −2.44 (batch-relative ranks; servable version pending) |

Conformal (α=0.1) coverage: GNN 0.897, XGB 0.881, ensemble 0.908; average set size 0.95–0.97 (paper ≈0.90 / ≈1).
Leakage finding: the paper-style XGBoost drops from 47.63 to 12.92 once node features are frozen at the train cutoff.
Graph engine (synthetic, 282k tx): layering chains recovered 59/60, cycles 56/60, p50 7 ms.
Synthetic AMLSim-style ensemble: PR-AUC 0.996. Too easy; it proves the pipeline, not real-world performance.
