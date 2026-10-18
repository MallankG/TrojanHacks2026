"""One canonical edge order for every HI-Small script and scorer that assigns edge ids.

Edge ids feed the GNN's hashed sampling salts, CSR offsets and the row alignment of saved score files,
so training, re-evaluation, XGBoost, the ensemble, typology recall and serving must all order edges the
same way. Order: time, then the row's own fields. String columns are cast with astype(str) (NaN -> "nan")
and factorised with SORTED categories, so the order is deterministic and NaN-safe. Rows identical on every
key are indistinguishable inputs, so their relative order cannot affect any feature or score.

Score files carry `order` = ORDER_VERSION; files without it predate this module and use the legacy
time-then-input order (order_edges(e, None)).
"""
import numpy as np
import pandas as pd

CANON_KEYS = ("ts", "from_bank", "src", "to_bank", "dst", "amt_paid", "amt_recv", "cur_paid", "cur_recv", "fmt")
ORDER_VERSION = "canon-v1"


def canonical_order(e: pd.DataFrame) -> np.ndarray:
    """Positions that sort `e` canonically (np.lexsort over integer / float keys only)."""
    missing = [c for c in CANON_KEYS if c not in e]
    if missing:
        raise ValueError(f"canonical order needs columns {missing}")
    keys = []
    for c in CANON_KEYS:
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


def order_edges(e: pd.DataFrame, version) -> pd.DataFrame:
    """Order edges the way a score file was written: canon-v1, or legacy (None: time, then input order)."""
    if version == ORDER_VERSION:
        return canonical(e)
    if version is None:
        return e.sort_values("ts", kind="stable").reset_index(drop=True)
    raise ValueError(f"unknown edge order version {version!r}")


def npz_order(z):
    """The `order` stamp of a loaded score/split npz (None for legacy files)."""
    return str(z["order"]) if "order" in z else None
