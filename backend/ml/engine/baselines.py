"""Robust per-account behavioural baselines (PRD §5 "Temporal behavior": rolling robust baselines + seasonal first).

Every value for a transaction at time t uses only the origin account's events strictly before t: rolling
stats are shifted one row, then rows sharing (account, timestamp) all take the first such row's stats, so
same-second activity never leaks in.

    from ml.engine.baselines import account_baselines
    b = account_baselines(tx)   # tx: transaction_id, timestamp, amount, origin_account_id (any order)
"""
import numpy as np
import pandas as pd

WINDOW = 50       # ponytail: last-50-event robust window per account; widen if accounts are very active
MIN_HISTORY = 5   # fewer prior events -> baseline is NaN ("not enough history"), never a fake zero
MAD_SCALE = 1.4826  # MAD -> sigma for normal data
MAD_FLOOR_LOG = 0.05   # ~5% amount spread minimum (log scale)
MAD_FLOOR_SECS = 60.0  # 1 minute inter-arrival spread minimum


def _strictly_past(df, col, func):
    """Rolling stat over the previous WINDOW rows of the same account, excluding same-timestamp rows."""
    g = df.groupby("acct", sort=False)[col]
    stat = g.transform(lambda s: func(s.rolling(WINDOW, min_periods=MIN_HISTORY)).shift(1))
    return stat.groupby([df["acct"], df["ts"]], sort=False).transform("first")


def account_baselines(tx: pd.DataFrame) -> pd.DataFrame:
    """Per transaction (aligned to tx's index): robust amount z-score, inter-arrival z-score, burstiness and
    hour-of-week seasonality of the origin account, from its strictly-past history."""
    df = pd.DataFrame({"acct": tx["origin_account_id"].to_numpy(), "ts": pd.to_datetime(tx["timestamp"], utc=True),
                       "amt": tx["amount"].to_numpy(float)}, index=tx.index)
    df = df.sort_values(["acct", "ts"], kind="stable")
    df["log_amt"] = np.log1p(df["amt"])
    med = _strictly_past(df, "log_amt", lambda r: r.median())
    df["absdev"] = (df["log_amt"] - df.groupby("acct", sort=False)["log_amt"].transform(
        lambda s: s.rolling(WINDOW, min_periods=1).median())).abs()
    mad = _strictly_past(df, "absdev", lambda r: r.median())

    secs = (df["ts"] - df["ts"].min()).dt.total_seconds()
    df["gap"] = secs.groupby(df["acct"], sort=False).diff()
    # the gap of a same-second burst is 0, which is real; the first event's gap is NaN (no history)
    gap_med = _strictly_past(df, "gap", lambda r: r.median())
    df["absgap"] = (df["gap"] - df.groupby("acct", sort=False)["gap"].transform(
        lambda s: s.rolling(WINDOW, min_periods=1).median())).abs()  # same vectorised MAD as amounts
    gap_mad = _strictly_past(df, "absgap", lambda r: r.median())

    # burstiness: events in the previous hour vs the account's typical hourly rate over the previous 30 days
    t = df["ts"].astype("int64") // 10**9
    key = pd.factorize(df["acct"])[0].astype(np.int64) * 10**10 + t.to_numpy()
    right = np.searchsorted(key, key, side="left")  # strictly before t (key is sorted: acct, ts)
    last_1h = right - np.searchsorted(key, key - 3600, side="left")
    last_30d = right - np.searchsorted(key, key - 30 * 86400, side="left")

    # seasonality: share of the account's past events that fell in this hour-of-week
    # (cumcount counts earlier rows; taking the first row per (acct, ts) drops same-second ones)
    first = lambda s: s.groupby([df["acct"], df["ts"]], sort=False).transform("first").to_numpy()
    df["how"] = df["ts"].dt.dayofweek * 24 + df["ts"].dt.hour
    prior = first(df.groupby("acct", sort=False).cumcount())
    same_slot = first(df.groupby(["acct", "how"], sort=False).cumcount())

    out = pd.DataFrame(index=df.index)
    # floors: a perfectly regular history (MAD 0) makes any deviation large, not undefined
    out["amount_robust_z"] = (df["log_amt"] - med) / (MAD_SCALE * mad.clip(lower=MAD_FLOOR_LOG))
    out["gap_robust_z"] = (df["gap"] - gap_med) / (MAD_SCALE * gap_mad.clip(lower=MAD_FLOOR_SECS))
    out["burst_ratio_1h"] = last_1h / (last_30d / (30 * 24) + 1e-9)
    out["burst_ratio_1h"] = out["burst_ratio_1h"].where(prior >= MIN_HISTORY)
    out["hour_of_week_share"] = np.where(prior >= MIN_HISTORY, same_slot / np.maximum(prior, 1), np.nan)
    out["prior_events"] = prior
    return out.loc[tx.index]
