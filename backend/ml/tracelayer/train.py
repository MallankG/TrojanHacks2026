"""End-to-end training + evaluation for TraceLayer risk models.

Rules baseline, Isolation Forest anomaly score, XGBoost, time-respecting GATv2,
and an XGBoost+GNN mean ensemble, on a chronological train/val/test split.
Thresholds and model selection use validation only; test is touched once.

    python -m ml.tracelayer.generate_data --out ml/data
    python -m ml.tracelayer.train --data ml/data --out ml/artifacts/tracelayer
"""
import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import torch
import xgboost as xgb
from sklearn.ensemble import IsolationForest
from sklearn.metrics import average_precision_score, confusion_matrix, precision_recall_curve, roc_auc_score

from . import gnn as G
from .features import FEATURE_VERSION, RULES, apply_rules, build_features, feature_columns

SEED = 7
SPLIT_DAYS = {"train": (0, 60), "val": (60, 75), "test": (75, 90)}
MODEL_VERSION = "tracelayer-risk-1.0.0"


def best_f1_threshold(y, s):
    p, r, t = precision_recall_curve(y, s)
    f1 = 2 * p * r / np.clip(p + r, 1e-9, None)
    i = int(np.nanargmax(f1[:-1]))
    return float(t[i])


def recall_threshold(y, s, target=0.9):
    return float(np.quantile(s[y == 1], 1 - target))


def evaluate(y, s, thr, typ):
    order = np.argsort(-s, kind="stable")
    pred = s >= thr
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    out = {
        "pr_auc": float(average_precision_score(y, s)),
        "roc_auc": float(roc_auc_score(y, s)),
        "threshold": thr,
        "precision": float(tp / max(tp + fp, 1)),
        "recall": float(tp / max(tp + fn, 1)),
        "f1": float(2 * tp / max(2 * tp + fp + fn, 1)),
        "false_alerts_per_1000_tx": float(fp / len(y) * 1000),
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    }
    for k in (100, 500, 1000):
        hits = y[order[:k]].sum()
        out[f"precision@{k}"] = float(hits / k)
        out[f"recall@{k}"] = float(hits / max(y.sum(), 1))
    out["recall_by_typology"] = {t: float(pred[(typ == t) & (y == 1)].mean()) for t in sorted(set(typ[y == 1]))}
    return out


def train_gnn(graph, tab, y, train_rows, val_rows, epochs=30, patience=5, log=print):
    torch.manual_seed(SEED)
    model = G.EdgeGAT(tab.shape[1])
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    pos_w = torch.tensor(float(np.sqrt((y[train_rows] == 0).sum() / max(y[train_rows].sum(), 1))))
    lossf = torch.nn.BCEWithLogitsLoss(pos_weight=pos_w)
    buckets = np.unique(graph.bucket[train_rows])
    buckets = buckets[buckets * G.BUCKET_SECS >= graph.t[0] + 2 * 86400]  # need some context
    yt = torch.from_numpy(y.astype(np.float32))
    rng = np.random.default_rng(SEED)
    best, best_state, bad, history = -1, None, 0, []
    for ep in range(epochs):
        model.train()
        t0, tot = time.time(), 0.0
        for b in rng.permutation(buckets):
            r = graph.targets(b)
            x, ei, ea = graph.snapshot(b)
            logit = model(x, ei, ea, torch.from_numpy(graph.src[r]), torch.from_numpy(graph.dst[r]), torch.from_numpy(tab[r]))
            loss = lossf(logit, yt[r])
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            tot += loss.item()
        ap = average_precision_score(y[val_rows], G.score(model, graph, tab, val_rows))
        history.append({"epoch": ep + 1, "train_loss": tot / len(buckets), "val_pr_auc": float(ap)})
        log(f"  gnn epoch {ep + 1:2d} loss {tot / len(buckets):.4f} val PR-AUC {ap:.4f} ({time.time() - t0:.0f}s)")
        if ap > best:
            best, bad = ap, 0
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
            if bad >= patience:
                break
    model.load_state_dict(best_state)
    return model, history


