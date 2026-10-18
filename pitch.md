# TraceLayer: pitch and technical briefing

Part 1 is the 4-minute spoken script (about 600 words). Part 2 is the technical briefing behind it, for judge
questions and the deep-dive slot: how each piece is built, why we chose it, and why we built it in this order.
All data is synthetic or public benchmark data, and every metric is a benchmark result, not real-world
performance. Sources of truth: `report.md`, `backend/ml/artifacts/ibm/RESULTS.md`, `docs/trace_layer_prd.md`.

---

# Part 1: the 4-minute script

## 0:00 - The problem (30 s)

Banks move money across ACH, wire, card, cash and P2P. Rules catch single events, but laundering is
connected: funds fan in, pass through, split and come back around. Investigators lose hours stitching
systems together, and every alert raises the same questions: what happened, which accounts are connected,
why was it ranked, and can we trust the data behind it?

## 0:30 - What TraceLayer is (30 s)

TraceLayer is a governed investigation layer. It turns a transaction feed into explainable, evidence-backed
cases. It is decision support: a human always makes the call. It is built to complement platforms like
Oracle OFSAA, not replace them. Our focus is making the chain from source row to final decision visible.

## 1:00 - Data governance and lineage (45 s)

Everything starts with governed data. CSV ingestion maps columns to a canonical schema and runs more than
20 data-quality rules: required fields, timestamps, currencies, duplicates, impossible amounts and schema
drift. Every rejected row is kept with its reason, and a governance gate holds bad batches back from scoring.

On Supabase we have 18 migrations and 41 tables, with row-level security on all of them. Amounts are exact
numeric(20,4), never floats. Every transaction carries a NOT NULL foreign key to its source row, so lineage
runs source row -> transaction -> features -> model and rule -> alert -> case -> decision. The audit log is
append-only: updates and deletes are refused by the database itself.

## 1:45 - Models: what, why, and in what order (60 s)

We built the detection stack from most explainable to most powerful, and each layer had to beat the one
before it on a time-ordered validation split before we kept it.

First, transparent typology rules: fan-in, fan-out, pass-through, dormancy. Easy to explain, weak alone.

Second, an unsupervised Isolation Forest on behaviour features. It needs no labels, so it works on day one.

Third, XGBoost. On the IBM AML HI-Small benchmark we reproduced the published paper: 38.05 against its
38.46. Then we found a leak. The paper-style account features use future data; freeze them at the training
cutoff and AUPRC falls from 47.63 to 12.92. So we rebuilt every account feature from strictly earlier
transactions. That leak-free XGBoost scores 44.92, above the paper's 38.46.

Fourth, a graph neural network, GATv2, that only ever sees transactions strictly before the one it scores.
It reaches 61.33 AUPRC against the paper's 64.85, which lets transactions see later ones in the same window.
It catches most fan-out and gather-scatter patterns. A graph engine traces time-respecting paths and
cycles: on our 282,000-transaction synthetic feed it recovered 59 of 60 injected layering chains in about
7 milliseconds.

## 2:45 - Agents, local LLM and verifier (45 s)

Investigation runs on three LangGraph workflows with bounded nodes. There is no free-form SQL or graph
query. Facts are assembled deterministically. A local Ollama model writes only the narrative synthesis,
and if it is unavailable we fall back to a deterministic template. No customer data goes to an external
model.

Then a verifier with nine error codes checks every transaction ID, amount, date, account and citation,
and flags prohibited language. A draft that fails is redrafted or blocked. The workflow then pauses for a
human, and every human decision produces a hash-chained receipt.

## 3:30 - Honest limits and close (30 s)

What's next: the trained models are verified offline but not yet wired into the live scoring path; that
is our first integration step. Policy retrieval is keyword-only today, and loading official FFIEC and
FinCEN text with embeddings is next. All results are synthetic or benchmark data.

TraceLayer connects financial-crime signals to the governed data behind them: what moved, why it was
ranked, and whether the evidence holds up, with a human making the decision.

---

# Part 2: technical briefing

## 1. Architecture

