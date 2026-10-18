"""Shared data, features and evaluation for the HI-Small model scripts in ml/ibm/.

Features are built once and cached (`python -m ml.ibm.common --data <dir> --cache <dir>`); every
model script reads the cache, so each script only trains and evaluates its own model.

Feature groups (leakage-safe except `cycles`):
  safe39     paper's 39 features with node aggregates from TRAIN-period transactions only
  temporal   per-edge account state from strictly earlier edges (1h/24h/7d/30d windows, recency, counterparties)
  graph      SVD embedding of the train graph + 2-/3-cycle closure counts (Bonato & Szava 2025)
  cycles     RETIRED: 2-/3-cycle counts on each split's visible graph. Each edge saw later edges of its own
             split (the return leg leaked); strictly-past 2-cycles are already pair_reciprocal in `temporal`
Every model saves val/test scores as ibm_<model>_scores.npz, which conformal.py consumes.
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.sparse as sp
from scipy.sparse.linalg import svds

from ..tracelayer.features import _account_state, _distinct_cp_to_date, _pair_counts
from .xgb import build, edge_features, load, metrics, split

HUB_DEGREE = 1000  # ponytail: hubs dropped from 3-cycle counting to keep A@A sparse; raise if RAM allows
GROUPS = ("safe39", "temporal", "graph")  # cycles is retired (lookahead); build only with --groups cycles


def load_edges(data):
    """Time-sorted non-self-loop edges, labels, split indices, leakage-safe 39-feature matrix, train-period node features."""
    tx, acc = load(data)
    tx = tx.sort_values("ts", kind="stable").reset_index(drop=True)
    e = tx[tx["src"] != tx["dst"]].reset_index(drop=True)
    tr, va, te, cut = split(e["ts"])
    _, _, _, names, node_tr = build(tx[tx["ts"] <= cut[0]], acc)  # node aggregates from train period only
    nv = node_tr.reindex(pd.Index(pd.unique(np.r_[e["src"], e["dst"]]))).fillna(0)
    safe = np.hstack([nv.loc[e["src"]].to_numpy(np.float32), nv.loc[e["dst"]].to_numpy(np.float32), edge_features(e).to_numpy(np.float32)])
    return e, e["y"].to_numpy(), (tr, va, te), pd.DataFrame(safe, columns=names), node_tr


def temporal_features(e):
    n = len(e)
    t = ((e["ts"] - pd.Timestamp("2022-01-01")) // pd.Timedelta(seconds=1)).to_numpy(np.int64)
    codes, _ = pd.factorize(np.r_[e["src"].to_numpy(), e["dst"].to_numpy()])
    o, d = codes[:n], codes[n:]
    acct, tt, is_out = np.r_[o, d], np.r_[t, t], np.r_[np.ones(n), np.zeros(n)]
    amt = np.r_[e["amt_paid"].to_numpy(float), e["amt_recv"].to_numpy(float)]
    st = _account_state(acct, tt, {"in_cnt": 1 - is_out, "in_sum": (1 - is_out) * amt, "out_cnt": is_out, "out_sum": is_out * amt})
    cp = np.r_[d, o]
    for name, m in (("in_cp", is_out == 0), ("out_cp", is_out == 1)):
        st[name] = _distinct_cp_to_date(acct[m], cp[m], tt[m], acct, tt)
    f = pd.DataFrame(index=range(n))
    for p, part in (("o", st.iloc[:n]), ("d", st.iloc[n:])):
        for c in part.columns:
            f[f"{p}_{c}"] = part[c].to_numpy(np.float32)
        f[f"{p}_secs_since_last"] = f[f"{p}_secs_since_last"].fillna(1e7)
        f[f"{p}_flow_24h"] = f[f"{p}_out_sum_24h"] / (f[f"{p}_in_sum_24h"] + 1)
    a = e["amt_paid"].to_numpy(float)
    f["pass_through_24h"] = a / (f["o_in_sum_24h"] + 1)
    f["amt_vs_o_mean_7d"] = a / (f["o_out_sum_7d"] / f["o_out_cnt_7d"].clip(lower=1) + 1)
    f["pair_prior"], f["pair_reciprocal"] = _pair_counts(o, d, t)
    return f.astype(np.float32)


def graph_features(e, tr, k=16):
    ids, inv = np.unique(np.r_[e["src"].to_numpy(), e["dst"].to_numpy()], return_inverse=True)
    n, m = len(ids), len(e)
    s, d = inv[:m], inv[m:]
    A = sp.csr_matrix((np.ones(len(tr), np.float32), (s[tr], d[tr])), shape=(n, n))
    A.data[:] = 1
    U, S, _ = svds((A + A.T).astype(np.float32), k=k)
    emb = (U * np.sqrt(S)).astype(np.float32)
    deg = np.asarray((A + A.T).sum(1)).ravel()
    keep = sp.diags((deg <= HUB_DEGREE).astype(np.float32))
    Ak = keep @ A @ keep
    A2 = (Ak @ Ak).tocsr()  # A2[v, u] = #paths v->w->u, so edge u->v closes that many 3-cycles
    f = pd.DataFrame({"cyc2": np.asarray(A[d, s]).ravel(), "cyc3": np.asarray(A2[d, s]).ravel()}, dtype=np.float32)
    for j in range(k):
        f[f"src_emb{j}"], f[f"dst_emb{j}"] = emb[s, j], emb[d, j]
    return f


def cycle_features(e, splits):
    """2-/3-cycle closure counts on each split's VISIBLE graph (train / train+val / all, as in the GNN protocol),
    so counts are as fresh for test rows as for train rows. No embedding: SVD axes don't align across graphs."""
    ids, inv = np.unique(np.r_[e["src"].to_numpy(), e["dst"].to_numpy()], return_inverse=True)
    n, m = len(ids), len(e)
    s, d = inv[:m], inv[m:]
    out = np.zeros((m, 2), np.float32)
    for rows in splits:
        vis = np.arange(rows.max() + 1)  # edges are time-sorted, so the visible graph is a prefix
        A = sp.csr_matrix((np.ones(len(vis), np.float32), (s[vis], d[vis])), shape=(n, n))
        A.data[:] = 1
        deg = np.asarray((A + A.T).sum(1)).ravel()
        keep = sp.diags((deg <= HUB_DEGREE).astype(np.float32))
        Ak = keep @ A @ keep
        A2 = (Ak @ Ak).tocsr()
        out[rows, 0] = np.asarray(A[d[rows], s[rows]]).ravel()
        out[rows, 1] = np.asarray(A2[d[rows], s[rows]]).ravel()
        del A, Ak, A2
    return pd.DataFrame(out, columns=["cyc2_visible", "cyc3_visible"])


