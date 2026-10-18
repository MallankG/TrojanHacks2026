"""PRD metric "recall by typology" for every trained model on HI-Small.

Maps each test edge to its laundering pattern type via HI-Small_Patterns.txt, then reports each
model's recall per typology at its validation-chosen threshold (from ibm_<model>_results.json).
Reads ibm_<model>_scores.npz, whose test rows are the time-sorted test edges in order.

    python -m ml.ibm.typology_recall --data <HI-Small dir> --out ml/artifacts/ibm
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .explain import parse_patterns
from .order import npz_order, order_edges
from .xgb import load, split


def threshold_of(res):
    return (res.get("results", res))["val"]["best_threshold"]  # gnn results nest under "results"


def main(data, out):
    o = Path(out)
    tx, _ = load(data)
    raw = tx[tx["src"] != tx["dst"]]
    pat = parse_patterns(Path(data) / "HI-Small_Patterns.txt")
    key = lambda df: df["ts"].dt.floor("min").astype(str) + "|" + df["src"] + "|" + df["dst"] + "|" + df["amt_paid"].round(2).astype(str)
    typ = pd.Series(pat["typology"].to_numpy(), index=key(pat))
    typ = typ[~typ.index.duplicated()]
    labels = {}

    def test_labels(version):  # (y, typology) of test edges in the row order that score file was written in
        if version not in labels:
            e = order_edges(raw, version)
            _, _, te, _ = split(e["ts"])
            t, yy = typ.reindex(key(e.iloc[te])).to_numpy(), e["y"].to_numpy()[te]
            labels[version] = (yy, np.where((yy == 1) & pd.isna(t), "UNLISTED", t))  # illicit, in no listed pattern
        return labels[version]

    rows, y, t_test = {}, None, None
    for f in sorted(o.glob("ibm_*_scores.npz")):
        name = f.name[4:-11]
        rj = o / f"ibm_{name}_results.json"
        if not rj.exists():
            continue
        z = np.load(f)
        y, t_test = test_labels(npz_order(z))
        if len(z["s_test"]) != len(y) or not np.array_equal(z["y_test"], y):
            print(f"skip {name}: test rows don't line up with the {npz_order(z) or 'legacy'} edge order")
            continue
        pred = z["s_test"] >= threshold_of(json.loads(rj.read_text()))
        rows[name] = {t: float(pred[(y == 1) & (t_test == t)].mean()) for t in sorted(set(t_test[y == 1]))}
    df = pd.DataFrame(rows)
    df["n_test_edges"] = pd.Series({t: int(((y == 1) & (t_test == t)).sum()) for t in df.index})
    (o / "ibm_typology_recall.json").write_text(df.to_json(indent=2))
    print((100 * df.drop(columns="n_test_edges")).round(1).assign(n=df["n_test_edges"]).to_string())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default="ml/artifacts/ibm")
    a = ap.parse_args()
    main(a.data, a.out)
