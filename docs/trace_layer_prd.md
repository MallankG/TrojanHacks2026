TraceLayer
Financial Crime Intelligence & Governed Investigation
Product Requirements Document | v2.0 | October 2026

24-hour hackathon prototype • U.S. bank / fintech context • Synthetic data only
Positioning: a focused, evidence-backed investigation layer that complements existing financial-crime platforms; not a replacement for OFSAA or a production compliance system.

1. Executive summary
TraceLayer is a governed financial-crime investigation layer for U.S. banks and fintechs. It turns transaction feeds into traceable, explainable investigation work: source-data quality and lineage, transaction and account risk signals, connected money-flow exploration, evidence-backed case narratives, and human-reviewed case outcomes.
The hackathon prototype is not a core banking system, a regulatory filing engine, or a replacement for an enterprise AML platform. It demonstrates one high-value workflow end to end using synthetic transactions: ingest, validate, map entities, build a transaction graph, rank suspicious activity, investigate related flows, retrieve relevant policy guidance, verify the evidence, and preserve an audit trail.
Primary value proposition: connect financial-crime analytics to the data that produced them. Every alert should let an investigator answer: What happened financially? Which accounts and transactions are connected? Why was this ranked? Is the source data reliable? Which facts support the narrative? Who reviewed the result?
2. Problem and opportunity
Banks process large volumes of ACH, wire, card, cash, check, and person-to-person payment activity. Rule-based scenarios remain useful, but isolated rules can miss connected behavior across accounts, channels, and time. Investigators also lose time reconciling data quality issues, hopping between systems, reconstructing money flows, and assembling defensible evidence.
TraceLayer focuses on the gap between raw transaction data and a reviewable investigation. Its differentiator is not simply an AI-generated summary or a risk score. It binds financial signals to transaction-level evidence, graph relationships, source lineage, data-quality status, model version, policy references, and investigator actions.
3. Financial use cases and typologies
MVP use cases
Rapid in-and-out / pass-through behavior: incoming funds followed by outgoing transfers within configurable time windows, with retained-balance and flow-ratio features.
Layering and multi-hop movement: funds moving through several accounts or entities, with graph paths, time gaps, amount continuity, and possible fragmentation or aggregation.
Potential mule-account networks: accounts receiving funds from many unrelated senders and rapidly dispersing them to a smaller set of recipients, or accounts that sit between suspicious network clusters.
Funnel-like activity: many geographically or behaviorally diverse sources feeding a common account or a small group of accounts.
Circular flows and suspicious cycles: funds moving through a cycle or returning to an origin-related account; display as an investigative lead, not proof of laundering.
Structuring-like patterns: repeated transactions near configurable review bands or patterns designed to avoid attention. Thresholds must be policy-configurable and must not be presented as a universal legal rule.
Dormant-to-active and behavioral deviation: sudden transaction bursts, unusually large amounts, new counterparties, or channel shifts compared with an account's prior baseline.
Possible scam/fraud proceeds: rapid beneficiary changes, new payees, high velocity after account changes, or downstream dispersal. These are risk indicators, not definitive account-takeover findings.
Financial context and metrics
Transaction amount, currency, channel, direction, timestamp, originator/beneficiary, account age, and available customer-risk attributes.
Rolling 1-hour, 24-hour, 7-day and 30-day counts, amounts, unique counterparties, average/median ticket, amount deviation, inter-transaction time, and burstiness.
Inflow/outflow ratio, net flow, retained balance proxy, counterparty concentration, source/destination diversity, amount fragmentation/aggregation, and multi-hop path summaries.
Graph features: degree, weighted degree, reciprocal edges, cycle participation, shared counterparties, neighborhood risk, and time-respecting paths.
Risk context hooks: KYC/customer profile mismatch, entity resolution, beneficial ownership, sanctions/PEP screening, high-risk geography, and prior reviewed cases. These are integration points for the demo, not claims that live screening data is supplied.
Important: the prototype uses synthetic data. It must not label an individual or transaction as criminal. UI language should use “suspicious pattern,” “risk signal,” “review recommended,” and “evidence observed.”
4. Users and jobs to be done
AML/BSA investigator: understand the financial pattern, trace connected accounts, inspect evidence, and draft a case summary.
Fraud operations analyst: identify rapid movement and connected beneficiaries, then escalate for review.
Data steward / financial data owner: find missing fields, invalid timestamps, schema drift, duplicate events, and inconsistent identifiers before those defects undermine analytics.
Compliance manager / model risk reviewer: inspect why an alert was ranked, which model/rules ran, what evidence was used, and what the investigator decided.
Data/AI platform owner: trace the path from source feed through transformations and features to the final score.
5. Product scope
Must ship in 24 hours
CSV ingestion for a documented canonical transaction schema, with field mapping and validation report.
Data-quality checks: required fields, timestamp/currency validation, duplicate detection, impossible amounts, unresolved account IDs, and schema mismatch. Record source file, ingest run, and row-level exceptions.
A transaction/account graph with time-aware links and interactive path exploration.
A baseline model using XGBoost or a comparable tabular classifier, a simple anomaly score, and deterministic typology detectors. Add one GNN experiment only if data prep and evaluation are stable.
A ranked alert queue with component signals, supporting transactions, time window, involved accounts, model/rule version, and clear limitations.
Investigation workbench with chronological transaction timeline, graph view, path summary, amounts, counterparties, and links back to source records.
RAG-based policy assistant grounded in curated public sources such as FFIEC BSA/AML guidance and FinCEN materials, with citations to retrieved passages.
Narrative draft generated from verified facts only. A verifier checks transaction IDs, dates, amounts, account references, and citation presence against stored evidence before display.
Audit events for ingestion, score runs, case status changes, narrative versions, reviewer decisions, and evidence access where implemented.
Should ship if time permits
Customer/account behavioral baseline comparison, explainability via feature contributions, alert feedback capture, and reviewer disposition labels.
Entity-resolution demo using deterministic normalization and fuzzy matching on synthetic names/business identifiers.
Model comparison card showing chronological test metrics and top-K alert utility.
Explicitly out of scope
Production-grade real-time payment interdiction, account freezes, automated SAR filing, production sanctions screening, cross-bank identity sharing, and regulatory-grade model validation.
Kafka streaming infrastructure, LSTM forecasting, autonomous NL-to-SQL/Cypher, complex network contagion simulation, production deployment controls, and a custom graph database.
Claiming that a risk score proves money laundering, or using an LLM to make final compliance decisions.
6. Product workflow
Ingest: upload a synthetic transaction file and map columns to the canonical schema.
Govern: show row-level validation, quality exceptions, source owner, schema version, ingest timestamp, and remediation state.
Transform: normalize timestamps/currencies/identifiers, retain source-to-canonical row lineage, and construct account/transaction relationships.
Score: run typology rules, anomaly detection, and the chosen supervised model; record model and feature versions.
Prioritize: rank alerts using a documented strategy. Prefer a learned ranker only if trained and evaluated; otherwise show component scores and transparent configurable ranking rather than invented weights.
Investigate: select an alert, trace time-respecting paths, inspect related transactions and counterparties, and view data-quality warnings that may affect confidence.
Assist: retrieve policy excerpts and draft a factual narrative referencing evidence IDs and retrieved sources.
Verify and review: run factual checks, show unresolved discrepancies, let a human edit/accept/reject/escalate, and save the decision with an audit event.
7. Differentiation from Oracle OFSAA
Oracle Financial Services Analytical Applications (OFSAA) is a broad enterprise suite spanning risk, performance, compliance, customer insight, shared data models, integration, metadata, and processing frameworks. Oracle's Financial Crime and Compliance Management portfolio already advertises transaction monitoring, behavioral analytics, graph intelligence, entity resolution, AI-assisted investigations, case management, model governance, and SAR-support workflows. TraceLayer must acknowledge this overlap.
Positioning
TraceLayer is a focused, hackathon-scale investigation and data-traceability layer, not an OFSAA replacement. Its product hypothesis is that an institution can inspect the chain from a source row to data-quality checks, normalized transaction, graph features, risk signals, retrieved policy, generated narrative, and reviewer decision in one lightweight workflow. It is designed to complement existing monitoring/case systems through imports and APIs, not assume they are absent.
Dimension
Oracle OFSAA / Oracle FCCM
TraceLayer prototype
Scope
Broad enterprise financial-services applications and compliance ecosystem
One focused financial-crime investigation workflow
Deployment / economics
Enterprise platform and implementation program
Hackathon prototype; lightweight integration concept
Financial detection
Established transaction monitoring, scenarios, behavioral analytics, graph intelligence and scoring capabilities
Transparent typology signals plus benchmarked baseline and optional GNN experiment
Investigation
Enterprise case/investigation capabilities and AI assistance
Graph-to-evidence workbench with fact-checked narrative draft
Governance
Enterprise product controls for rules/models/access and operational governance
Visible source lineage, data-quality status, model version, citations and reviewer audit trail for the demo
Relationship
Could be an incumbent platform in a bank's stack
Potential companion layer or research prototype; no claim of compatibility until tested

