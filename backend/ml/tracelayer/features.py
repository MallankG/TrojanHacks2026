"""Time-respecting transaction features and deterministic typology rules.

Every feature for a transaction at time t uses only events strictly before t
(same-second events are excluded too), so the same code is leakage-safe for
training and for serving.
"""
import numpy as np
import pandas as pd

FEATURE_VERSION = "fs-1.0"
WINDOWS = {"1h": 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}
CHANNELS = ["card", "p2p", "ach", "wire", "cash"]
ACCOUNT_TYPES = ["individual", "merchant", "employer", "landlord", "supplier"]
NEAR_BAND = (8000.0, 10000.0)  # ponytail: policy-configurable review band, hard-coded for the demo


def _secs(ts):
    # unit-agnostic (pandas may store s/ms/us/ns)
    return ((pd.to_datetime(ts, utc=True) - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)).to_numpy().astype(np.int64)


def _account_state(acct, t, cols):
    """Per event (acct, t): sums of each value column over past windows, strictly before t."""
    order = np.lexsort((t, acct))
    a, tt = acct[order], t[order]
    key = a.astype(np.int64) * 10**10 + tt  # t < 1e10 s, so keys never cross accounts
    right = np.searchsorted(key, key, side="left")
    out = {}
    for w_name, w in WINDOWS.items():
        left = np.searchsorted(key, key - w, side="left")
        for c, v in cols.items():
            cs = np.r_[0.0, np.cumsum(v[order])]
            out[f"{c}_{w_name}"] = cs[right] - cs[left]
    # lifetime counters and recency
    first = np.searchsorted(key, a.astype(np.int64) * 10**10, side="left")
    out["n_events_to_date"] = (right - first).astype(float)
    prev_t = np.where(right > first, tt[np.maximum(right - 1, 0)], np.nan)
    out["secs_since_last"] = tt - prev_t
    res = pd.DataFrame(out)
    inv = np.empty_like(order)
    inv[order] = np.arange(len(order))
    return res.iloc[inv].reset_index(drop=True)


def _distinct_cp_to_date(acct, cp, t):
    """Count of distinct counterparties seen strictly before each event."""
    df = pd.DataFrame({"a": acct, "c": cp, "t": t, "i": np.arange(len(t))}).sort_values(["a", "t", "i"])
    df["new"] = ~df.duplicated(["a", "c"])
    df["cum"] = df.groupby("a")["new"].cumsum() - df["new"]
    return df.sort_values("i")["cum"].to_numpy().astype(float)


