"""Serve the Colab-trained IBM HI-Small XGBoost model (see ml/ibm/xgb.py, artifacts in ml/artifacts/ibm).

    from ml.ibm.scorer import IBMScorer
    scorer = IBMScorer()
    scorer.score(df)  # df columns: ts, src, dst, amt_paid, cur_paid, cur_recv, fmt

Node features are the per-account aggregates frozen at training time; an account
not seen in HI-Small gets all-zero node features (no history).

    python -m ml.ibm.scorer --data <dir with HI-Small_Trans.csv>   # self-check
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

from .xgb import edge_features

ART = Path(__file__).parent.parent / "artifacts" / "ibm"


class IBMScorer:
    def __init__(self, artifact_dir=ART, config="tuned"):
        d = Path(artifact_dir)
        res = json.loads((d / f"ibm_xgb_{config}_results.json").read_text())
        self.features, self.threshold = res["features"], res["operating_threshold"]
        self.model_version = f"ibm-hi-small-xgb-{config}-seed{res['serving_seed']}"
        self.booster = xgb.Booster()
        self.booster.load_model(d / f"ibm_xgb_{config}.json")
        self.booster.set_param({"device": "cpu"})
        self.node = pd.read_parquet(d / "ibm_node_features.parquet").set_index("account")

    def matrix(self, df):
        nv = self.node.to_numpy(np.float32)
        nv = np.vstack([nv, np.zeros((1, nv.shape[1]), np.float32)])  # last row: unknown account
        idx = lambda s: np.where((i := self.node.index.get_indexer(s)) < 0, len(self.node), i)
        X = np.hstack([nv[idx(df["src"])], nv[idx(df["dst"])], edge_features(df).to_numpy(np.float32)])
        return xgb.DMatrix(X, feature_names=self.features)

    def score(self, df: pd.DataFrame, top_k=5) -> pd.DataFrame:
        df = df.assign(ts=pd.to_datetime(df["ts"])).reset_index(drop=True)
        dm = self.matrix(df)
        p = self.booster.predict(dm)
        contrib = self.booster.predict(dm, pred_contribs=True)[:, :-1]
        top = np.argsort(-contrib, axis=1)[:, :top_k]
        return pd.DataFrame({
            "risk_score": p,
            "flagged": p >= self.threshold,
            "top_features": [[{"feature": self.features[j], "contribution": float(contrib[i, j])} for j in top[i] if contrib[i, j] > 0]
                             for i in range(len(df))],
            "model_version": self.model_version,
        })


if __name__ == "__main__":  # self-check: serving path reproduces the Colab test AUPRC of the served model
    import argparse

    from sklearn.metrics import average_precision_score

    from .xgb import load, split

    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    tx, _ = load(ap.parse_args().data)
    e = tx[tx["src"] != tx["dst"]].reset_index(drop=True)
    _, _, te, _ = split(e["ts"])
    sc = IBMScorer()
    p = sc.booster.predict(sc.matrix(e.iloc[te]))  # raw scores; score() also explains, too slow for 1.2M rows
    ap_ = 100 * average_precision_score(e["y"].to_numpy()[te], p)
    want = json.loads((ART / "ibm_xgb_tuned_results.json").read_text())["runs"][-1]["test"]["auprc"]
    assert abs(ap_ - want) < 0.05, (ap_, want)
    print(f"serving test AUPRC {ap_:.2f} == Colab {want:.2f}; flagged {(p >= sc.threshold).sum():,} of {len(p):,}")
    print(sc.score(e.iloc[te[np.argsort(-p)[:3]]]).to_string())