```
 Next.js UI (8 screens)                      Supabase (managed Postgres)
 - Supabase client, publishable key only     - Auth: admin/investigator/analyst/reviewer/data_steward
 - user-scoped reads under RLS  ──────────▶  - 41 tables, RLS on all 41, tenant-scoped
 - ML and workflow calls go to FastAPI       - Storage: source CSVs, evidence bundles (+ SHA-256 in DB)
        │                                    - pgvector: policy_chunks.embedding vector(768), HNSW index
        ▼                                    - append-only audit_events + audit triggers
 FastAPI backend (backend/app)                         ▲
 - ingestion, data quality, canonical transform        │ server-side key only (never in the browser)
 - graph, alerts, cases, lineage, policy, scoring  ────┘
 - evidence packages, governance routes
        │
        ├──▶ ML worker (backend/ml)
        │     tracelayer/  synthetic-data models (rules, IF, XGBoost, GNN, ensemble)
        │     ibm/         IBM HI-Small benchmark models and evaluation
        │     engine/      alert fusion, behavioural baselines, entity resolution
        │     graph/       GraphBase engine: time-respecting paths, cycles, motifs, propagation
        │
        └──▶ Agents (agents/tracelayer_agents, LangGraph)
              analysis / case / copilot workflows, bounded nodes, durable checkpoints
              local Ollama for narrative synthesis only, deterministic template fallback
```

**Why this split.** The PRD (Sec 12) puts identity, storage, row-level security and vector search on a
managed platform, and keeps heavy and stateful work (feature generation, model inference, graph traversal,
LangGraph orchestration) in a Python worker. We followed that rule:
- **The browser never holds a privileged key.** It reads only what RLS allows that user to see.
- **Anything that changes state or runs a model goes through FastAPI.** FastAPI validates every request
  and writes an audit event.
- **Supabase is still PostgreSQL.** We get exact numerics, foreign keys and triggers for lineage and audit,
  and we didn't build a custom database.

## 2. Ingestion and governance pipeline

What happens when an analyst uploads a CSV (`backend/app/services/ingestion.py`, `quality.py`):

1. **Map.** `POST /ingestion/preview-mapping` proposes a mapping from the file's columns to the canonical
   transaction schema, shows a preview, and lets the analyst correct it before anything is written.
2. **Fingerprint and store.**
   - The file's SHA-256 is computed first. A file already ingested for this tenant is rejected as a
     duplicate (unique on tenant + checksum).
   - The raw file goes to Supabase Storage under `tenant/YYYY/MM/<checksum>_<name>`.
   - A `source_files` row records owner, source system, schema version, uploader, size and checksum.
3. **Open an ingestion run.** An `ingestion_runs` row records status, row count, mapping and timestamps.
   A trigger writes the audit event.
4. **Validate.** Every row passes through 20+ typed rules, each with a stable ID, severity and field:

   | family | rule IDs | what it catches |
   |---|---|---|
   | schema | DQ-SCH-001..003 | required field unmapped, mapped column missing, unmapped columns (schema drift) |
   | required fields | DQ-REQ-001 | missing timestamp, amount, currency, accounts, channel |
   | timestamps | DQ-TS-001..004 | unparseable, implausibly old, in the future, timezone assumed |
   | currency | DQ-CUR-001 | unrecognised ISO code |
   | amounts | DQ-AMT-001..004 | non-numeric, zero or negative, above a plausible maximum, rounds to zero |
   | accounts | DQ-ACC-001 | account identifier cannot be resolved |
   | duplicates | DQ-DUP-001..003 | repeated ID in the file, ID already ingested, near-duplicate content |
   | format | DQ-CHN-001, DQ-STS-001 | unknown channel or status, mapped and flagged |

5. **Keep everything.**
   - Every raw row is stored in `source_rows` with its original values and status (accepted or rejected).
   - Every finding goes to `data_quality_issues` with run, row, field, issue type, severity and a
     remediation status and note.
   - Rejected rows are never silently dropped.
6. **Transform.**
   - Accepted rows become canonical `transactions` with amount as numeric(20,4), `amount_usd` with its FX
     rate, and `occurred_at` as UTC `timestamptz`. The original source values are kept beside the
     normalised ones.
   - Each transaction has `source_row_id NOT NULL UNIQUE` with a foreign key to its source row, so lineage
     is one-to-one and the database enforces it.
   - Accounts and counterparties are resolved or created at the same time.
