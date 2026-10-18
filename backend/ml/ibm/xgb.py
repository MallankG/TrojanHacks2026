"""XGBoost baseline on IBM AML HI-Small, reproducing Shahbazi et al. (2026), arXiv:2609.37155, Sec. 2.2 + 2.4.

39 features = src node (14) + dst node (14) + edge (11); temporal 70/15/15 percentile split;
300 trees, depth 6, lr 0.1, subsample/colsample 0.8, 50:1 negative subsampling, 5 seeds.
Paper (Table 12) test: AUROC 98.18 ± 0.03, AUPRC 38.46 ± 1.01, Best F1 40.30 ± 0.87.

    python -m ml.ibm.xgb --data <dir with HI-Small_Trans.csv + HI-Small_accounts.csv>
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score

ENTITY_TYPES = ["Individual", "Sole Proprietorship", "Partnership", "Corporation", "Country", "Direct"]
PAPER = {"auroc": 98.18, "auprc": 38.46, "auprc_sd": 1.01, "best_f1": 40.30}


def load(data):
    tx = pd.read_csv(Path(data) / "HI-Small_Trans.csv", dtype=str)
    tx.columns = ["ts", "from_bank", "src", "to_bank", "dst", "amt_recv", "cur_recv", "amt_paid", "cur_paid", "fmt", "y"]
    acc = pd.read_csv(Path(data) / "HI-Small_accounts.csv", dtype=str).drop_duplicates("Account Number")
    tx["ts"] = pd.to_datetime(tx["ts"], format="%Y/%m/%d %H:%M")
    for c in ("amt_recv", "amt_paid"):
        tx[c] = tx[c].astype(float)
    tx["y"] = tx["y"].astype(int)
    return tx, acc


DAY0 = pd.Timestamp("2022-09-01")  # first HI-Small timestamp's date; "day" feature is days since this
FORMATS = ["ACH", "Bitcoin", "Cash", "Cheque", "Credit Card", "Wire"]  # formats left after self-loop removal


def edge_features(e):
    """11 edge features (paper Sec. 2.2.2) from columns ts, amt_paid, fmt, cur_paid, cur_recv."""
    ef = pd.DataFrame(index=e.index)
    ef["log_amt"] = np.log1p(e["amt_paid"])
    h = e["ts"].dt.hour
    ef["h_sin"], ef["h_cos"] = np.sin(2 * np.pi * h / 24), np.cos(2 * np.pi * h / 24)
    ef["day"] = (e["ts"] - DAY0).dt.days
    for f in FORMATS:
        ef[f"fmt_{f}"] = (e["fmt"] == f).astype(float)
    ef["cur_mismatch"] = (e["cur_paid"] != e["cur_recv"]).astype(float)
    return ef


def build(tx, acc):
    """Returns (X [n_edges x 39], y, ts) for non-self-loop edges, following paper Sec. 2.2."""
    self_loop = tx["src"] == tx["dst"]
    e, s = tx[~self_loop].reset_index(drop=True), tx[self_loop]

    # node features (14), aggregated over E' (+ self-loops as reinvestment behaviour)
    ids = pd.Index(pd.unique(np.r_[acc["Account Number"].to_numpy(), tx["src"].to_numpy(), tx["dst"].to_numpy()]))
    node = pd.DataFrame(index=ids)
    out = e.groupby("src")["amt_paid"].agg(["count", "mean", "std"])
    inn = e.groupby("dst")["amt_recv"].agg(["count", "mean", "std"])
    node["out_deg"], node["out_mean"], node["out_std"] = out["count"], np.log1p(out["mean"]), np.log1p(out["std"])
    node["in_deg"], node["in_mean"], node["in_std"] = inn["count"], np.log1p(inn["mean"]), np.log1p(inn["std"])
    rein = s.groupby("src")["amt_paid"].agg(["count", "sum"])
    node["reinv_cnt"], node["reinv_vol"] = rein["count"], np.log1p(rein["sum"])
    node = node.fillna(0.0)
    ent = acc.set_index("Account Number")["Entity Name"].str.replace(r" #.*", "", regex=True).reindex(ids)
    for t in ENTITY_TYPES:
        node[f"ent_{t}"] = (ent == t).to_numpy(float)
    assert node.shape[1] == 14, node.shape

    ef = edge_features(e)
    assert ef.shape[1] == 11, list(ef.columns)

    nv = node.to_numpy(np.float32)
    X = np.hstack([nv[ids.get_indexer(e["src"])], nv[ids.get_indexer(e["dst"])], ef.to_numpy(np.float32)])
    names = [f"src_{c}" for c in node] + [f"dst_{c}" for c in node] + list(ef)
    return X, e["y"].to_numpy(), e["ts"], names, node


PAPER_CUTOFFS = ("2022-09-06T18:24", "2022-09-08T03:28")  # paper Sec. 2.7.1 (its tau70 / tau85)


def split(ts):
    """Train <= tau70 < val <= tau85 < test (paper Eq. 26), using the paper's stated cutoff timestamps."""
    t = ts.to_numpy().astype("datetime64[s]")  # unit-agnostic (pandas 3 may infer s/ms)
    t70, t85 = (np.datetime64(c, "s") for c in PAPER_CUTOFFS)
    return np.flatnonzero(t <= t70), np.flatnonzero((t > t70) & (t <= t85)), np.flatnonzero(t > t85), (t70, t85)