def main(data, out):
    np.random.seed(SEED)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    tx_path = Path(data) / "transactions.parquet"
    tx = pd.read_parquet(tx_path).sort_values("timestamp", kind="stable").reset_index(drop=True)
    acc = pd.read_parquet(Path(data) / "accounts.parquet")
    y = tx["is_suspicious"].to_numpy()
    typ = tx["typology"].to_numpy()
    t0 = tx["timestamp"].min().normalize()
    day = ((tx["timestamp"] - t0) / pd.Timedelta(days=1)).to_numpy()
    rows = {k: np.flatnonzero((day >= a) & (day < b)) for k, (a, b) in SPLIT_DAYS.items()}
    for k, r in rows.items():
        print(f"{k}: {len(r):,} tx, {y[r].sum():,} suspicious ({y[r].mean():.2%})")

    f = build_features(tx, acc)
    cols = feature_columns(f)
    X = f[cols].to_numpy(np.float32)
    rules = apply_rules(f)
    tr, va, te = rows["train"], rows["val"], rows["test"]
    scores, results = {}, {}

    # 1. rules baseline: number of typology detectors fired
    scores["rules"] = rules.sum(axis=1).to_numpy(float)

    # 2. isolation forest (unsupervised, fitted on training-period features)
    iso = IsolationForest(n_estimators=200, max_samples=4096, random_state=SEED, n_jobs=-1).fit(X[tr])
    scores["isolation_forest"] = -iso.score_samples(X)

    # 3. XGBoost
    spw = float(np.sqrt((y[tr] == 0).sum() / y[tr].sum()))
    booster = xgb.train(
        {"objective": "binary:logistic", "eval_metric": "aucpr", "tree_method": "hist", "max_depth": 6, "eta": 0.05,
         "subsample": 0.8, "colsample_bytree": 0.8, "min_child_weight": 5, "scale_pos_weight": spw, "seed": SEED},
        xgb.DMatrix(X[tr], y[tr], feature_names=cols), num_boost_round=2000,
        evals=[(xgb.DMatrix(X[va], y[va], feature_names=cols), "val")], early_stopping_rounds=100, verbose_eval=False)
    booster = booster[: booster.best_iteration + 1]
    scores["xgboost"] = booster.predict(xgb.DMatrix(X, feature_names=cols))
    print(f"xgboost: {booster.num_boosted_rounds()} rounds")

    # 4. GNN
    graph = G.TxGraph(tx, acc)
    stats = G.fit_tab_stats(f.iloc[tr], cols)
    tab = G.tab_transform(f, stats)
    t_g = time.time()
    model, history = train_gnn(graph, tab, y, tr, va)
    print(f"gnn trained in {time.time() - t_g:.0f}s")
    eval_rows = np.r_[va, te]
    scores["gnn"] = np.full(len(tx), np.nan)
    scores["gnn"][eval_rows] = G.score(model, graph, tab, eval_rows)

    # 5. ensemble
    scores["ensemble_xgb_gnn"] = (scores["xgboost"] + scores["gnn"]) / 2

    thresholds = {}
    for name, s in scores.items():
        thr = best_f1_threshold(y[va], s[va])
        thresholds[name] = {"operating": thr, "review_recall90": recall_threshold(y[va], s[va])}
        results[name] = {"val": evaluate(y[va], s[va], thr, typ[va]), "test": evaluate(y[te], s[te], thr, typ[te])}
    selected = max(["xgboost", "gnn", "ensemble_xgb_gnn"], key=lambda k: results[k]["val"]["pr_auc"])
    print(f"selected by val PR-AUC: {selected}")

    # --- artifacts --------------------------------------------------------
    booster.save_model(out / "xgboost.json")
    joblib.dump(iso, out / "isolation_forest.joblib")
    torch.save({"state_dict": model.state_dict(), "config": model.config, "tab_stats": stats}, out / "gnn.pt")
    importance = booster.get_score(importance_type="gain")
    card = {
        "model_version": MODEL_VERSION,
        "feature_version": FEATURE_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": SEED,
        "dataset": {"path": str(tx_path), "sha256": hashlib.sha256(tx_path.read_bytes()).hexdigest(),
                    "n_transactions": len(tx), "n_accounts": len(acc), "synthetic": True},
        "split": {k: {"days": SPLIT_DAYS[k], "start": str(t0 + pd.Timedelta(days=SPLIT_DAYS[k][0])),
                      "end": str(t0 + pd.Timedelta(days=SPLIT_DAYS[k][1])), "n": len(rows[k]), "positives": int(y[rows[k]].sum())}
                  for k in rows},
        "selected_model": selected,
        "thresholds": thresholds,
        "feature_columns": cols,
        "rules": list(RULES),
        "gnn": {"config": model.config, "context_days": G.CONTEXT_SECS / 86400, "bucket_hours": G.BUCKET_SECS / 3600, "history": history},
        "xgboost": {"rounds": booster.num_boosted_rounds(), "scale_pos_weight": spw,
                    "top_features_by_gain": dict(sorted(importance.items(), key=lambda kv: -kv[1])[:15])},
        "metrics": results,
        "limitations": [
            "Synthetic AMLSim-style data generated by ml/tracelayer/generate_data.py; metrics are a demonstration, not real-world validation.",
            "Labels mark injected typology patterns, not criminal findings. Scores are review-priority signals.",
            "GNN context is a 14-day trailing graph refreshed per 6h bucket; activity inside the current bucket is only seen via tabular features.",
            "Not retrained on train+val; thresholds were chosen on validation (max F1) and never tuned on test.",
        ],
    }
    (out / "model_card.json").write_text(json.dumps(card, indent=2, default=float))
    write_report(out / "RESULTS.md", card)
    print((out / "RESULTS.md").read_text())