7. **Fail safe.** If a run fails partway, its partial writes are rolled back and the run is marked failed.
   The audit trail keeps the record.
8. **Governance gate (agent side).** The analysis workflow's `governance` node scores the batch:
   - **pass:** no issues.
   - **qualified:** warnings or info only.
   - **fail:** any critical issue.

   In strict mode a failed batch halts before scoring, and the case goes to "held for data review". Data
   problems are shown to the investigator as caveats. They can raise an alert's priority to review, but
   never lower it.

**Why it matters.** A model score is only as trustworthy as the data under it. Every alert can be traced
back to the exact CSV row, file checksum and quality findings that produced it. That is the PRD's Sec 8
lineage chain, enforced with foreign keys instead of documentation.

## 3. Lineage and audit

- **The lineage chain:**
  - `source_files` -> `source_rows` -> `transactions` (FK, 1:1)
  - -> `features` (`feature_set_version`, `as_of_timestamp`)
  - -> `model_runs` / `risk_signals` (`model_run_id` FK, model and feature version)
  - -> `alerts` (`model_run_id` FK, component signals)
  - -> `case_alerts` -> `cases` -> `case_narratives` / `case_decisions`
- **Database-enforced audit.**
  - `audit_events` is append-only: a trigger raises on UPDATE or DELETE.
  - Automatic triggers record ingestion runs, model runs, alert changes, case status, narrative versions
    and reviewer decisions.
  - Source-file downloads are audited from the API.
- **Agent receipts.** Each human decision in the agent workflow writes a hash-chained receipt, and the UI
  can re-verify the chain.
- **Exports.** Evidence bundles are stored with a checksum. CSV cells that a spreadsheet would execute as
  formulas are neutralised.

## 4. Models: how they work, why we chose them, why in this order

### Why this sequence

The PRD (Sec 5) asks for "XGBoost or comparable, a simple anomaly score, and deterministic typology
detectors", and to "add one GNN experiment only if data prep and evaluation are stable". We built in that
order, and each step served as the baseline for the next:

| step | model | why it comes here |
|---|---|---|
| 1 | Typology rules | Fully explainable floor; every alert names the pattern. Shows what simple logic misses. |
| 2 | Isolation Forest | Needs no labels, so it works on a new bank's feed on day one. A second, independent signal. |
| 3 | XGBoost | The PRD asks us to preserve a tabular model. Strong on tabular data, per-feature contributions for explanations. |
| 4 | GATv2 GNN | Laundering is a network pattern; a GNN sees neighbours that row features cannot. Only added once the time-ordered evaluation was trustworthy. |
| 5 | Ensemble and fusion | Only kept because validation showed a gain; the fusion policy keeps every component signal visible. |

Every model is judged on the same chronological split, with thresholds and model choice made on validation
only. The test set is never used for tuning.

### Data

- **Synthetic AMLSim-style feed** (`ml/tracelayer/generate_data.py`):
  - 282k transactions, 7,560 accounts, 90 days.
  - Normal behaviour (card, P2P, payroll, rent, B2B invoices, merchant settlements) plus seven labelled
    typologies: fan-in, fan-out, cycle, layering, structuring, dormant reactivation, pass-through.
  - Legitimate hubs (merchants, employers, landlords) deliberately look like fan-in and fan-out, so rules
    get realistic false positives.
  - Labels mark injected patterns, not people.
- **IBM AML HI-Small** (public benchmark):
  - 5.08M transactions, 0.10% illicit.
  - Chronological split at the paper's cutoffs: train 2.59M, validation 659k, test 1.24M edges.
  - This is where we compare against Shahbazi et al. (2026).

### Leakage controls (the part we are proudest of)

- **Point-in-time features.** Every feature for a transaction at time t uses only events strictly before
  t. Same-timestamp events are excluded, and results do not depend on input row order.
  `tests/test_ml_features.py` brute-forces this against a reference implementation.
- **The paper's leak.** Paper-style node aggregates are computed over the whole dataset, test period
  included. Frozen at the training cutoff, the tuned XGBoost drops from 47.63 to 12.92. We report the
  leak and serve the point-in-time model instead.