What not to claim: “OFSAA cannot do graph analytics,” “OFSAA has no GenAI,” “TraceLayer is more accurate,” or “TraceLayer replaces OFSAA.” Official Oracle materials describe capabilities in these areas. The defensible claim is narrower: TraceLayer makes provenance and evidence flow unusually visible in a compact, inspectable prototype, and can demonstrate how that experience might complement existing systems.
Judge-facing line: “We are not rebuilding OFSAA. We are prototyping the governed investigation layer around a financial-crime alert: from source quality and lineage, through connected money-flow evidence, to a verified human-reviewed case narrative.”
8. Data-Sleek alignment
Data-Sleek's public positioning emphasizes data strategy, integration/centralization, governance, architecture, data quality, metadata, lineage, and activation through analytics and AI. TraceLayer connects that philosophy to a concrete financial-services workflow.
Data governance: source ownership, schema contracts, quality rules, exception handling, and remediation status.
Metadata and lineage: source file/row → canonical transaction → derived feature → model/rule result → alert → case narrative and reviewer decision.
AI-ready data: quality warnings are visible to investigators and model consumers instead of being hidden behind a score.
Data activation: the governed transaction layer powers graph analysis, anomaly detection, model scoring, RAG, and case workflow.
This is a product-philosophy fit, not an assertion of a Data-Sleek partnership, customer relationship, or existing API integration.
9. Research basis and model strategy
Relevant recent research
Shahbazi et al. (2026), “Explainable and Trustworthy AI for Anti-Money Laundering: A Graph-based Hybrid Framework for Real-World Financial Crime Detection,” arXiv:2609.37155. It evaluates graph attention models against tabular baselines on IBM AML Transactions HI-Small and explicitly studies explanations and uncertainty. Treat reported results as a preprint result to reproduce, not a guaranteed result for TraceLayer. https://arxiv.org/abs/2609.37155
Poon et al. (2026), “LineMVGNN: Anti-Money Laundering with Line-Graph-Assisted Multi-View Graph Neural Networks,” arXiv:2603.23584. It motivates modeling payment/receipt directions and transaction-edge relationships. Useful as a research direction, too ambitious to reimplement in the hackathon. https://arxiv.org/abs/2603.23584
Deprez et al. (2025), “Advances in Continual Graph Learning for Anti-Money Laundering Systems: A Comprehensive Review,” WIREs Computational Statistics / arXiv:2503.24259. Highlights evolving typologies, class imbalance, and the challenge of adapting models without forgetting. Use this to justify future drift monitoring, not a continual-learning MVP. https://arxiv.org/abs/2503.24259
Bonato and Szava (2025), “Network Embedding Analysis for Anti-Money Laundering Detection,” arXiv:2509.10715. Uses network embeddings and cycle-focused features on anonymized banking data. Supports testing graph structure and cycle context alongside tabular features. https://arxiv.org/abs/2509.10715
GM-DualTransAML++ (2026), “A Multi-Source Graph–Transformer Framework with Statistical Risk Mining and Typology Supervision for Anti-Money Laundering Detection,” Annals of Data Science. Combines graph mining, statistical risk, sequential behavior, and typology supervision. Treat the reported metrics as dataset/split-specific and not directly comparable with other papers. https://link.springer.com/article/10.1007/s40745-026-00730-3
Research caution: papers use different datasets, label definitions, split strategies, graph construction, and class prevalence. Published metrics cannot be compared as if they came from one benchmark. A paper's reported score is not a performance promise for TraceLayer.
10. Model design and evaluation
Practical model stack
Baseline: XGBoost (or HistGradientBoosting if installation is a blocker) using transaction, account-window, and graph-derived features.
Anomaly component: Isolation Forest or robust unsupervised score to surface behavior unlike the observed baseline. It is a ranking signal, not a probability of crime.
Typology engine: deterministic, configurable detectors for rapid in/out, many-to-one funnel behavior, one-to-many dispersal, cycles, bursts, and dormant reactivation.
Graph experiment: GraphSAGE or GATv2 edge/transaction classifier if labels and graph setup support it. Use time-respecting graph construction and avoid future-neighbor leakage. If the experiment is not stable, present the graph as investigative context and do not pretend a GNN is deployed.
Explainability: show the features/rules that contributed, supporting transaction IDs, path evidence, and limitations. Never expose attention weights as a complete explanation by themselves.
Evaluation protocol
Use AMLSim-generated transactions and/or IBM AML Transactions HI-Small where licensing and dataset access permit; clearly identify synthetic/benchmark data.
Split by time, not random rows alone. Build features using only information available before the prediction timestamp. Keep related graph edges from leaking future information.
Report PR-AUC, precision@K, recall@K, false alerts per 1,000 transactions, recall by synthetic typology, and confusion matrix at a stated operating threshold. ROC-AUC alone is insufficient under extreme imbalance.
Compare the model with a simple rule baseline on the same split and feature availability. Save dataset version, split dates, code/config version, random seed, and model artifact.
Do not tune on the test set. If the demo data is tiny or synthetic, label metrics as a demonstration, not real-world validation. Avoid arbitrary combined scores unless weights are explicitly configurable and tested.
For a 24-hour build, the best evidence is a reproducible, honest comparison and an end-to-end traceable case, not a complicated architecture with unverified accuracy.
11. Multi-agent and RAG architecture
Use LangGraph to orchestrate a small set of bounded agents/tools. Most stages should be deterministic code or ML services, not free-form LLM calls.
Ingestion/governance worker: schema mapping, validation, ownership and lineage events.
Graph builder: creates account nodes, transaction edges, time windows and derived graph features.
Scoring worker: runs rules, baseline ML, anomaly detection and optional graph model; emits structured evidence.
Investigation planner: selects bounded graph/path queries and assembles transaction evidence. No unrestricted generated SQL/Cypher.
Policy RAG agent: retrieves only from curated official guidance and returns short cited passages with source URL/title/section.
Narrative drafter: turns structured evidence into a draft, separating facts from hypotheses and explicitly listing unknowns.
Evidence verifier: checks every transaction ID, amount, date, account and policy citation against source records; blocks or flags unsupported claims.
Human reviewer: accepts, edits, rejects or escalates. The system does not autonomously file SARs, freeze accounts, or decide guilt.
RAG corpus: start with selected FFIEC BSA/AML Examination Manual pages and relevant FinCEN public guidance. Store document title, URL, publication/update date when available, section heading, chunk text, embedding, and retrieval timestamp. Use keyword plus vector retrieval where feasible. Keep regulatory guidance separate from transaction evidence.
Model providers: use Groq-hosted inference if the chosen model and free quota are available on hackathon day; otherwise use a local Ollama model. Use a local embedding model such as nomic-embed-text if supported. Never send raw customer data to an external model in a real deployment without an approved privacy/security review.
12. Supabase-based architecture
Replace the standalone PostgreSQL deployment in the application architecture with Supabase as the managed backend platform. Important distinction: Supabase itself uses PostgreSQL; this is a platform change, not a change away from PostgreSQL as the underlying database.
Supabase Database: canonical transactions, accounts, counterparties, ingestion runs, quality exceptions, alert records, case records, model runs, evidence references, audit events, policy chunks, and embeddings.
Supabase Auth: prototype investigator and reviewer identities. Use role claims/metadata carefully; enforce authorization in the backend and with database policies.
Row Level Security (RLS): enable and test RLS for every exposed table. Model bank/tenant/case scope explicitly even in the demo. Never expose the service-role/secret key to the browser.
Supabase Storage: store synthetic source files, model artifacts only if appropriate, and exported case bundles. Store metadata and checksums in the database.
pgvector extension: store policy embeddings and perform vector similarity search for RAG; apply permission filtering to retrieved chunks.
FastAPI: owns ingestion, heavy model inference, graph construction, feature generation, evidence verification and LangGraph orchestration. Use a server-side secret for privileged operations, connection pooling where needed, and explicit request validation.
Next.js: UI and Supabase client for authenticated user-scoped reads; use FastAPI for ML/workflow operations. Do not put model training or long-running graph jobs in browser code or short-lived edge functions.
24-hour implementation rule: use Supabase's hosted database/Auth/Storage/pgvector where available, but keep graph computation and model execution in the FastAPI worker. Do not build a custom streaming stack. If time is tight, seed the synthetic dataset and use a single idempotent scoring endpoint.
13. Data model (initial)
Table
Purpose / key fields
source_files
file_id, filename, checksum, source_system, owner, schema_version, uploaded_by, uploaded_at
ingestion_runs
run_id, file_id, status, row_count, accepted_count, rejected_count, started_at, completed_at
data_quality_issues
issue_id, run_id, row_reference, field_name, issue_type, severity, status, remediation_note
accounts
account_id, customer_ref, account_type, open_date, status, risk_context fields when synthetic
counterparties
counterparty_id, normalized_name, entity_type, jurisdiction, resolution_status
transactions
transaction_id, source_row_ref, timestamp, amount, currency, channel, direction, origin_account_id, destination_account_id, transaction_status
features
entity/transaction reference, feature_set_version, as_of_timestamp, feature JSON or normalized feature columns
model_runs
run_id, model_name, model_version, feature_version, dataset_version, split_config, metrics, created_at
alerts
alert_id, transaction/account/network reference, component signals, rank, status, explanation refs, model_run_id
cases
case_id, title, status, assigned_to, disposition, created_at, updated_at
case_evidence
case_id, evidence_type, source_ref, transaction_ids, explanation, added_by, created_at
policy_chunks
chunk_id, source_title, source_url, section, effective_date, text, embedding, indexed_at
audit_events
event_id, actor_id, action, object_type, object_id, timestamp, metadata

