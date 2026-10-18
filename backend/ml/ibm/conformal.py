"""Mondrian (class-conditional) conformal prediction on any model's saved scores (Shahbazi et al. 2026, Sec. on UQ).

Calibrates on validation scores, reports test coverage and set size. Paper: coverage ~0.90 at
nominal 0.90 with average set size ~1.

    python -m ml.ibm.conformal --model gnn --out ml/artifacts/ibm      # reads ibm_gnn_scores.npz
"""
import argparse
import json
from pathlib import Path

import numpy as np

from .order import ORDER_VERSION, npz_order


def mondrian(y_cal, s_cal, y_test, s_test, alpha=0.1):
    p_cal, p_test = np.c_[1 - s_cal, s_cal], np.c_[1 - s_test, s_test]
    q = []
    for c in (0, 1):
        nc = np.sort(1 - p_cal[y_cal == c, c])
        q.append(nc[min(int(np.ceil((len(nc) + 1) * (1 - alpha))) - 1, len(nc) - 1)])
    sets = (1 - p_test) <= np.array(q)
    cover = sets[np.arange(len(y_test)), y_test]
    size = sets.sum(1)
    return {"alpha": alpha, "coverage": float(cover.mean()), "coverage_illicit": float(cover[y_test == 1].mean()),
            "coverage_licit": float(cover[y_test == 0].mean()), "avg_set_size": float(size.mean()),
            "singleton_rate": float((size == 1).mean()), "empty_rate": float((size == 0).mean()),
            "illicit_flagged_singleton": int(((size == 1) & sets[:, 1]).sum()), "q": [float(x) for x in q]}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", default="ml/artifacts/ibm")
    ap.add_argument("--alpha", type=float, default=0.1)
    a = ap.parse_args()
    z = np.load(Path(a.out) / f"ibm_{a.model}_scores.npz")
    if npz_order(z) not in (None, ORDER_VERSION):  # positional rows are irrelevant here, but refuse unknown files
        raise ValueError(f"unknown edge order {npz_order(z)!r} in ibm_{a.model}_scores.npz")
    r = mondrian(z["y_val"], z["s_val"], z["y_test"], z["s_test"], a.alpha)
    (Path(a.out) / f"ibm_{a.model}_conformal.json").write_text(json.dumps(r, indent=2))
    print(f"conformal[{a.model}] " + "  ".join(f"{k} {v:.4f}" if isinstance(v, float) else f"{k} {v}" for k, v in r.items()))