- **Code review found more, and we fixed it:**
  - stale-by-one counterparty counts;
  - cycle features that saw later edges in the same window (retired);
  - a 3-cycle rule calibrated on a train graph that included later edges (retired);
  - a GNN whose batch could see later transactions (now capped per target).
- **The GNN is strict.** Each scored transaction gets its own neighbourhood containing only earlier
  transactions. Sampling is a deterministic function of the transaction itself, so the score doesn't depend
  on what else is scored with it or on input order.

### Model by model

**1. Typology rules** (`ml/tracelayer/features.py`, `ml/ibm/rules.py`)
- **What they check:** fan-in, fan-out, inflow and outflow bursts, rapid pass-through, reciprocal flow,
  dormant-then-large, new-account high value, and a structuring review band (synthetic data only).
- **How:** each rule is "signal >= threshold" on a point-in-time feature. In the calibrated variant, each
  threshold is picked on the training period and each rule is weighted by its training precision.
- **Why:** they are the transparent baseline, and every alert can name its pattern.
- **Result:** test AUPRC 0.93, about 9x the 0.10% base rate but far below the learned models. That gap is
  the argument for the models.
- **Fix from review:** a first-ever transaction is "new", not "dormant". Without that fix the rule fired on
  every new account's first large payment.

**2. Isolation Forest** (`ml/ibm/anomaly.py`)
- **How:** trained on scale-free behaviour features (ratios and rates, not raw volumes).
- **Why we changed the features:** on raw volume features it ranked worse than random (AUROC 45.9),
  because big legitimate hubs are the outliers.
- **Result:** AUROC 85.5. We use it as a secondary ranking signal, not an alert source: at its threshold it
  raises 45 false alerts per 1,000 transactions.

**3. XGBoost** (`ml/ibm/xgb.py`, `ml/ibm/xgb_temporal.py`, served by `ml/ibm/scorer.py`)
- **Reproduction:** the paper's 39 features (14 per account, 11 per edge) and its settings give
  38.05 +/- 0.91 over 5 seeds, against the paper's 38.46.
- **Point-in-time model (served):** those features with account aggregates frozen at the train cutoff,
  plus rolling account state from strictly earlier transactions:
  - in/out counts and sums over 1h, 24h, 7d and 30d;
  - recency and distinct counterparties;
  - pass-through ratio and amount versus the 7-day mean;
  - prior and reciprocal payments between the same pair.
- **Training:**
  - Tuned on validation AUPRC with 1:200 negative subsampling and depth 8.
  - Early stopping on validation picked 938 trees, and serving uses exactly those trees.
  - Operating threshold 0.785, chosen on validation (best F1).
- **Result:** test AUPRC 44.92, false alerts 0.53 per 1,000.
- **Explanations:** per-transaction tree contributions give the top features behind each score.
- **Why XGBoost:** it is the PRD's preserved model and strong on tabular data, and its contributions give
  investigators a reason for each score.

**4. GATv2 graph neural network** (`ml/ibm/gnn.py`)
- **How it works:**
  - An edge classifier: each transaction is scored from its own features plus attention over a sampled
    two-hop neighbourhood (up to 50 then 20 neighbours).
  - Node inputs are entity type and degree counted only from earlier transactions.
  - Edge inputs are amount, time, format and currency mismatch.
- **Why a GNN:** the typologies are network shapes (fan-in, fan-out, gather-scatter, cycles, stacks) that a
  single transaction's features cannot show.
- **Why GATv2:** the paper benchmarks it, attention gives an explanation signal, and it handles
  variable-size neighbourhoods.
- **Result:**
  - Test AUPRC 61.33 (seed 7). The like-for-like model that could see later transactions in the same window
    scored 56.84.
  - F1 at the validation threshold 61.53, false alerts 0.46 per 1,000.
  - It catches most fan-out and gather-scatter patterns at its operating point (per-typology recall in
    `RESULTS.md`).
- **Paper comparison:** 3.5 points below the paper's 64.85, which allows within-window visibility.

**5. Ensemble and alert fusion** (`ml/ibm/ensemble.py`, `ml/engine/fusion.py`)
- **Ensemble:** 0.8 x GNN + 0.2 x XGBoost, with the weight chosen on validation.
  - Each model's score is ranked against its stored validation distribution, so a single new transaction
    can be scored on its own.
  - Test AUPRC 61.22, precision@100 96%, precision@1,000 83.5%. Validation picked it.
