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
from .xgb import load, split


def threshold_of(res):
    return (res.get("results", res))["val"]["best_threshold"]  # gnn results nest under "results"


def main(data, out):
    o = Path(out)
    tx, _ = load(data)
    e = tx[tx["src"] != tx["dst"]].sort_values("ts", kind="stable").reset_index(drop=True)
    _, _, te, _ = split(e["ts"])
    pat = parse_patterns(Path(data) / "HI-Small_Patterns.txt")
    key = lambda df: df["ts"].dt.floor("min").astype(str) + "|" + df["src"] + "|" + df["dst"] + "|" + df["amt_paid"].round(2).astype(str)
    typ = pd.Series(pat["typology"].to_numpy(), index=key(pat))
    typ = typ[~typ.index.duplicated()]
    t_test = typ.reindex(key(e.iloc[te])).to_numpy()
    y = e["y"].to_numpy()[te]
    t_test = np.where((y == 1) & pd.isna(t_test), "UNLISTED", t_test)  # illicit edges not in any listed pattern
    rows = {}
    for f in sorted(o.glob("ibm_*_scores.npz")):
        name = f.name[4:-11]
        rj = o / f"ibm_{name}_results.json"
        if not rj.exists():
            continue
        z = np.load(f)
        if len(z["s_test"]) != len(te):
            print(f"skip {name}: {len(z['s_test'])} test scores != {len(te)} test edges")
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
