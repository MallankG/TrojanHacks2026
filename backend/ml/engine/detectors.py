"""PRD §5 typology detectors not covered by ml/tracelayer/features.RULES or ml/ibm/rules.py.

Every detector is deterministic, time-respecting (only events strictly before each transaction count) and
returns per-transaction booleans plus the evidence behind them. A detector whose input field is absent
returns NaN ("not evaluated"), never False: missing data is not low risk (PRD §10).

    from ml.engine.detectors import run_detectors
    fired, notes = run_detectors(tx, accounts)            # tx: canonical transactions frame
    graph = graph_detectors(edges)                         # split-merge + cycles, via ml.graph.motifs
"""
import numpy as np
import pandas as pd

# ponytail: policy-configurable review thresholds, hand-set for the demo; these are review bands, not legal rules
CONFIG = {
    "new_beneficiary_min_amount": 3000.0,  # first-ever payment to this payee
    "new_beneficiary_min_origin_age_days": 30,  # established payer suddenly paying someone new
    "campaign_window_hours": 24,
    "campaign_min_new_payees": 4,  # same payer, near-identical amounts, several brand-new payees
    "campaign_amount_tolerance": 0.02,
    # payroll is the textbook benign look-alike (many new payees, identical amounts); measured on the synthetic
    # feed, every campaign hit without this exclusion was an employer paying salaries (870 hits, 0 suspicious)
    "campaign_exclude_payer_types": ("employer",),
    "profile_mismatch_ratio": 3.0,  # 30-day outflow vs declared expected monthly amount
    "geo_shift_window_days": 30,
}


def _prep(tx):
    df = pd.DataFrame({"o": tx["origin_account_id"].astype(str).to_numpy(), "d": tx["destination_account_id"].astype(str).to_numpy(),
                       "ts": pd.to_datetime(tx["timestamp"], utc=True), "amt": tx["amount"].to_numpy(float)}, index=tx.index)
    return df.sort_values("ts", kind="stable")


def _first_seen(df, keys):
    """Timestamp each key combination first occurred (an event is 'new' iff its own ts is that first ts)."""
    return df.groupby(keys, sort=False)["ts"].transform("min")


def new_beneficiary(df, accounts):
    """Origin pays this destination for the first time, for a large amount, after an established history."""
    first_pair = _first_seen(df, ["o", "d"])
    opened = pd.to_datetime(accounts.set_index("account_id")["open_date"], utc=True)
    age_days = (df["ts"] - df["o"].map(opened)).dt.days
    fired = (df["ts"] == first_pair) & (df["amt"] >= CONFIG["new_beneficiary_min_amount"]) & (age_days >= CONFIG["new_beneficiary_min_origin_age_days"])
    return fired, "first payment to this payee, above the review band, from an established payer"


def repeated_campaign(df, accounts):
    """Same origin sends near-identical amounts to several brand-new payees within a window (strictly past).
    Payers of a configured type (payroll employers) are excluded as known benign look-alikes."""
    w = pd.Timedelta(hours=CONFIG["campaign_window_hours"])
    new = (df["ts"] == _first_seen(df, ["o", "d"])).to_numpy().copy()
    if "account_type" in accounts:
        new &= ~df["o"].map(accounts.set_index("account_id")["account_type"]).isin(CONFIG["campaign_exclude_payer_types"]).to_numpy()
    tol = CONFIG["campaign_amount_tolerance"]
    fired = np.zeros(len(df), bool)
    ts, amt = df["ts"].to_numpy(), df["amt"].to_numpy()
    nd = new.nonzero()[0]  # only first-time-payee payments can take part in a campaign
    for _, idx in pd.Series(nd).groupby(df["o"].to_numpy()[nd], sort=False).indices.items():
        idx = nd[idx]  # this payer's first-time-payee payments, time-ordered (df is sorted by ts)
        if len(idx) < CONFIG["campaign_min_new_payees"]:
            continue
        t, a = ts[idx], amt[idx]
        lo = np.searchsorted(t, t - w, side="left")
        hi = np.searchsorted(t, t, side="left")  # strictly earlier timestamps only
        for k in range(len(idx)):
            win = a[lo[k]:hi[k]]
            fired[idx[k]] = (np.abs(win - a[k]) <= tol * max(a[k], 1.0)).sum() + 1 >= CONFIG["campaign_min_new_payees"]
    return pd.Series(fired, index=df.index), "near-identical amounts to several first-time payees inside the window"