def metrics(y, s):
    p, r, t = precision_recall_curve(y, s)
    f1 = 2 * p * r / np.clip(p + r, 1e-12, None)
    i = int(np.argmax(f1[:-1]))  # last PR point has no threshold
    return {"auroc": 100 * roc_auc_score(y, s), "auprc": 100 * average_precision_score(y, s),
            "best_f1": 100 * f1[i], "best_precision": 100 * p[i], "best_recall": 100 * r[i], "best_threshold": float(t[i])}


# paper: Table 4 exactly. tuned: same 39 features/split, chosen by a small grid on validation AUPRC only
# (neg ratio {50,200} x depth {6,8} x min_child_weight {1,5}), early-stopped on validation.
CONFIGS = {
    "paper": dict(neg_ratio=50, n_estimators=300, max_depth=6, learning_rate=0.1, spw="ratio"),
    "tuned": dict(neg_ratio=200, n_estimators=3000, max_depth=8, learning_rate=0.05, spw="sqrt", early_stopping_rounds=150),
}


def run(X, y, tr, va, te, seed, config="paper", device="cpu"):
    c = dict(CONFIGS[config])
    rng = np.random.default_rng(seed)
    pos, neg = tr[y[tr] == 1], tr[y[tr] == 0]
    ratio = c.pop("neg_ratio")
    sub = np.sort(np.r_[pos, rng.choice(neg, min(len(neg), ratio * len(pos)), replace=False)])
    spw = (y[sub] == 0).sum() / (y[sub] == 1).sum()
    c["scale_pos_weight"] = np.sqrt(spw) if c.pop("spw") == "sqrt" else spw
    m = xgb.XGBClassifier(**c, subsample=0.8, colsample_bytree=0.8, tree_method="hist",
                          device=device, random_state=seed, n_jobs=-1, eval_metric="aucpr")
    m.fit(X[sub], y[sub], eval_set=[(X[va], y[va])], verbose=False)
    return m, {"val": metrics(y[va], m.predict_proba(X[va])[:, 1]), "test": metrics(y[te], m.predict_proba(X[te])[:, 1])}


def main(data, device="cpu", seeds=range(5), out=None, configs=("paper", "tuned")):
    tx, acc = load(data)
    X, y, ts, names, node = build(tx, acc)
    tr, va, te, cut = split(ts)
    print(f"edges {len(y):,} | train {len(tr):,} ({y[tr].sum()}) val {len(va):,} ({y[va].sum()}) "
          f"test {len(te):,} ({y[te].sum()}) | cutoffs {cut[0]} / {cut[1]}")
    print("paper:  train 2,590,591 (2421) val 658,773 (651) test 1,237,769 (2094) | 2022-09-06 18:24 / 2022-09-08 03:28")
    results = {}
    for cfg in configs:
        print(f"\n== {cfg} ==")
        results[cfg] = evaluate_config(X, y, tr, va, te, cfg, device, seeds, out, names)
    if out:  # serving needs per-account node features to assemble the 39-dim vector
        node.rename_axis("account").reset_index().to_parquet(Path(out) / "ibm_node_features.parquet")
        (Path(out) / "ibm_xgb_summary.json").write_text(json.dumps(
            {"paper": PAPER, "split_cutoffs": PAPER_CUTOFFS, "features": names, "test_mean_sd": results}, indent=2))
    return results


def evaluate_config(X, y, tr, va, te, config, device, seeds, out, names):
    runs = []
    for sd in seeds:
        m, r = run(X, y, tr, va, te, sd, config, device)
        runs.append(r)
        print(f"seed {sd}: val auprc {r['val']['auprc']:.2f} | test " + "  ".join(f"{k} {v:.2f}" for k, v in r["test"].items()))
    agg = {k: (float(np.mean([r["test"][k] for r in runs])), float(np.std([r["test"][k] for r in runs])))
           for k in runs[0]["test"]}
    print("\nTEST mean ± sd over seeds vs paper (Table 12)")
    for k, (mu, sd) in agg.items():
        print(f"  {k:15s} {mu:6.2f} ± {sd:.2f}   paper {PAPER.get(k, float('nan')):6.2f}")
    gap = agg["auprc"][0] - PAPER["auprc"]
    verdict = "EXCEEDS" if gap >= 0 else "WITHIN 1 SD OF" if gap >= -PAPER["auprc_sd"] else "BELOW"
    print(f"AUPRC gap vs paper: {gap:+.2f} pts -> {verdict} paper")
    if out:
        Path(out).mkdir(parents=True, exist_ok=True)
        m.save_model(Path(out) / f"ibm_xgb_{config}.json")  # last seed's model is the one served
        (Path(out) / f"ibm_xgb_{config}_results.json").write_text(json.dumps(
            {"config": CONFIGS[config], "paper": PAPER, "runs": runs, "test_mean_sd": agg, "features": names,
             "serving_seed": seeds[-1], "operating_threshold": runs[-1]["val"]["best_threshold"]}, indent=2))
    return agg


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    main(a.data, a.device, range(a.seeds), a.out)
