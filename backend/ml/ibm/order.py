"""One canonical edge order for every HI-Small script and scorer that assigns edge ids.

Edge ids feed the GNN's hashed sampling salts, CSR offsets and the row alignment of saved score files,
so training, re-evaluation, XGBoost, the ensemble, typology recall and serving must all order edges the
same way. Order: time, then the row's own fields. String columns are cast with astype(str) (NaN -> "nan")
and factorised with SORTED categories, so the order is deterministic, NaN-safe and independent of input order.
"""
import numpy as np
import pandas as pd

CANON_KEYS = ("ts", "src", "dst", "amt_paid", "amt_recv", "cur_paid", "cur_recv", "fmt")


def canonical_order(e: pd.DataFrame) -> np.ndarray:
    """Positions that sort `e` canonically (np.lexsort over integer / float keys only)."""
    keys = []
    for c in CANON_KEYS:
        if c not in e:
            continue
        v = e[c]
        if c == "ts":
            keys.append(pd.to_datetime(v).to_numpy("datetime64[ns]").astype(np.int64))
        elif c.startswith("amt"):
            keys.append(pd.to_numeric(v, errors="coerce").fillna(-np.inf).to_numpy(np.float64))
        else:
            keys.append(pd.Categorical(v.astype(str)).codes.astype(np.int64))  # categories are sorted
    return np.lexsort(keys[::-1])


def canonical(e: pd.DataFrame) -> pd.DataFrame:
    return e.iloc[canonical_order(e)].reset_index(drop=True)