Keep transaction amounts as exact decimal/numeric values rather than binary floating point for financial display. Preserve original source values and normalized values separately when transformation is material. Use UTC timestamps internally and display the timezone explicitly.
14. UI flows
Screen A: Data Operations & Governance
Upload synthetic CSV, map fields, show source owner/schema, accepted vs rejected rows, missing identifiers, duplicates, invalid timestamps/currencies and remediation state.
Click a bad row to see source row, validation result, normalized value if any, and downstream alerts/features that may be affected.
Screen B: Risk Intelligence
Ranked alert queue with suspected typology, estimated amount in the selected window, number of connected accounts, contributing signals, data-quality caveat, and last model run.
Avoid opaque red/green “guilty” indicators; use review-priority bands with explanation.
Screen C: Money-Flow Investigation Workbench
Interactive graph and chronological timeline; select a path to see amounts, timestamps, sender/receiver, time gaps, and path length.
Show that path tracing is investigative context. Do not imply funds can always be precisely traced through commingled balances.
Screen D: AI Investigation Copilot
Agent run status; verified transaction evidence; retrieved official policy excerpts; draft narrative; unsupported-claim warnings; human edit and disposition controls.
Screen E: Lineage & Audit
Source row → canonical transaction → features → model/rule version → alert → case evidence → narrative revision → reviewer action.
A coherent end-to-end path across these screens is more valuable than six shallow dashboards.
15. Security, privacy, and responsible use
Use synthetic or public benchmark data only in the hackathon. Do not upload real customer account details, KYC documents, or SAR information to public model APIs.
Apply Supabase RLS to exposed data; validate authorization server-side; keep secret keys in server environment variables; restrict logs from recording sensitive payloads.
Treat transaction descriptions and retrieved documents as untrusted input. Defend against prompt injection and tool misuse by using allowlisted retrieval and typed tool arguments.
LLM outputs are drafts. A human investigator is responsible for case disposition and any regulated reporting decision.
Keep evidence references, model versions, prompt/config versions and reviewer changes so a demo decision can be reproduced.
Do not claim compliance certification, production readiness, or regulatory approval.
16. Success criteria and demo acceptance
A judge can upload the sample CSV and see row-level data-quality findings in under 30 seconds on the demo machine.
At least three typology scenarios are represented in synthetic data and produce visible, evidence-backed signals.
Every displayed alert opens supporting transactions and a graph/timeline view.
Every generated narrative includes evidence IDs and policy citations; verifier flags deliberately injected unsupported facts.
A reviewer can record disposition and see an audit event.
A reproducible evaluation compares a simple rule baseline with XGBoost; optional GNN metrics are included only if the pipeline runs correctly.
Supabase RLS policies are tested for the main exposed tables, and no secret/service-role key appears in client code.
The pitch clearly says synthetic prototype, decision support, and complement-to-existing-systems rather than OFSAA replacement.
17. 24-hour execution plan
Time window
Build target
Exit condition
Hours 0–2
Lock schema, demo scenario, repository and Supabase project
Seed data loads; canonical schema documented
Hours 2–5
Ingestion, data-quality checks, lineage tables and basic UI
Bad rows and lineage are visible
Hours 5–9
Typology features, graph construction, alert queue
Three synthetic scenarios surface evidence
Hours 9–13
XGBoost baseline, evaluation split and metrics
Baseline metrics and run config reproducible
Hours 13–16
Graph visualization and investigation timeline
Alert traces to connected transactions
Hours 16–19
RAG policy retrieval, narrative drafting and evidence verifier
Citations and fact checks visible
Hours 19–22
Supabase RLS/auth, audit flow, integration fixes
Core permissions and case disposition work
Hours 22–24
Test, seed reset, demo script and pitch
Reliable 4–6 minute demo; honest limitations stated

