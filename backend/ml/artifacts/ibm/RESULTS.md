# IBM AML HI-Small results vs papers

Dataset: IBM Transactions for AML, HI-Small (5,078,345 tx; 0.10% illicit). Self-loops removed → 4,487,133 edges.
Temporal split at the paper's cutoffs (2022-09-06 18:24 / 2022-09-08 03:28): train 2,590,591 · val 658,773 · test 1,237,769.
All thresholds and model selection on validation only. Trained on Colab GPU.

## Test metrics

| model | AUPRC | AUROC | Best F1 | source |
|---|---|---|---|---|
| **Paper** XGBoost (Shahbazi et al. 2026, Table 12) | 38.46 ± 1.01 | 98.18 | 40.30 | arXiv:2609.37155 |
| **Paper** GATv2-IMPROVED | 64.85 | — | 68.42 | arXiv:2609.37155 |
| XGBoost, paper settings (5 seeds) | 38.05 ± 0.91 | 98.21 | 39.71 | `ml/ibm/xgb.py` |
| XGBoost, tuned (5 seeds) | 47.63 ± 0.38 | 98.32 | 47.83 | `ml/ibm/xgb.py` |
| GATv2-IMPROVED v1, fanout (20,8) (ours) | **61.60** | 98.74 | 61.75 (58.73 @ val thr) | `ml/ibm/gnn.py` · val 47.45 · paper gap −3.25 AUPRC |
| GATv2-IMPROVED v2, fanout (50,20) + ego IDs | 56.84 | 98.68 | 61.12 (50.56 @ val thr) | `ml/ibm/gnn.py --tag gnn_v2` · val **54.95** (best val of any single model) but −4.8 test vs v1; one seed each, seed runs queued |
| **Ensemble 0.9·GNN v2 + 0.1·XGBoost temporal (rank blend, weight on val)** | 60.07 | 98.75 | **65.02** (51.82 @ val thr) | `ml/ibm/ensemble.py` · val **57.12** (best overall) · 0.04 false alerts / 1k tx · closest to paper F1 68.42 |
| Typology rules, hand-set thresholds | 0.29 | 66.81 | 0.78 | `ml/ibm/rules.py` · hand-set thresholds ignore HI-Small's degree distribution, so near random |
| Typology rules, train-calibrated + precision-weighted | 1.47 | 75.02 | 7.13 (7.06 @ val thr) | `ml/ibm/rules.py` · 1.53 alerts/1k · signal almost entirely from cycle_3 (18.9% train precision) and reciprocal |
| Isolation Forest, raw temporal features | 0.15 | 45.87 | 0.37 | `ml/ibm/anomaly.py` · below random: legit high-volume hubs are the outliers |
| Isolation Forest, scale-free behaviour features | 0.85 | 85.89 | 1.91 (1.11 @ val thr) | `ml/ibm/anomaly.py` · unsupervised; 5× base rate; 58.5 alerts/1k, so a secondary ranking signal, not an alert source |
| XGBoost, paper's 39 features with node aggregates frozen at train end | 12.92 | 97.51 | 16.40 (15.52 @ val thr) | `ml/ibm/xgb_safe.py` · val 17.28 · snapshot baseline: stale for val/test, zeros for accounts first seen after the cutoff |
| **XGBoost, point-in-time features (leakage-safe)** | **42.02** | 98.76 | 42.50 (37.37 @ val thr) | `ml/ibm/xgb_temporal.py` · val 43.42 · 0.28 false alerts / 1k tx · **+3.56 over the paper's XGBoost, with no leak** |
| + train-graph SVD embedding & cycle counts (Bonato & Szava) | 37.52 | 98.75 | 39.11 (32.50 @ val thr) | `ml/ibm/xgb_embed.py` · val 42.05 · hurts: the embedding is a train-cutoff snapshot |
| + cycle counts on each split's visible graph, no embedding | 42.48 | 98.74 | 43.21 (39.45 @ val thr) | `ml/ibm/xgb_cycles.py` · val 43.04 · ≈ xgb_temporal: cycles add little over point-in-time features |

## Uncertainty and explainability

| item | ours | paper (Shahbazi et al.) | script |
|---|---|---|---|
| Mondrian conformal, nominal 90%: GNN v1 / GNN v2 / XGB temporal / ensemble | coverage 0.894 / 0.901 / 0.880 / 0.903; avg set size 0.97 / 0.95 / 0.95 / 0.95 | coverage ≈ 0.90, avg set size ≈ 1 · **reproduced** | `ml/ibm/conformal.py` |
| GNNExplainer Jaccard vs ground-truth patterns (GNN v2, n=200) | GNNExplainer **11.6%** · attention 9.1% · random 3.6% | 21.4% (attention 1.0%, random 1.0%) · **half the paper's figure** | `ml/ibm/explain.py` · only 66% of pattern edges fall inside the sampled 2-hop subgraph, which caps Jaccard |

