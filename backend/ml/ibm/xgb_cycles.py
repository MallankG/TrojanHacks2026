"""RETIRED. Tuned XGBoost on HI-Small: xgb_temporal + 2/3-cycle counts on each split's visible graph.

The `cycles` group built each split's graph from its whole window, so an edge saw its own split's later
edges (a laundering return leg leaked into the forward leg). Its 42.48 test AUPRC is not leakage-safe.
Strictly-past 2-cycles are already `pair_reciprocal` in `temporal`, so use ml.ibm.xgb_temporal.
"""

if __name__ == "__main__":
    raise SystemExit(__doc__)