def build_cache(data, cache, groups=GROUPS):
    t0 = time.time()
    c = Path(cache)
    c.mkdir(parents=True, exist_ok=True)
    e, y, (tr, va, te), safe, node_tr = load_edges(data)
    node_tr.rename_axis("account").reset_index().to_parquet(c / "ibm_node_features_train.parquet")  # serving needs it
    print(f"edges {len(e):,} | train {len(tr):,} val {len(va):,} test {len(te):,} ({time.time() - t0:.0f}s)", flush=True)
    np.savez(c / "ibm_split.npz", y=y, tr=tr, va=va, te=te, amt=e["amt_paid"].to_numpy())
    make = {"safe39": lambda: safe, "temporal": lambda: temporal_features(e), "graph": lambda: graph_features(e, tr),
            "cycles": lambda: cycle_features(e, (tr, va, te))}
    for g in groups:
        make[g]().to_parquet(c / f"ibm_feat_{g}.parquet")
        print(f"{g} done ({time.time() - t0:.0f}s)", flush=True)


def load_cache(cache, groups, as_array=False):
    """DataFrame of the feature groups, or (with as_array) one float32 C-contiguous array + column names
    filled group by group, so only one full-size copy ever exists (HI-Small x 119 cols is ~2.1 GB)."""
    c = Path(cache)
    z = np.load(c / "ibm_split.npz")
    split = z["y"], (z["tr"], z["va"], z["te"]), z["amt"]
    if not as_array:
        return (pd.concat([pd.read_parquet(c / f"ibm_feat_{g}.parquet") for g in groups], axis=1), *split)
    names, X, j = [], None, 0
    for g in groups:
        f = pd.read_parquet(c / f"ibm_feat_{g}.parquet")
        if X is None:
            X = np.empty((len(f), sum(_ncols(c, h) for h in groups)), np.float32)
        X[:, j:j + f.shape[1]] = f.to_numpy(np.float32)
        names += list(f.columns)
        j += f.shape[1]
        del f
    return (X, names), *split


