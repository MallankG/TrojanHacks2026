"""Alert fusion (PRD §5): keep every component signal separate, assign priority with a documented, versioned,
configurable policy (no invented blend weights), and group related alerts into clusters without dropping findings.

    from ml.engine.fusion import POLICY, fuse, cluster
    alerts = fuse(signals)        # signals: one row per transaction, component columns described in POLICY
    alerts = cluster(alerts, tx)  # adds cluster_id: alerts sharing an account within the window
"""
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PriorityPolicy:
    """Bands from which independent families flagged, not from a weighted sum. Each model's own threshold
    (chosen on validation) decides whether it flagged; the policy only counts and orders them."""
    version: str = "priority-1.0"
    # component -> family. Two models of the same family agreeing count as one independent family.
    families: dict = field(default_factory=lambda: {"xgb_flag": "supervised", "gnn_flag": "graph_model", "rule_hits": "rules",
                                                    "graph_hits": "graph_pattern", "anomaly_flag": "anomaly"})
    critical_families: int = 3     # >= 3 independent families flagged
    high_families: int = 2         # 2 families, or a supervised/graph model above its high-confidence score
    high_confidence_score: float = 0.99
    review_families: int = 1       # anything flagged at all
    quality_caveat_floor: str = "medium"  # data-quality issues never LOWER priority; they raise it to review

    def describe(self) -> dict:
        return asdict(self)


POLICY = PriorityPolicy()
BANDS = ["low", "medium", "high", "critical"]


def fuse(signals: pd.DataFrame, policy: PriorityPolicy = POLICY) -> pd.DataFrame:
    """signals columns (any subset): xgb_flag, gnn_flag, anomaly_flag (bool); rule_hits, graph_hits (int count);
    xgb_score, gnn_score (float); quality_issues (int). Returns signals + priority_band, families, reasons."""
    s = signals.copy()
    fams = []
    for i in range(len(s)):
        row = s.iloc[i]
        fams.append(sorted({fam for col, fam in policy.families.items() if col in s and bool(row[col]) and row[col] == row[col]}))
    n = np.array([len(f) for f in fams])
    top_model = s[[c for c in ("xgb_score", "gnn_score") if c in s]].max(axis=1).fillna(0).to_numpy() if {"xgb_score", "gnn_score"} & set(s) else np.zeros(len(s))
    band = np.select([n >= policy.critical_families, (n >= policy.high_families) | (top_model >= policy.high_confidence_score),
                      n >= policy.review_families], ["critical", "high", "medium"], "low")
    if "quality_issues" in s:
        floor = BANDS.index(policy.quality_caveat_floor)
        band = np.where((s["quality_issues"].fillna(0) > 0) & (np.array([BANDS.index(b) for b in band]) < floor), policy.quality_caveat_floor, band)
    s["families"] = fams
    s["priority_band"] = band
    s["priority_rank"] = (pd.Series([BANDS.index(b) for b in band], index=s.index) * 10 + top_model).rank(ascending=False, method="first").astype(int)
    s["reasons"] = [_reasons(s.iloc[i], fams[i], top_model[i], policy) for i in range(len(s))]
    s["policy_version"] = policy.version
    return s


def _reasons(row, fams, top, policy):
    r = [f"{f} flagged" for f in fams]
    if top >= policy.high_confidence_score:
        r.append(f"model score {top:.3f} >= high-confidence {policy.high_confidence_score}")
    if row.get("quality_issues", 0) and row.get("quality_issues", 0) == row.get("quality_issues", 0):
        r.append(f"{int(row['quality_issues'])} data-quality issue(s): review confidence before acting")
    return r


def cluster(alerts: pd.DataFrame, tx: pd.DataFrame, window_hours: float = 72) -> pd.DataFrame:
    """Union alerts (rows with priority above low) that share an account within window_hours into clusters.
    Every alert keeps its own findings; cluster_id only groups them for review (dedup without deletion)."""
    a = alerts[alerts["priority_band"] != "low"].copy()
    t = pd.to_datetime(tx.loc[a.index, "timestamp"], utc=True)
    parent = {i: i for i in a.index}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    w = pd.Timedelta(hours=window_hours)
    for col in ("origin_account_id", "destination_account_id"):
        for _, idx in tx.loc[a.index].groupby(col).groups.items():
            idx = sorted(idx, key=lambda i: t[i])
            for prev, cur in zip(idx, idx[1:]):
                if t[cur] - t[prev] <= w:
                    parent[find(cur)] = find(prev)
    roots = {i: find(i) for i in a.index}
    ids = {r: k for k, r in enumerate(sorted(set(roots.values())))}
    out = alerts.copy()
    out["cluster_id"] = pd.Series({i: ids[r] for i, r in roots.items()}).reindex(out.index)
    out["cluster_size"] = out.groupby("cluster_id")["cluster_id"].transform("size")
    return out
