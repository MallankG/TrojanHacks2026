"""Alert fusion: group related signals into alerts and rank them with the documented priority policy.

Pure function over Findings (each already written as a risk_signal). Every component signal stays visible on the
alert with its own score, strength, weight and contribution; rank_score is exactly the sum of those contributions.
"""

import math
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from app.services.detection import config
from app.services.detection.detectors import ROLE_RANK, Finding


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        self.parent[self.find(a)] = self.find(b)


def cluster(rules: list[Finding], window: timedelta) -> list[list[Finding]]:
    """Related rule findings: shared evidence transactions, or the same subject account in overlapping time."""
    uf = _UnionFind(len(rules))
    by_txn: dict[str, int] = {}
    by_subject: dict[str, list[int]] = defaultdict(list)
    for i, f in enumerate(rules):
        for t in f.evidence:
            if t in by_txn:
                uf.union(i, by_txn[t])
            else:
                by_txn[t] = i
        if f.subject:
            for j in by_subject[f.subject]:
                g = rules[j]
                if f.window_start <= g.window_end + window and g.window_start <= f.window_end + window:
                    uf.union(i, j)
            by_subject[f.subject].append(i)
    groups: dict[int, list[Finding]] = defaultdict(list)
    for i, f in enumerate(rules):
        groups[uf.find(i)].append(f)
    return sorted(groups.values(), key=lambda g: min(f.window_start for f in g))


def _amount_factor(amount: Decimal, saturation: float) -> float:
    return min(1.0, math.log10(1 + float(amount)) / math.log10(1 + saturation)) if amount > 0 else 0.0


def _band(score: float, bands: dict) -> str:
    for name in ("critical", "high", "medium"):
        if score >= bands[name]:
            return name
    return "low"


def _component(f: Finding, weight: float, contribution: float) -> dict:
    return {
        "signal_id": f.signal_id, "signal_name": f.detector, "signal_version": f.version, "source": f.source,
        "typology": f.typology, "score": f.score, "threshold": f.threshold, "strength": f.strength,
        "triggered": f.triggered, "weight": weight, "contribution": round(contribution, 4),
        "reason": f.details.get("reason"),
    }