def build_features(tx: pd.DataFrame, accounts: pd.DataFrame) -> pd.DataFrame:
    """tx must be sorted by timestamp. Returns a feature frame aligned to tx rows."""
    n = len(tx)
    t = _secs(tx["timestamp"])
    amt = tx["amount"].to_numpy(float)
    ids = pd.Index(accounts["account_id"])
    o = ids.get_indexer(tx["origin_account_id"])
    d = ids.get_indexer(tx["destination_account_id"])
    if (o < 0).any() or (d < 0).any():
        raise ValueError("transactions reference unknown account ids")
    near = ((amt >= NEAR_BAND[0]) & (amt < NEAR_BAND[1])).astype(float)

    # Two events per transaction: an 'out' for the origin and an 'in' for the destination.
    acct = np.r_[o, d]
    tt = np.r_[t, t]
    is_out = np.r_[np.ones(n), np.zeros(n)]
    cols = {
        "in_cnt": 1 - is_out, "in_sum": (1 - is_out) * np.r_[amt, amt],
        "out_cnt": is_out, "out_sum": is_out * np.r_[amt, amt],
        "near_cnt": is_out * np.r_[near, near],
    }
    st = _account_state(acct, tt, cols)
    # distinct counterparties per direction
    in_mask, out_mask = is_out == 0, is_out == 1
    cp = np.r_[d, o]
    st["in_cp_to_date"] = 0.0
    st["out_cp_to_date"] = 0.0
    st.loc[in_mask, "in_cp_to_date"] = _distinct_cp_to_date(acct[in_mask], cp[in_mask], tt[in_mask])
    st.loc[out_mask, "out_cp_to_date"] = _distinct_cp_to_date(acct[out_mask], cp[out_mask], tt[out_mask])
    # carry the latest distinct count to the other direction's events via per-account ffill
    for c in ("in_cp_to_date", "out_cp_to_date"):
        own = in_mask if c.startswith("in") else out_mask
        s = pd.Series(np.where(own, st[c], np.nan))
        order = np.lexsort((tt, acct))
        s = s.iloc[order].groupby(acct[order]).ffill().fillna(0)
        st[c] = s.sort_index().to_numpy()

    f = pd.DataFrame(index=range(n))
    f["amount"] = amt
    f["log_amount"] = np.log1p(amt)
    f["round_100"] = (np.mod(amt, 100) == 0).astype(float)
    f["near_band"] = near
    ts = pd.to_datetime(tx["timestamp"], utc=True)
    f["hour"] = ts.dt.hour.to_numpy()
    f["dow"] = ts.dt.dayofweek.to_numpy()
    for c in CHANNELS:
        f[f"ch_{c}"] = (tx["channel"].to_numpy() == c).astype(float)

    acc_type = accounts["account_type"].to_numpy()
    open_s = _secs(accounts["open_date"])
    for p, idx, part in (("o", o, st.iloc[:n].reset_index(drop=True)), ("d", d, st.iloc[n:].reset_index(drop=True))):
        f[f"{p}_age_days"] = (t - open_s[idx]) / 86400
        for at in ACCOUNT_TYPES:
            f[f"{p}_type_{at}"] = (acc_type[idx] == at).astype(float)
        for c in part.columns:
            f[f"{p}_{c}"] = part[c].to_numpy()
        f[f"{p}_secs_since_last"] = f[f"{p}_secs_since_last"].fillna(1e8)
        for w in ("24h", "7d"):
            f[f"{p}_flow_ratio_{w}"] = f[f"{p}_out_sum_{w}"] / (f[f"{p}_in_sum_{w}"] + 1)
            f[f"{p}_net_flow_{w}"] = f[f"{p}_in_sum_{w}"] - f[f"{p}_out_sum_{w}"]
        f[f"{p}_out_mean_30d"] = f[f"{p}_out_sum_30d"] / f[f"{p}_out_cnt_30d"].clip(lower=1)
        f[f"{p}_in_mean_30d"] = f[f"{p}_in_sum_30d"] / f[f"{p}_in_cnt_30d"].clip(lower=1)

    # pass-through signal: share of what just came in that this payment forwards
    f["amt_over_o_in_24h"] = amt / (f["o_in_sum_24h"] + 1)
    f["amt_over_o_out_mean_30d"] = amt / (f["o_out_mean_30d"] + 1)

    # pair history: prior payments o->d and reciprocal d->o (strictly earlier rows)
    pair = pd.DataFrame({"lo": np.minimum(o, d), "hi": np.maximum(o, d), "fwd": (o < d).astype(int), "i": np.arange(n)})
    g = pair.groupby(["lo", "hi"])
    c_fwd = g["fwd"].cumsum() - pair["fwd"]
    c_rev = g.cumcount() - c_fwd
    same = np.where(pair["fwd"] == 1, c_fwd, c_rev)
    opp = np.where(pair["fwd"] == 1, c_rev, c_fwd)
    f["pair_prior_cnt"] = same.astype(float)
    f["pair_reciprocal_cnt"] = opp.astype(float)
    return f


def feature_columns(f: pd.DataFrame):
    return [c for c in f.columns if c != "amount"]


# --- deterministic typology rules (the transparent baseline) -------------
RULES = {
    "rapid_pass_through": lambda f: (f.amount > 1000) & (f.amt_over_o_in_24h.between(0.7, 1.05)) & (f.o_type_individual == 1),
    "funnel_many_to_one": lambda f: (f.d_in_cnt_7d >= 8) & (f.d_type_individual == 1),
    "dispersal_one_to_many": lambda f: (f.o_out_cnt_24h >= 5) & (f.o_in_sum_7d > 10000) & (f.o_type_individual == 1),
    "structuring_band": lambda f: (f.near_band == 1) & (f.o_near_cnt_7d >= 2),
    "dormant_reactivated": lambda f: ((f.o_secs_since_last > 30 * 86400) | (f.d_secs_since_last > 30 * 86400)) & (f.amount > 3000),
    "new_account_high_value": lambda f: ((f.o_age_days < 30) | (f.d_age_days < 30)) & (f.amount > 5000),
    "reciprocal_flow": lambda f: (f.pair_reciprocal_cnt > 0) & (f.amount > 3000) & (f.o_type_individual == 1) & (f.d_type_individual == 1),
}


def apply_rules(f: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({k: r(f).astype(int) for k, r in RULES.items()})