def write_report(path, card):
    m = card["metrics"]
    names = list(m)
    L = [f"# TraceLayer model results ({card['model_version']})", "",
         f"Synthetic data, chronological split. Selected model (by validation PR-AUC): **{card['selected_model']}**.", "",
         "| split | " + " | ".join(f"{k}: {v['start'][:10]} → {v['end'][:10]}, {v['n']:,} tx, {v['positives']:,} pos" for k, v in card["split"].items()) + " |", "",
         "## Test metrics (thresholds chosen on validation)", "",
         "| model | PR-AUC | ROC-AUC | P@100 | P@500 | R@1000 | precision | recall | F1 | false alerts / 1k tx |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for n in names:
        t = m[n]["test"]
        L.append(f"| {n} | {t['pr_auc']:.3f} | {t['roc_auc']:.3f} | {t['precision@100']:.2f} | {t['precision@500']:.2f} | {t['recall@1000']:.2f} | "
                 f"{t['precision']:.3f} | {t['recall']:.3f} | {t['f1']:.3f} | {t['false_alerts_per_1000_tx']:.2f} |")
    L += ["", "## Validation PR-AUC", "", " | ".join(f"{n}: {m[n]['val']['pr_auc']:.3f}" for n in names), "",
          "## Test recall by typology (at operating threshold)", "",
          "| typology | " + " | ".join(names) + " |", "|---" * (len(names) + 1) + "|"]
    for ty in m[names[0]]["test"]["recall_by_typology"]:
        L.append(f"| {ty} | " + " | ".join(f"{m[n]['test']['recall_by_typology'][ty]:.2f}" for n in names) + " |")
    L += ["", "## Confusion matrices (test)", ""]
    for n in names:
        c = m[n]["test"]["confusion_matrix"]
        L.append(f"- {n}: TP {c['tp']}, FP {c['fp']}, FN {c['fn']}, TN {c['tn']}")
    L += ["", "## Limitations", ""] + [f"- {x}" for x in card["limitations"]]
    path.write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="ml/data")
    ap.add_argument("--out", default="ml/artifacts/tracelayer")
    a = ap.parse_args()
    main(a.data, a.out)
