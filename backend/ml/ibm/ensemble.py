"""Score-level ensemble of two saved HI-Small models (PRD "Prioritize": an evaluated ranker, not invented weights).

Each model's raw score is mapped to its percentile within that model's stored VALIDATION scores
(searchsorted on the sorted val distribution), then blended as w*pct_a + (1-w)*pct_b. The reference
distributions are frozen at training time, so a single new transaction can be scored (servable, not
transductive). The weight is chosen on validation AUPRC; test is scored once.

Caveat: the committed ref distributions, weight and threshold were built from gnn_temporal scores evaluated
with per-BATCH temporal caps (Colab). Live IBMGNNScorer now uses per-TARGET caps with deterministic sampling,
so EnsembleScorer reproduces the saved test scores only when fed the saved GNN scores. Rebuild
ref/weight/threshold after the gnn_temporal per-target re-eval (RUNS.md follow-up queue).

    python -m ml.ibm.ensemble --models gnn_temporal xgb_temporal --out ml/artifacts/ibm
    # serving:  EnsembleScorer("gnn_temporal", "xgb_temporal").blend(gnn_scores, xgb_scores)
"""
import argparse
import json
from pathlib import Path

import numpy as np
from sklearn.metrics import average_precision_score

from .common import evaluate_and_save

ART = Path(__file__).parent.parent / "artifacts" / "ibm"


def pct(ref_sorted, s):
    """Fraction of reference (val) scores <= s; inputs cast to the ref dtype (no off-by-one on float64 ties)."""
    return np.searchsorted(ref_sorted, np.asarray(s, dtype=ref_sorted.dtype), side="right") / len(ref_sorted)


TIE_EPS = 1e-9  # far below the smallest blended-percentile step, min(w, 1-w)/len(ref), for scores in [0, 1]


def blend_scores(ref_a, ref_b, w, s_a, s_b):
    """Blend pure percentiles, then add ONE raw-score tie-break, so it only orders exact blend ties (e.g. rows
    beyond both models' val extremes) and can never override a real percentile difference."""
    s_a, s_b = np.asarray(s_a, dtype=np.float64), np.asarray(s_b, dtype=np.float64)
    return w * pct(ref_a, s_a) + (1 - w) * pct(ref_b, s_b) + TIE_EPS * (w * s_a + (1 - w) * s_b)


class EnsembleScorer:
    def __init__(self, a, b, artifact_dir=ART):
        d = Path(artifact_dir)
        res = json.loads((d / f"ibm_ensemble_{a}_{b}_results.json").read_text())
        ref = np.load(d / f"ibm_ensemble_{a}_{b}_ref.npz")
        self.ref_a, self.ref_b, self.w = ref["ref_a"], ref["ref_b"], res[f"weight_{a}"]
        self.threshold = res["val"]["best_threshold"]
        self.calibration = res.get("calibration", "batch-cap GNN scores; rebuild after gnn_temporal per-target re-eval")

    def blend(self, s_a, s_b):
        return blend_scores(self.ref_a, self.ref_b, self.w, s_a, s_b)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs=2, required=True)
    ap.add_argument("--out", default="ml/artifacts/ibm")
    a = ap.parse_args()
    z = [np.load(Path(a.out) / f"ibm_{m}_scores.npz") for m in a.models]
    assert all((x["y_val"] == z[0]["y_val"]).all() and (x["y_test"] == z[0]["y_test"]).all() for x in z), "row order mismatch"
    ref = [np.sort(x["s_val"]) for x in z]
    blend = lambda w, part: blend_scores(ref[0], ref[1], w, z[0][part], z[1][part])
    grid = np.round(np.arange(0, 1.01, 0.1), 2)
    ap_val = {float(w): float(average_precision_score(z[0]["y_val"], blend(w, "s_val"))) for w in grid}
    w = max(ap_val, key=ap_val.get)
    y = np.r_[z[0]["y_val"], z[0]["y_test"]]
    n_val = len(z[0]["y_val"])
    s = np.r_[blend(w, "s_val"), blend(w, "s_test")]
    va, te = np.arange(n_val), np.arange(n_val, len(y))
    name = f"ensemble_{a.models[0]}_{a.models[1]}"
    np.savez_compressed(Path(a.out) / f"ibm_{name}_ref.npz", ref_a=ref[0], ref_b=ref[1])
    evaluate_and_save(name, y, s, va, te, a.out, {"models": a.models, f"weight_{a.models[0]}": w, "val_auprc_by_weight": ap_val,
                                                  "ranking": "percentile vs stored val score distributions (servable)"})
    print(f"weight on {a.models[0]}: {w}  (val AUPRC by weight: {ap_val})")
