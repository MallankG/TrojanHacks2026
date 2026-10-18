"""Score-level ensemble of saved HI-Small models (PRD "Prioritize": a ranker evaluated, not invented weights).

Blends per-model scores after converting each to its percentile rank within the split; the blend weight is
picked on validation AUPRC over a grid and reported, test is scored once.

    python -m ml.ibm.ensemble --models gnn_v2 xgb_temporal --out ml/artifacts/ibm
"""
import argparse
from pathlib import Path

import numpy as np
from scipy.stats import rankdata
from sklearn.metrics import average_precision_score

from .common import evaluate_and_save


def pct(s):
    return rankdata(s) / len(s)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs=2, required=True)
    ap.add_argument("--out", default="ml/artifacts/ibm")
    ap.add_argument('--fixed-reference', action='store_true', help='Serve fixed validation-reference percentiles; never rank within an inference batch')
    a = ap.parse_args()
    z = [np.load(Path(a.out) / f"ibm_{m}_scores.npz") for m in a.models]
    assert all((x["y_val"] == z[0]["y_val"]).all() and (x["y_test"] == z[0]["y_test"]).all() for x in z), "row order mismatch"
    val = [pct(x["s_val"]) for x in z]
    test = [pct(x["s_test"]) for x in z]
    if a.fixed_reference:
        reference = [np.sort(x['s_val']) for x in z]
        def transform(values, ref):
            return np.minimum(1.,(np.searchsorted(ref, values, side='left') + np.searchsorted(ref, values, side='right') + 1) / (2*len(ref)))
        val = [transform(x['s_val'], ref) for x,ref in zip(z,reference)]
        test = [transform(x['s_test'], ref) for x,ref in zip(z,reference)]
    grid = np.round(np.arange(0, 1.01, 0.1), 2)
    ap_val = {float(w): float(average_precision_score(z[0]["y_val"], w * val[0] + (1 - w) * val[1])) for w in grid}
    w = max(ap_val, key=ap_val.get)
    y = np.r_[z[0]["y_val"], z[0]["y_test"]]
    s = np.r_[w * val[0] + (1 - w) * val[1], w * test[0] + (1 - w) * test[1]]
    va, te = np.arange(len(val[0])), np.arange(len(val[0]), len(y))
    name = f"ensemble_{a.models[0]}_{a.models[1]}"
    if a.fixed_reference:
        name = 'reference_' + name
        np.savez_compressed(Path(a.out) / f'ibm_{name}_ref.npz', first=reference[0], second=reference[1])
    evaluate_and_save(name, y, s, va, te, a.out, {"reference_mode": "fixed_validation" if a.fixed_reference else "batch_relative", "models": a.models, f"weight_{a.models[0]}": w, "val_auprc_by_weight": ap_val})
    print(f"weight on {a.models[0]}: {w}  (val AUPRC by weight: {ap_val})")