- **Alert fusion is not a weighted sum.**
  - Each model flags at its own validation threshold.
  - Priority comes from how many independent families agree: supervised, graph model, rules, graph pattern,
    anomaly. Three or more families make it critical; two make it high; one makes it review.
  - The policy is versioned config (`priority-1.0`).
  - Data-quality caveats can raise priority but never lower it.
- **Why:** the PRD forbids invented blend weights, and investigators need to see every component signal.

**Uncertainty and explainability** (from the paper, reproduced)
- **Conformal prediction** at alpha 0.1 gives coverage of 0.88-0.91 with average set size about 0.97. Some
  sets are empty, which we disclose.
- **GNNExplainer:** its explanations overlap the ground-truth pattern edges 11.6% of the time, against 3.6%
  for random. The paper reports 21.4%.

### Graph engine (`ml/graph`)
- **What it does:**
  - time-respecting path tracing, where money can only move forward in time;
  - bounded cycle and motif search (fan-in, fan-out, split-and-merge, circular);
  - graph metrics: degree, k-core, PageRank, betweenness;
  - network propagation: after a human disposition, 1-2-hop neighbours are queued for review as leads,
    never as labels.
- **Results on the synthetic feed:** 59 of 60 layering chains and 56 of 60 cycles recovered, p50 latency
  7 ms.
- **Safety:** every query is bounded (hops, results, time window). There is no free-form Cypher.

## 5. Agents and the verifier

Three LangGraph workflows with durable checkpoints:

```
analysis:  ingest -> governance --(fail, strict)--> halt: "held for data review"
                       └-> build_graph -> {detect_graphml, detect_behavior} -> aggregate -> investigate_flows

case:      load_case -> retrieve_policy -> draft_narrative -> verify_evidence -> save_draft
                              ^                                                  |
                              └──── verifier errors and attempts < 2 ───────────┤
                                                                                 └-> human_decision (pause)
                                                                                      -> record_decision

copilot:   retrieve_policy -> copilot_answer -> verify_citations
```

- **Facts first, prose second.**
  - Every fact claim (patterns, transactions, per-account sums) and policy claim is built deterministically
    from stored evidence.
  - The local LLM (Ollama) sees those claims as numbered lines and writes only the summary, hypotheses that
    cite claim numbers, uncertainties and follow-up questions.
  - If the model is down or times out, a template writes the synthesis. The facts are identical either way.
- **The verifier** (`nodes/verify.py`) has nine error codes, including unknown transaction or account,
  unsupported amount or date, bad citation, ungrounded token and prohibited language ("criminal", "proven
  laundering").
  - A failing draft is redrafted up to twice, with the errors fed back to the model.
  - If errors remain, it is marked `needs_revision` and the flagged claims are shown as unverified.
  - The backend refuses a final decision until verification passes.
- **Human in the loop.**
  - The case workflow pauses with a real `interrupt()`. A reviewer escalates, dismisses or asks for more
    data.
  - `record_decision` writes the audit event and a hash-chained receipt.
  - Nothing is filed, frozen or sent to a customer.
- **Prompt-injection defence.**
  - Retrieved policy text and transaction memos are treated as untrusted data, escaped and fenced in
    prompts.
  - Agents call fixed functions, never free SQL or graph queries.
  - Tests cover fabricated and injected chunks.
- **Privacy.** There are no external LLM SDK calls in the agents, backend or frontend. Only synthetic or
  public data is used.

## 6. Security and responsible use

- RLS on all 41 tables. Agent tables also revoke `anon` and `authenticated` access.
- The frontend uses only `NEXT_PUBLIC_*` values: the API URL, the Supabase URL and the publishable key.
  The service-role key stays server-side.
- Scores rank review priority. They are never a finding of guilt, and the UI and verifier block that
  language.
- We claim no compliance certification, production readiness or superiority over OFSAA.

## 7. Honest limits (say these before a judge does)

- **Live scoring.** The live scoring path doesn't call the trained models yet; they are verified offline.
- **RAG.** Retrieval is keyword-only today. The vector column and index exist, but nothing generates
  embeddings yet, and the policy corpus is a small placeholder.
- **GNN reporting.**
  - The GNN result is a single seed.
  - Its threshold was chosen before the final per-target sampling fix. A Colab re-evaluation is queued, and
    the scorer labels its output "per-target re-eval pending" until then.
- **Benchmark limits.**
  - 27% of HI-Small's illicit edges belong to no listed pattern. Every model misses them (8% recall at
    most), which caps recall near 73%.
  - The synthetic feed is too easy (XGBoost PR-AUC 0.998). It proves the pipeline, not performance.

