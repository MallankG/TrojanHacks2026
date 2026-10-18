"""PRD anomaly component on HI-Small: Isolation Forest fitted on the train period (labels unused).

  isolation_forest           raw temporal features (ablation: volume-dominated, so legit hubs are the outliers)
  isolation_forest_behavior  scale-free behaviour deltas: amount vs own history, burstiness, counterparty
                             novelty, pass-through and flow ratios, recency. All log-compressed.

    python -m ml.ibm.anomaly --cache ml/artifacts/ibm/cache --out ml/artifacts/ibm
"""
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from .common import cli, evaluate_and_save, load_cache


def behavior(f, amt):
    b = pd.DataFrame({
        "amt_vs_own_mean_7d": f.amt_vs_o_mean_7d,
        "pass_through_24h": f.pass_through_24h,
        "src_flow_24h": f.o_flow_24h,
        "dst_flow_24h": f.d_flow_24h,
        "src_burst_1h_vs_7d": f.o_out_cnt_1h / (f.o_out_cnt_7d + 1),
        "dst_burst_24h_vs_7d": f.d_in_cnt_24h / (f.d_in_cnt_7d + 1),
        "src_cp_diversity": f.o_out_cp / (f.o_n_events_to_date + 1),
        "dst_cp_diversity": f.d_in_cp / (f.d_n_events_to_date + 1),
        "new_pair": (f.pair_prior == 0).astype(float),
        "src_idle_days": f.o_secs_since_last / 86400,
        "dst_idle_days": f.d_secs_since_last / 86400,
        "log_amount": np.log1p(amt),
    })
    return np.log1p(b.clip(lower=0)).to_numpy(np.float32)


def fit_score(name, X, tr, y, va, te, a, order):
    iso = IsolationForest(n_estimators=200, max_samples=8192, random_state=a.seed, n_jobs=-1).fit(X[tr])
    joblib.dump(iso, Path(a.out) / f"ibm_{name}.joblib")
    return evaluate_and_save(name, y, -iso.score_samples(X), va, te, a.out, order=order)


if __name__ == "__main__":
    a = cli(__doc__)
    f, y, (tr, va, te), amt, order = load_cache(a.cache, ["temporal"])
    Path(a.out).mkdir(parents=True, exist_ok=True)
    fit_score("isolation_forest", f.to_numpy(np.float32), tr, y, va, te, a, order)
    fit_score("isolation_forest_behavior", behavior(f, amt), tr, y, va, te, a, order)
