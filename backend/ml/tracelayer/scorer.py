"""Load trained artifacts and score transactions — the entry point for the API.

    from ml.tracelayer.scorer import TraceLayerScorer
    scorer = TraceLayerScorer("ml/artifacts/tracelayer")
    alerts = scorer.score(transactions_df, accounts_df)            # all rows
    alerts = scorer.score(transactions_df, accounts_df, rows=idx)  # only some rows

`transactions_df` is the canonical schema (transaction_id, timestamp, amount,
currency, channel, origin_account_id, destination_account_id) and must include
the history before the rows being scored: features and the GNN context only
look backwards in time, so scoring without history under-reports risk.
"""
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import torch
import xgboost as xgb

from . import gnn as G
from .features import apply_rules, build_features


class TraceLayerScorer:
    def __init__(self, artifact_dir="ml/artifacts/tracelayer"):
        d = Path(artifact_dir)
        self.card = json.loads((d / "model_card.json").read_text())
        self.cols = self.card["feature_columns"]
        self.booster = xgb.Booster()
        self.booster.load_model(d / "xgboost.json")
        self.iso = joblib.load(d / "isolation_forest.joblib")
        ck = torch.load(d / "gnn.pt", weights_only=False)
        self.gnn = G.EdgeGAT(**ck["config"])
        self.gnn.load_state_dict(ck["state_dict"])
        self.gnn.eval()
        self.tab_stats = ck["tab_stats"]
        self.thr = self.card["thresholds"]

    def score(self, tx: pd.DataFrame, accounts: pd.DataFrame, rows=None, top_k_features=5) -> pd.DataFrame:
        tx = tx.sort_values("timestamp", kind="stable").reset_index(drop=True)
        rows = np.arange(len(tx)) if rows is None else np.asarray(rows)
        f = build_features(tx, accounts)
        X = f[self.cols].to_numpy(np.float32)[rows]
        dm = xgb.DMatrix(X, feature_names=self.cols)
        p_xgb = self.booster.predict(dm)
        contrib = self.booster.predict(dm, pred_contribs=True)[:, :-1]  # drop bias column
        p_gnn = G.score(self.gnn, G.TxGraph(tx, accounts), G.tab_transform(f, self.tab_stats), rows)
        rules = apply_rules(f.iloc[rows]).reset_index(drop=True)
        s = {"xgboost": p_xgb, "gnn": p_gnn, "ensemble_xgb_gnn": (p_xgb + p_gnn) / 2}
        risk = s[self.card["selected_model"]]
        t = self.thr[self.card["selected_model"]]
        top = np.argsort(-contrib, axis=1)[:, :top_k_features]
        out = pd.DataFrame({
            "transaction_id": tx["transaction_id"].to_numpy()[rows],
            "risk_score": risk,
            "priority_band": np.where(risk >= t["operating"], "high", np.where(risk >= t["review_recall90"], "review", "low")),
            "xgboost_score": p_xgb,
            "gnn_score": p_gnn,
            "anomaly_score": -self.iso.score_samples(f[self.cols].to_numpy(np.float32)[rows]),
            "rules_fired": rules.apply(lambda r: [k for k, v in r.items() if v], axis=1),
            "top_features": [[{"feature": self.cols[j], "contribution": float(contrib[i, j])} for j in top[i] if contrib[i, j] > 0]
                             for i in range(len(rows))],
            "model_version": self.card["model_version"],
            "feature_version": self.card["feature_version"],
        })
        return out


if __name__ == "__main__":  # self-check: serving path reproduces the training-time test metrics
    from sklearn.metrics import average_precision_score

    sc = TraceLayerScorer()
    tx = pd.read_parquet("ml/data/transactions.parquet").sort_values("timestamp", kind="stable").reset_index(drop=True)
    acc = pd.read_parquet("ml/data/accounts.parquet")
    day = ((tx.timestamp - tx.timestamp.min().normalize()) / pd.Timedelta(days=1)).to_numpy()
    rows = np.flatnonzero(day >= 75)
    res = sc.score(tx, acc, rows)
    ap = average_precision_score(tx.is_suspicious.to_numpy()[rows], res.risk_score)
    want = sc.card["metrics"][sc.card["selected_model"]]["test"]["pr_auc"]
    assert abs(ap - want) < 1e-4, (ap, want)
    print(f"serving PR-AUC {ap:.4f} == training {want:.4f}")
    print(res.sort_values("risk_score", ascending=False).head(5).to_string())
