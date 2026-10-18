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
| 12 | gnn v2 (fanout 50/20, ego IDs, lr 3e-3, bs 1024, neg 0.1, pw 3; best ep 16/22) | b1dd3490 | 54.95 | 56.84 | 98.68 | 61.12 | 50.56 | 1.23 | GNN −8.01 | val +7.5 over v1 but test −4.8: single-seed val/test inversion; config ranking not yet reliable |
| 13 | cycles_cache (2/3-cycle counts per split-visible graph) | 1fc22144 | — | — | — | — | — | — | — | built in 57 s, no OOM |
| 14 | xgb_cycles (safe39 + temporal + visible-graph cycles) | b54d8f1c | 43.04 | 42.48 | 98.74 | 43.21 | 39.45 | 0.30 | XGB +4.02 (leak-free) | ≈ xgb_temporal (+0.46 test, −0.38 val); cycles add little over temporal |
| 15 | ensemble gnn_v2 + xgb_temporal (rank blend, w=0.9 on gnn_v2 chosen on val) | 9c1d1e2b | 57.12 | 60.07 | 98.75 | 65.02 | 51.82 | 0.04 | GNN −4.78; best-F1 −3.40 | best val of all; 0.04 false alerts/1k at val threshold |
| 16 | conformal (Mondrian, α=0.1, cal on val) | 719caa91 | — | — | — | — | — | — | coverage ≈ paper | gnn 0.894 / gnn_v2 0.901 / xgb_temporal 0.880 / ensemble 0.903; avg set size 0.95–0.97 (paper ≈0.90, ≈1) |
| 17 | explain (GNNExplainer on gnn_v2, n=200, Jaccard vs HI-Small_Patterns) | cfbb1a1c | — | — | — | — | — | — | GNNExplainer 11.6% vs paper 21.4% | attention 9.1, random 3.6 (paper 1.0/1.0); only 66% of pattern edges reachable in the 2-hop sample, which caps Jaccard |
| 18 | typology_recall (recall @ val thr, per pattern type) | 5f9b77d8 | — | — | — | — | — | — | — | gnn v1: fan-in 94.9, scatter-gather 94.3, gather-scatter 93.6, fan-out 89.9, cycle 67.2, stack 45.0, bipartite 31.8; UNLISTED illicit edges ≈3% for all models |

Queued at session pause (2026-10-18 00:15Z): gnn_s1, gnn_s2, gnn_v2_s1, gnn_v2_s2 (seed runs, ~25 min each), gnn.py md5 52362b8b.

## Rebuild on fixed code (2026-10-18, fresh T4 runtime; commits b3a4d1e + 3dc030e + 4112bca)
Runtime recycled during the pause, so earlier artifacts (except gnn v1, tuned xgb, first rules/IF) were lost; numbers above stand under the old code.
Fixes: strictly-earlier-timestamp counterparty/pair counts (same-second rows excluded), dormant rule ignores first-ever events, cycle_3 / cycles group retired (lookahead), temporal GNN mode with minute-exact cap.

| # | job | code md5 | val AUPRC | test AUPRC | AUROC | best F1 | F1@val-thr | alerts/1k | vs paper | notes |
|---|---|---|---|---|---|---|---|---|---|---|
| R2 | xgb_temporal (fixed features) | af80e0eb/e1c5def9 | 44.87 | 44.92 | 98.75 | 45.37 | 43.82 | 0.53 | XGB +6.46 (leak-free) | +2.9 over pre-fix 42.02; the served XGB |
| R3 | rules_calibrated (cycle_3 retired, dormant fixed) | 0df14d78 | 0.88 | 0.93 | 72.85 | 7.27 | 7.09 | 1.50 | no paper row | dormant_then_large now real (train prec 2.39%); hand-set rules 0.25 |
| R4 | isolation_forest_behavior (fixed inputs) | 7fcf5d12 | 0.98 | 0.68 | 85.53 | 1.88 | 0.81 | 45.01 | no paper row | raw IF still inverted (AUROC 45.86) |
| R5 | **gnn_temporal** (PRD-strict: message passing only over edges before the batch's earliest target minute; targets isolated; seed 7; best ep 15/21) | 6f3968d8 | 49.67 | **61.33** | 98.92 | 61.81 | 61.53 | 0.46 | GNN −3.52 | ≈ within-window v1 (61.60) with no lookahead; better operating point (F1@thr 61.5 vs 58.7, 575 FP) |
| R6 | **ensemble gnn_temporal + xgb_temporal** (w=0.8 on gnn, chosen on val) | 9c1d1e2b | 53.20 | **62.41** | 98.99 | 61.60 | 54.18 | 0.11 | GNN −2.44 | best test AUPRC of any model, strict protocol |
| R7 | conformal (α=0.1) | 719caa91 | — | — | — | — | — | — | coverage ≈ paper | gnn_temporal 0.897 / xgb_temporal 0.881 / ensemble 0.908; set size 0.95–0.97 |
| R8 | typology_recall @ val thr | 5f9b77d8 | — | — | — | — | — | — | — | gnn_temporal: gather-scatter 87.8, fan-out 85.5, scatter-gather 79.1, cycle 70.2, fan-in 69.2, random 64.5, stack 62.4, bipartite 50.0, unlisted 3.8 |
