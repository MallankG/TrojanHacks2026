"""Serve the leakage-safe IBM HI-Small XGBoost (ml/ibm/xgb_temporal.py, artifacts in ml/artifacts/ibm).

    from ml.ibm.scorer import IBMScorer
    scorer = IBMScorer()
    scorer.score(edges)             # all rows
    scorer.score(edges, rows=idx)   # only some rows (positions in `edges`)

`edges` has columns ts, src, dst, amt_paid, amt_recv, cur_paid, cur_recv, fmt and must include the
history before the rows being scored: the temporal features only look strictly backwards in time.
Node features are aggregates frozen at the train cutoff; an account first seen later gets zeros.

The paper-style `ibm_xgb_tuned` model is not served: its node aggregates span the whole dataset,
test period included (47.63 test AUPRC, 12.92 once frozen at the train cutoff; see RESULTS.md).

Needs ibm_xgb_temporal.json, ibm_xgb_temporal_results.json and ibm_node_features_train.parquet
(written to the feature cache by `python -m ml.ibm.common`).

    python -m ml.ibm.scorer --data <dir with HI-Small_Trans.csv>   # self-check
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

from .common import temporal_features
from .xgb import edge_features

ART = Path(__file__).parent.parent / "artifacts" / "ibm"
MODEL = "xgb_temporal"


class IBMScorer:
    def __init__(self, artifact_dir=ART):
        d = Path(artifact_dir)
        res = json.loads((d / f"ibm_{MODEL}_results.json").read_text())
        self.threshold = res["test_at_val_threshold"]["threshold"]  # chosen on val (best F1)
        self.booster = xgb.Booster()
        self.booster.load_model(d / f"ibm_{MODEL}.json")
        self.booster.set_param({"device": "cpu"})
        # early-stopped: serve the trees validation picked, not every tree trained
        self.trees = (0, int(self.booster.attr("best_iteration")) + 1)
        self.node = pd.read_parquet(d / "ibm_node_features_train.parquet").set_index("account")
        self.model_version = f"ibm-hi-small-{MODEL}-iter{self.trees[1]}"

    def matrix(self, e):
        """Features for every row of the time-sorted edge frame `e`."""
        nv = self.node.to_numpy(np.float32)
        nv = np.vstack([nv, np.zeros((1, nv.shape[1]), np.float32)])  # last row: account unseen by the train cutoff
        idx = lambda s: np.where((i := self.node.index.get_indexer(s)) < 0, len(self.node), i)
        ef = edge_features(e)
        tf = temporal_features(e)
        names = [f"src_{c}" for c in self.node] + [f"dst_{c}" for c in self.node] + list(ef) + list(tf)
        X = np.hstack([nv[idx(e["src"])], nv[idx(e["dst"])], ef.to_numpy(np.float32), tf.to_numpy(np.float32)])
        return X, names

    def predict(self, X):
        return self.booster.inplace_predict(X, iteration_range=self.trees)

    def score(self, edges: pd.DataFrame, rows=None, top_k=5) -> pd.DataFrame:
        e = edges.assign(ts=pd.to_datetime(edges["ts"])).reset_index(drop=True)
        rows = np.arange(len(e)) if rows is None else np.asarray(rows)
        order = np.argsort(e["ts"].to_numpy(), kind="stable")
        pos = np.empty_like(order)
        pos[order] = np.arange(len(order))
        X, names = self.matrix(e.iloc[order].reset_index(drop=True))
        X = X[pos[rows]]  # back to the caller's row positions
        p = self.predict(X)
        dm = xgb.DMatrix(X, feature_names=names)
        contrib = self.booster.predict(dm, pred_contribs=True, iteration_range=self.trees)[:, :-1]
        top = np.argsort(-contrib, axis=1)[:, :top_k]
        return pd.DataFrame({
            "risk_score": p,
            "flagged": p >= self.threshold,
            "top_features": [[{"feature": names[j], "contribution": float(contrib[i, j])} for j in top[i] if contrib[i, j] > 0]
                             for i in range(len(rows))],
            "model_version": self.model_version,
        })


if __name__ == "__main__":  # self-check: serving path reproduces the Colab test AUPRC of the served model
    import argparse

    from sklearn.metrics import average_precision_score

    from .order import canonical
    from .xgb import load, split

    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    tx, _ = load(ap.parse_args().data)
    e = canonical(tx[tx["src"] != tx["dst"]])  # the same edge order as load_edges / EnsembleScorer
    _, _, te, _ = split(e["ts"])
    sc = IBMScorer()
    X, _ = sc.matrix(e)
    p = sc.predict(X[te])  # raw scores; score() also explains, too slow for 1.2M rows
    ap_ = 100 * average_precision_score(e["y"].to_numpy()[te], p)
    want = json.loads((ART / f"ibm_{MODEL}_results.json").read_text())["test"]["auprc"]
    assert abs(ap_ - want) < 0.05, (ap_, want)
    print(f"serving test AUPRC {ap_:.2f} == Colab {want:.2f}; flagged {(p >= sc.threshold).sum():,} of {len(p):,}")
    print(sc.score(e, rows=te[np.argsort(-p)[:3]]).to_string())