def _ncols(c, g):
    import pyarrow.parquet as pq
    return len([n for n in pq.read_schema(c / f"ibm_feat_{g}.parquet").names if not n.startswith("__index")])


def at_threshold(y, s, thr):
    p = s >= thr
    tp, fp, fn = int((p & (y == 1)).sum()), int((p & (y == 0)).sum()), int((~p & (y == 1)).sum())
    return {"threshold": float(thr), "precision": 100 * tp / max(tp + fp, 1), "recall": 100 * tp / max(tp + fn, 1),
            "f1": 100 * 2 * tp / max(2 * tp + fp + fn, 1), "false_alerts_per_1k": 1000 * fp / len(y), "tp": tp, "fp": fp, "fn": fn, "tn": int((~p & (y == 0)).sum())}


def evaluate_and_save(name, y, s, va, te, out, extra=None):
    """Val/test metrics, test @ val threshold, P@K; writes ibm_<name>_results.json and ibm_<name>_scores.npz."""
    v, t = metrics(y[va], s[va]), metrics(y[te], s[te])
    k = np.argsort(-s[te],kind="stable")
    t.update({f"precision@{K}": 100 * float(y[te][k[:K]].mean()) for K in (100, 1000)})
    t.update({f"recall@{K}": 100 * float(y[te][k[:K]].sum()) / max(int(y[te].sum()),1) for K in (100,1000)})
    res = {"model": name, "val": v, "test": t, "test_at_val_threshold": at_threshold(y[te], s[te], v["best_threshold"]), **(extra or {})}
    o = Path(out)
    o.mkdir(parents=True, exist_ok=True)
    (o / f"ibm_{name}_results.json").write_text(json.dumps(res, indent=2, default=float))
    np.savez_compressed(o / f"ibm_{name}_scores.npz", y_val=y[va], s_val=s[va].astype(np.float32), y_test=y[te], s_test=s[te].astype(np.float32))
    print(f"{name}: val AUPRC {v['auprc']:.2f} | test AUPRC {t['auprc']:.2f} AUROC {t['auroc']:.2f} best F1 {t['best_f1']:.2f} "
          f"| F1@val-thr {res['test_at_val_threshold']['f1']:.2f} alerts/1k {res['test_at_val_threshold']['false_alerts_per_1k']:.2f}", flush=True)
    return res


def fit_xgb(name, groups, a):
    """Tuned XGBoost (ml/ibm/xgb.py config) on the given cached feature groups; saves model + results."""
    from .xgb import run
    (X, names), y, (tr, va, te), _ = load_cache(a.cache, groups, as_array=True)
    m, _ = run(X, y, tr, va, te, a.seed, "tuned", a.device)
    Path(a.out).mkdir(parents=True, exist_ok=True)
    m.save_model(Path(a.out) / f"ibm_{name}.json")
    imp = m.get_booster().get_score(importance_type="gain")
    top = dict(sorted(imp.items(), key=lambda kv: -kv[1])[:15])
    top = {names[int(k[1:])] if k.startswith("f") and k[1:].isdigit() else k: v for k, v in top.items()}
    b = m.get_booster()
    b.set_param({"device": "cpu"})  # in-place CPU predict on 500k-row views: no DMatrix or GPU copy of X
    s = np.concatenate([b.inplace_predict(X[i:i + 500_000], iteration_range=(0, m.best_iteration + 1)) for i in range(0, len(X), 500_000)])
    return evaluate_and_save(name, y, s, va, te, a.out,
                             {"feature_groups": list(groups), "n_features": X.shape[1], "top_features_by_gain": top})


def cli(description):
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("--cache", default="ml/artifacts/ibm/cache", help="feature cache from `python -m ml.ibm.common`")
    ap.add_argument("--out", default="ml/artifacts/ibm")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--seed", type=int, default=7)
    return ap.parse_args()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--cache", default="ml/artifacts/ibm/cache")
    ap.add_argument("--groups", nargs="+", default=list(GROUPS), help="build only these groups (e.g. --groups cycles)")
    a = ap.parse_args()
    build_cache(a.data, a.cache, a.groups)