def profile_mismatch(df, accounts):
    """30-day outflow far above the account's declared expected monthly amount. Needs accounts.expected_monthly_amount."""
    if "expected_monthly_amount" not in accounts:
        return pd.Series(np.nan, index=df.index), "not evaluated: accounts.expected_monthly_amount missing"
    exp = df["o"].map(accounts.set_index("account_id")["expected_monthly_amount"].astype(float))
    out30 = df.groupby("o", sort=False).rolling("30D", on="ts", closed="left")["amt"].sum().reset_index(level=0, drop=True)
    out30 = out30.reindex(df.index).fillna(0) + df["amt"]  # past 30 days strictly before + this payment
    fired = (out30 > CONFIG["profile_mismatch_ratio"] * exp).where(exp.notna())
    return fired, "30-day outflow exceeds the declared expected activity (profile context, not proof)"


def geography_shift(df, tx):
    """Origin transacts from a geography token not seen in its past window. Needs tx.geo_token."""
    if "geo_token" not in tx:
        return pd.Series(np.nan, index=df.index), "not evaluated: transactions.geo_token missing"
    g = tx.loc[df.index, "geo_token"].astype(str)
    first_geo = df.assign(g=g).groupby(["o", "g"], sort=False)["ts"].transform("min")
    has_history = df["ts"] > df.groupby("o", sort=False)["ts"].transform("min")
    fired = (df["ts"] == first_geo) & has_history
    return fired, "first activity from this geography for an account with prior history (context, not proof)"


def run_detectors(tx: pd.DataFrame, accounts: pd.DataFrame):
    """All non-graph detectors. Returns (fired DataFrame aligned to tx.index, {detector: evidence note})."""
    df = _prep(tx)
    results = {"new_beneficiary": new_beneficiary(df, accounts), "repeated_campaign": repeated_campaign(df, accounts),
               "profile_mismatch": profile_mismatch(df, accounts), "geography_shift": geography_shift(df, tx)}
    fired = pd.DataFrame({k: v[0] for k, v in results.items()}).loc[tx.index]
    return fired, {k: v[1] for k, v in results.items()}


def graph_detectors(edges: list[dict], motif_limits: dict | None = None, cycle_limits: dict | None = None,
                    focus_account: str | None = None):
    """Split-merge motifs and bounded time-respecting cycles from the GraphBase engine (ml.graph, PRD §6).
    edges: canonical dicts {transaction_id, origin_account_id, destination_account_id, amount, occurred_at}.
    Returns ({transaction_id: [finding, ...]}, stats) so each flagged transaction keeps its edge-level evidence
    and any truncation caveat travels with the result."""
    from ml.graph import find_cycles, find_motifs  # graph engine (feat/graph-engine) owns the bounded search

    stats = {"split_merge": {}, "cycles": {}}
    found = (find_motifs("split_merge", edges, **(motif_limits or {}), stats=stats["split_merge"])
             + find_cycles(edges, **(cycle_limits or {}), stats=stats["cycles"]))
    if focus_account is not None:  # an account's slice also holds neighbours' patterns; keep only its own
        found = [f for f in found if focus_account in f["accounts"]]
    by_tx: dict[str, list[dict]] = {}
    for f in found:
        for tid in f["edge_ids"]:
            by_tx.setdefault(tid, []).append(f)
    return by_tx, stats
