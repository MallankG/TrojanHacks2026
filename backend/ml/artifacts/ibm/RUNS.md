# Colab run ledger — IBM AML HI-Small

Every model run executed on the Colab GPU runtime, in order. Test metrics are on the paper's split
(train ≤ 2022-09-06 18:24 < val ≤ 2022-09-08 03:28 < test). Thresholds are chosen on validation only.
Paper = Shahbazi et al. 2026 (arXiv:2609.37155), Table 12: XGBoost AUPRC 38.46 / F1 40.30; GATv2-IMPROVED AUPRC 64.85 / F1 68.42;
Mondrian conformal coverage ≈ 0.90, set size ≈ 1.

| # | job | code md5 | val AUPRC | test AUPRC | AUROC | best F1 | F1@val-thr | alerts/1k | vs paper | notes |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | xgb (paper config, 5 seeds) | b4396afa | 32.27 | 38.05 ± 0.91 | 98.21 | 39.71 | — | — | XGB −0.41 (within 1 SD) | node aggregates use all periods, as in paper |
| 0 | xgb (tuned, 5 seeds) | b4396afa | 42.98 | 47.63 ± 0.38 | 98.32 | 47.83 | — | — | XGB +9.17 | served by ml/ibm/scorer.py |
| 1 | gnn v1 (GATv2-IMPROVED, seed 7, fanout 20/8, 20 ep, early-stopped) | 2dc2e6dd | 47.45 | 61.60 | 98.74 | 61.75 | 58.73 | 0.72 | GNN −3.25 | T4, 1516 s; beats tuned XGB by +14 AUPRC |
| 3 | rules (6 PRD detectors, hand-set thresholds) | 844b976f | 0.19 | 0.30 | 70.91 | 0.87 | 0.87 | 170.73 | no paper row; ≈ base rate (0.17%) | thresholds don't fit HI-Small; sent back for rework |
| 4 | isolation_forest (temporal features, unsupervised) | 3293db9d | 0.12 | 0.15 | 45.87 | 0.37 | 0.31 | 23.88 | no paper row; worse than random (AUROC < 50) | sent back: score direction / feature choice |
| 5 | xgb_safe (paper 39 feats, node aggregates from TRAIN period only) | c712cda1 | 17.28 | 12.92 | 97.51 | 16.40 | 15.52 | 0.79 | XGB −25.5 | snapshot baseline, not clean: frozen train-period aggregates (train rows still see later train edges; unseen accounts get zeros). Shows paper-style aggregates are leak-inflated (47.63 → 12.92) |
| 6 | xgb_temporal (safe39 + time-respecting rolling account feats) | e1c5def9 | 43.42 | 42.02 | 98.76 | 42.50 | 37.37 | 0.28 | XGB +3.56 (leak-free) | honest XGBoost number |
| 7 | xgb_embed (safe39 + temporal + SVD/cycle graph feats) | a675b71f | — | — | — | — | — | — | — | FAILED rc=137: OOM-killed at 9.8 GB RSS during predict (12 GB runtime); sent back for memory fix |
| 8 | rules (hand-set, 8 detectors incl. inflow burst, cycle_3) | 21e169db | 0.22 | 0.29 | 66.81 | 0.78 | 0.53 | 4.71 | no paper row | ablation |
| 8 | rules_calibrated (train-quantile thresholds, precision-weighted) | 21e169db | 0.36 | 0.60 | 73.02 | 2.54 | 2.31 | 36.20 | no paper row | 2× better but weak; bug: zero-inflated rules (reciprocal, cycle_3) calibrate to thr 0 and fire on every edge — sent back |
| 9 | isolation_forest_behavior (scale-free behaviour deltas, unsupervised) | 7fcf5d12 | 1.08 | 0.85 | 85.89 | 1.91 | 1.11 | 58.52 | no paper row | real signal now (5× base rate, AUROC 86 vs 46 raw); ranking signal only |
| 10 | xgb_embed v2 (OOM fix: single float32 array, chunked CPU predict) | 8dbaa05a | 42.05 | 37.52 | 98.75 | 39.11 | 32.50 | 0.29 | XGB −0.94 | no OOM; graph SVD/cycle feats HURT vs xgb_temporal (−4.5 test, −1.4 val) |
| 11 | rules_calibrated v3 (thr-0 fix: quantiles above signal min) | 5b58f0d5 | 1.55 | 1.47 | 75.02 | 7.13 | 7.06 | 1.53 | no paper row | 2.5× v2; cycle_3 (train prec 18.9%) and reciprocal (7.7%) now useful; rules-vs-ML gap stands |