Explainer by typology (GNNExplainer / attention / random, %): CYCLE 10.1/16.8/3.3 · FAN-IN 14.2/7.9/2.3 · FAN-OUT 16.0/2.9/4.6 · GATHER-SCATTER 12.1/7.2/3.9 · SCATTER-GATHER 14.5/14.7/4.8 · STACK 6.0/5.6/2.7 · BIPARTITE 1.7/2.0/2.6 · RANDOM 7.3/17.8/2.7. GNNExplainer beats random about 3×, but attention is not near-random here, unlike in the paper.

## Recall by typology (test, at each model's val threshold, %)

| typology (n test illicit edges) | GNN v1 | GNN v2 | XGB temporal | ensemble | rules calibrated |
|---|---|---|---|---|---|
| FAN-IN (156) | **94.9** | 82.7 | 44.2 | 56.4 | 4.5 |
| FAN-OUT (159) | 89.9 | **91.8** | 40.9 | 60.4 | 4.4 |
| GATHER-SCATTER (425) | **93.6** | 88.0 | 34.6 | 55.5 | 6.6 |
| SCATTER-GATHER (296) | **94.3** | 85.1 | 29.7 | 54.1 | 5.4 |
| CYCLE (131) | 67.2 | **71.0** | 32.8 | 42.0 | 7.6 |
| STACK (189) | 45.0 | **61.9** | 34.9 | 31.2 | 7.4 |
| BIPARTITE (88) | 31.8 | **48.9** | 38.6 | 23.9 | 12.5 |
| RANDOM (93) | 53.8 | **60.2** | 30.1 | 33.3 | 7.5 |
| UNLISTED (557, 27% of illicit) | 3.6 | 2.2 | 3.6 | 0.4 | **8.3** |

27% of test illicit edges belong to no listed pattern, and every model misses them (≤ 8%), which caps recall near 73%. The GNNs dominate the structural typologies (fan/gather/scatter, about 90%). XGBoost's point-in-time features see each transaction only from its endpoints, so it stays near 30–44%. The ensemble's val-tuned threshold is conservative (0.04 alerts/1k), which lowers its recall at that operating point.

Conformal caveats: set size < 1 means 2.7–5% of test edges get an *empty* set (Mondrian class quantiles don't overlap). The {illicit} singleton fires on 5–8% of test edges at α = 0.1, so these sets give calibrated coverage, not a usable alert list. The ensemble's q looks inverted because its scores are percentile ranks; coverage is still valid.

## How to reproduce (Colab GPU)

```
python -m ml.ibm.gnn --data $D --out $O --epochs 30
python -m ml.ibm.common --data $D --cache $O/cache          # shared feature cache
python -m ml.ibm.common --data $D --cache $O/cache --groups cycles
python -m ml.ibm.{rules,anomaly,xgb_safe,xgb_temporal,xgb_embed,xgb_cycles} --cache $O/cache --out $O --device cuda
python -m ml.ibm.conformal --model gnn --out $O
python -m ml.ibm.explain --data $D --out $O --device cuda
```

## Caveats

- Test AUPRC is consistently far above val AUPRC on this split (GNN 61.6 vs 47.5; XGBoost 47.6 vs 43.0), so the val window is simply harder. Model selection still uses val only.

- **Leakage finding:** the paper's XGBoost (and our reproductions: 38.05 paper config, 47.63 tuned) aggregates node features over *all* edges, test period included. Freezing those same aggregates at the train cutoff (`xgb_safe`) drops test AUPRC to 12.92. So most of the paper-style XGBoost score comes from future degree and amount statistics. The paper's own 38.46 likely carries the same inflation.
- `xgb_safe` is not a clean leak-free baseline either. Its train rows still see later train-period edges, and val/test rows get stale aggregates. The fair comparison is `xgb_temporal`, where every account statistic is computed from strictly earlier transactions. It scores 42.02, above the paper's leaky 38.46.
- The GNN's node inputs are entity type plus degree in the visible split graph (train / train+val / all, per protocol). Within a split window, the graph is static, as in the paper and Multi-GNN.
- Paper numbers come from a preprint and its own runs; our numbers come from our own runs. They match on dataset and split cutoffs, but not on implementation.
