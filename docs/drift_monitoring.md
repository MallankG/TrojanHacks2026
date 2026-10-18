# Where to monitor drift and investigate changes

Start at **Governance and audit → Drift and health** (`/governance/drift`). The page compares
pinned windows, explains the checks, and offers three clearly labeled synthetic scenarios.
Use its advanced snapshot form to compute and save an actual comparison through
`POST /enterprise/drift/check`. Never treat the synthetic examples as live monitoring.

| Check | Signals to watch | Where to inspect | What to do next |
| --- | --- | --- | --- |
| Source quality | Missing/renamed fields, type/enum/timezone/unit changes, duplicates, late data, ledger differences | Data operations → Quality; Data contracts; Governance → Data governance | Inspect source rows and the contract. Quarantine failures; approve semantic changes before reprocessing. |
| Input data drift | Numeric PSI, categorical total variation, missing-rate changes, unseen categories, sample count | Drift and health → Input data drift and quality | Compare the same feature version with a frozen baseline. Slice by rail, currency, geography, customer cohort and time of week/month. |
| Prediction drift | Score PSI, alert rate at a fixed threshold, detector disagreement | Drift and health → Prediction drift; Risk intelligence → Detector comparison | Inspect representative alerts and benign examples. Check upstream changes before changing the model. |
| Model performance | Average precision, precision@K, recall@K, outcome coverage and positive rate | Drift and health → Measured model performance; backend model-run metrics | Wait for mature outcomes. Compare the same model, feature definitions and review budget. Check selection bias from reviewing only high-score alerts. |
| Calibration | Brier score and reliability curves when scores are probabilities | Monitoring API (`score_kind=calibrated_probability`); offline validation | Ranking scores are not probabilities. This prototype computes Brier only when probabilities are explicitly declared; reliability curves are not implemented. |
| Concept drift | Sustained performance loss within comparable cohorts, changed feature/outcome relationship | Reviewed outcomes plus segmented offline validation | Input/score drift alone cannot prove concept drift. Investigate label changes, seasonality, prevalence, delayed outcomes and policy changes. |
| Operational drift | Ingestion delay, scoring latency, graph timeouts, verification/retrieval failures, export success | Enterprise workspace → Operations (`/governance/enterprise`) | Use the displayed fallback. Restore the dependency/feed before interpreting stale or partial investigation results. |
| Provenance/release drift | Changed prompts, rules, schemas, models, policies or dataset manifests | Enterprise workspace → Release and residency; `backend/scripts/release.py` | Verify hashes and the Ed25519 signature with a separately trusted key. Missing hosted versions remain unavailable. |

## Prototype monitoring policy

Numeric PSI uses reference-window decile/support bins with a 0.5 pseudocount; watch at 0.10 and
review drift at 0.25. Categorical total variation uses 0.10/0.20. A missing-rate increase of at
least five percentage points takes precedence as a quality issue. These are example review
settings, not universal statistical guarantees or legal thresholds. Use a suitable minimum
sample size (default 30), seasonally comparable windows, multiple-window confirmation and
cohort analysis before choosing production alert thresholds.

Performance uses scikit-learn average precision (AP, not a trapezoidal PR-curve integral),
precision@K and recall@K on labeled observations. Both classes and the minimum sample count
must be present. Below those requirements, model performance is **unknown**, not healthy.
The report shows coverage and prevalence so reviewers can spot incomplete or shifted labels.
The endpoint does not infer concept drift or automatically retrain, promote or replace models.

The API accepts a pinned reference/current snapshot ID, one model version and one feature
version; the caller is responsible for ensuring both windows actually share those versions.
Reports save aggregate results and snapshot references, not individual feature values or labels.
Current monitoring caps: 1,000 rows/window and 30 feature definitions. Scale aggregation outside
the request path before monitoring larger feeds. Segment windows before submitting them;
this prototype does not automatically build cohort comparisons or confidence intervals.

Operation samples come from graph, policy search, verifier and evidence-export requests when
enterprise telemetry is enabled. Ingestion delay and scoring latency require explicit worker
samples at `POST /enterprise/metrics`; request duration is not relabeled as ingestion lag or
model inference time. Fewer than ten observations is **unknown**; last observation older than
one hour is **stale**. The dashboard inspects a bounded rolling 24-hour window, not active probes.

## References

[Evidently: data and prediction drift](https://learn.evidentlyai.com/ml-observability-course/module-2-ml-monitoring-metrics/data-prediction-drift-in-ml)
explains drift as a proxy while outcomes are unavailable, including the need to consider segments.
[scikit-learn: average precision](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.average_precision_score.html)
and [Brier score](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.brier_score_loss.html)
define the implemented model-quality metrics.