def build_alert(rules: list[Finding], anomalies: list[Finding], supervised: list[Finding], policy: dict) -> dict:
    """One alert (AlertIn-shaped dict without priority_rank) from a cluster and its attached context signals."""
    weights = policy["typology_weights"]
    precedence = {t: i for i, t in enumerate(policy["typology_precedence"])}
    core = rules or anomalies  # standalone anomaly alerts have no rule findings

    named = min(core, key=lambda f: (precedence.get(f.typology, 99), -f.strength))
    typology = named.typology
    components, contributions = [], {}

    best_rule = max(core, key=lambda f: weights.get(f.typology, 0.5) * f.strength)
    base = policy["typology_scale"] * weights.get(best_rule.typology, 0.5) * best_rule.strength
    contributions["typology"] = base
    for f in core:
        w = weights.get(f.typology, 0.5)
        components.append(_component(f, w, base if f is best_rule else 0.0))

    distinct = {f.detector for f in rules}
    corroboration = min(policy["corroboration_cap"], policy["corroboration_bonus"] * max(0, len(distinct) - 1))
    contributions["corroboration"] = corroboration

    ctx_anomalies = [] if not rules else anomalies
    if ctx_anomalies:
        top = max(ctx_anomalies, key=lambda f: f.strength)
        contributions["anomaly"] = policy["anomaly_weight"] * top.strength
        for f in ctx_anomalies:
            components.append(_component(f, policy["anomaly_weight"], contributions["anomaly"] if f is top else 0.0))
    if supervised:
        candidates=[f for f in supervised if f.detector.startswith('ensemble_')] or [f for f in supervised if f.detector=='xgb_temporal']
        top = max(candidates, key=lambda f: f.score) if candidates else None
        contributions["supervised"] = policy["supervised_weight"] * top.score if top else 0.0
        for f in supervised:
            components.append(_component(f, policy["supervised_weight"],
                                         contributions["supervised"] if f is top else 0.0))

    amount = max((f.amount_usd for f in core), default=Decimal("0"))
    contributions["amount"] = policy["amount_weight"] * _amount_factor(amount, policy["amount_saturation_usd"])
    rank_score = round(min(1.0, sum(contributions.values())), 6)

    # Evidence roles: the named typology's evidence triggers; other rule evidence supports; model context last.
    txn_roles: dict[str, str] = {}
    rank = {"trigger": 0, "supporting": 1, "context": 2}
    for f in core:
        role = "trigger" if f.typology == typology else "supporting"
        for t in f.evidence:
            if t not in txn_roles or rank[role] < rank[txn_roles[t]]:
                txn_roles[t] = role
        ctx = f.details.get("context_transaction_id")
        if ctx and ctx not in txn_roles:
            txn_roles[ctx] = "context"
    for f in (ctx_anomalies + supervised):
        for t in f.evidence[:1]:
            txn_roles.setdefault(t, "context")

    account_roles: dict[str, str] = {}
    for f in core + ctx_anomalies:
        for a, r in f.accounts.items():
            if a not in account_roles or ROLE_RANK[r] < ROLE_RANK[account_roles[a]]:
                account_roles[a] = r
    primary_account = named.subject
    for a, r in list(account_roles.items()):
        if r == "subject" and a != primary_account:
            account_roles[a] = "intermediary"  # one subject per alert; other flagged accounts sit in the flow

    trigger_txns = [t for t in named.evidence if txn_roles.get(t) == "trigger"] or named.evidence
    if typology in policy["network_typologies"] and len(account_roles) >= 3:
        scope = "network"
    elif not rules and named.transaction_id:
        scope = "transaction"
    else:
        scope = "account"

    reasons = [f"{c['signal_name']}: {c['reason']}" for c in components if c["reason"] and c["source"] != "supervised_model"]
    breakdown = {k: round(v, 4) for k, v in contributions.items()}
    summary = (f"{typology.replace('_', ' ').capitalize()} lead from {len(distinct) or 1} detector(s)"
               f"{' with anomaly context' if ctx_anomalies else ''}. Priority {rank_score:.2f} under {policy['version']}: "
               + ", ".join(f"{k} {v:.2f}" for k, v in breakdown.items() if v) + ".")
    limitations = policy["limitations"]
    if supervised:
        limitations += " " + config.SUPERVISED["caveat"]
    return {
        "scope": scope,
        "primary_account_id": primary_account,
        "primary_transaction_id": trigger_txns[0] if trigger_txns else None,
        "typology": typology,
        "rank_score": rank_score,
        "ranking_strategy": f"{policy['version']} (weighted max typology + corroboration + anomaly + model + amount)",
        "component_signals": components,
        "window_start": min(f.window_start for f in core),
        "window_end": max(f.window_end for f in core),
        "total_amount": amount.quantize(Decimal("0.0001")),
        "amount_currency": "USD",
        "limitations": limitations,
        "explanation": {"summary": summary, "reasons": reasons, "score_breakdown": breakdown,
                        "policy_version": policy["version"], "formula": policy["formula"],
                        "detectors": sorted(distinct) or [named.detector]},
        "signal_ids": [f.signal_id for f in core + ctx_anomalies + supervised if f.signal_id],
        "transactions": [{"transaction_id": t, "role": r} for t, r in txn_roles.items()],
        "accounts": [{"account_id": a, "role": r} for a, r in account_roles.items()],
    }


def fuse(rules: list[Finding], anomalies: list[Finding], supervised: list[Finding], policy: dict | None = None) -> list[dict]:
    policy = policy or config.POLICY
    window = timedelta(days=policy["cluster_window_days"])
    fired = [f for f in rules if f.triggered]
    clusters = cluster(fired, window)

    attached: set[int] = set()
    sup_by_txn = defaultdict(list)
    for f in supervised: sup_by_txn[f.transaction_id].append(f)
    alerts = []
    for group in clusters:
        evidence = {t for f in group for t in f.evidence}
        flow_accounts = {a for f in group for a, r in f.accounts.items() if r in ("subject", "intermediary")}
        start, end = min(f.window_start for f in group) - window, max(f.window_end for f in group) + window
        ctx = []
        for i, a in enumerate(anomalies):
            if a.transaction_id in evidence or (a.subject in flow_accounts and start <= a.window_start <= end):
                ctx.append(a)
                attached.add(i)
        sup = [f for t in sorted(evidence) for f in sup_by_txn.get(t,[])]
        alerts.append(build_alert(group, ctx, sup, policy))

    for i, a in enumerate(anomalies):
        if i not in attached and a.triggered and a.strength >= policy["standalone_anomaly_min_strength"]:
            alerts.append(build_alert([], [a], sup_by_txn.get(a.transaction_id,[]), policy))
    if policy.get("standalone_supervised"):
        covered = {t["transaction_id"] for al in alerts for t in al["transactions"]}
        for s in supervised:
            if s.triggered and s.transaction_id not in covered:
                alerts.append(build_alert([], [s], [], policy))

    alerts.sort(key=lambda a: (-a["rank_score"], a["window_start"]))
    alerts = alerts[: config.LIMITS["max_alerts"]]
    for rank, a in enumerate(alerts, start=1):
        a["priority_rank"] = rank
        a["priority_band"] = _band(a["rank_score"], policy["bands"])
    return alerts