Cut order if behind: remove GNN training first, then fuzzy entity resolution, then advanced agent choreography. Do not cut evidence linking, source-quality visibility, reproducible baseline evaluation, or the human-review step.
18. Demo script and pitch
Start with a synthetic transaction feed containing both normal behavior and three known suspicious patterns.
Show a malformed row and explain how data quality can undermine downstream financial-crime detection.
Run detection and open a high-priority alert. Show component signals and the exact supporting transaction IDs.
Trace a multi-hop flow and compare timestamps/amounts. Highlight uncertainty and data-quality caveats.
Ask the copilot for a case draft. Show retrieved official guidance, verified facts, and one intentionally rejected unsupported claim.
Record a human disposition and show the lineage/audit chain.
Pitch: “TraceLayer connects financial-crime signals to the governed data behind them. It helps an investigator see the money flow, understand why it was flagged, verify the underlying evidence, and preserve how the case was assembled. It is designed to complement existing bank platforms, not replace them.”
19. References
Oracle, About Oracle Financial Services Analytical Applications (OFSAA): https://docs.oracle.com/en/industries/financial-services/ofs-analytical-applications/data-governance-north-america-regulatory-reporting/81230/dgsrn/oracle-financial-services-analytical-applications-ofsaa.html
Oracle, Transaction Monitoring – AML and Financial Crime Compliance: https://www.oracle.com/financial-services/aml-financial-crime-compliance/transaction-monitoring/
Oracle, Financial Services Crime and Compliance Studio: https://www.oracle.com/financial-services/aml-financial-crime-compliance/crime-compliance-studio/
FFIEC, BSA/AML Examination Manual: https://bsaaml.ffiec.gov/manual
IBM AMLSim synthetic AML transaction simulator: https://github.com/IBM/AMLSim
Shahbazi et al. (2026), Explainable and Trustworthy AI for Anti-Money Laundering: https://arxiv.org/abs/2609.37155
Poon et al. (2026), LineMVGNN: https://arxiv.org/abs/2603.23584
Deprez et al. (2025), Advances in Continual Graph Learning for AML Systems: https://arxiv.org/abs/2503.24259
Bonato and Szava (2025), Network Embedding Analysis for AML Detection: https://arxiv.org/abs/2509.10715
GM-DualTransAML++ (2026), Annals of Data Science: https://link.springer.com/article/10.1007/s40745-026-00730-3
Supabase, Database overview: https://supabase.com/docs/guides/database/overview
Supabase, pgvector and embeddings: https://supabase.com/docs/guides/database/extensions/pgvector
Supabase, Row Level Security: https://supabase.com/docs/guides/database/postgres/row-level-security
Supabase, RAG with permissions: https://supabase.com/docs/guides/ai/rag-with-permissions