---

## Appendix A: numbers (IBM AML HI-Small test split, leakage-fixed code; benchmark results)

| signal | test AUPRC | F1 at val threshold | false alerts / 1k tx | vs Shahbazi et al. (2026) |
|---|---|---|---|---|
| Typology rules (train-calibrated) | 0.93 | 7.09 | 1.50 | rule baseline |
| Isolation Forest (behaviour) | 0.68 (AUROC 85.5) | 0.81 | 45.0 | unsupervised signal |
| XGBoost, paper config (5 seeds) | 38.05 +/- 0.91 | best F1 39.71 | - | paper 38.46: reproduced |
| XGBoost, point-in-time features (served) | 44.92 | 43.82 | 0.53 | +6.46, leak-free |
| GATv2, strictly time-respecting (1 seed) | 61.33 | 61.53 | 0.46 | -3.52 vs 64.85 |
| Ensemble 0.8 GNN + 0.2 XGB (servable) | 61.22 | 59.91 | 0.50 | -3.63; selected on validation |

- Leakage finding: paper-style XGBoost 47.63 -> 12.92 when node features are frozen at the train cutoff.
- Like-for-like GNN: strict 61.33 vs 56.84 for the same settings with within-window visibility.
- Conformal prediction (alpha 0.1): coverage 0.88-0.91, average set size ~0.97 (paper ~0.90 / ~1).
- GNNExplainer overlap with ground-truth patterns: 11.6% (paper 21.4%; random 3.6%).
- Graph engine, synthetic 282k tx: layering chains 59/60, cycles 56/60, p50 7 ms.
- Synthetic AMLSim-style data: XGBoost PR-AUC 0.998. Too easy; it proves the pipeline, not performance.

## Appendix B: likely judge questions

- **Is this better than OFSAA?** We make no accuracy claim against OFSAA. TraceLayer is a companion layer
  that makes provenance and evidence visible, and could sit alongside an incumbent platform.
- **Does a high score mean someone is laundering money?** No. Scores rank review priority. The UI and the
  verifier block words like "criminal" or "proven laundering", and a human decides every case.
- **Why build rules first if the GNN is so much better?** Each layer is the baseline for the next, and the
  rules are what an investigator can read. The gap from 0.93 to 61 is the evidence that the models earn
  their complexity. Fusion keeps the rule hits visible next to the model scores.
- **Why not just use the GNN?** It is the strongest single model, but XGBoost gives per-feature reasons,
  rules name the pattern, and agreement across independent families is what drives priority. One opaque
  score is harder to defend in a review.
- **Why is your GNN below the paper?** Our protocol is stricter: no transaction can see anything at or
  after its own timestamp, and we report one seed. Under the same settings, removing that visibility
  improved our score (56.84 -> 61.33). Details in `RESULTS.md`.
- **How do you know there is no leakage?**
  - Chronological split at the paper's cutoffs.
  - Features only from strictly earlier events, with a brute-force test.
  - Thresholds and model choice on validation only.
  - Several code-review rounds that found and fixed same-second, stale-count, within-window and
    batch-dependence bugs.
- **What happens to bad data?** It is never dropped silently. Every row and finding is stored, a critical
  issue halts the batch before scoring, and caveats travel with the alert.
- **Is the live demo using these models?** Not yet. They are verified offline; wiring them into the live
  scoring path is the first next step.
- **What data did you use?** Synthetic AMLSim-style transactions and the public IBM AML HI-Small
  benchmark. No real customer data.
- **What happens if the LLM is down?** The workflow falls back to a deterministic template, and the
  verifier still runs.
