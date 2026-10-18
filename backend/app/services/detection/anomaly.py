"""Statistical anomaly signal: each account against its own history (robust z-score, history only).

For every transaction, the account's earlier transactions in the same direction form the baseline. The modified
z-score of log(amount) uses the median and the median absolute deviation (MAD) of that baseline. A ranking signal,
not a probability of anything.
"""

import math
from statistics import median

from app.services.detection import config
from app.services.detection.detectors import Finding, _roles
from app.services.detection.ledger import Ledger

MAD_FLOOR = 0.05  # log-scale floor so a perfectly regular history does not divide by zero


def robust_z(value: float, history: list[float]) -> tuple[float, float, float]:
    """(modified z, median, MAD) of value against history, all on the given scale."""
    med = median(history)
    mad = median(abs(h - med) for h in history)
    return 0.6745 * (value - med) / max(mad, MAD_FLOOR), med, mad


def amount_deviation(ledger: Ledger, cfg: dict | None = None) -> list[Finding]:
    cfg = cfg or config.ANOMALY
    findings: list[Finding] = []
    for account in sorted(ledger.accounts()):
        best = None
        flagged = []
        for direction, txns in (("outbound", ledger.outflows.get(account, [])), ("inbound", ledger.inflows.get(account, []))):
            for i, t in enumerate(txns):
                prior = [p for p in txns[:i] if p.at < t.at]  # strictly earlier: same-timestamp events are not history
                if len(prior) < cfg["min_history"] or float(t.usd) < cfg["min_amount_usd"]:
                    continue
                prior_max = max(float(p.usd) for p in prior)
                if float(t.usd) < cfg["min_prior_max_multiple"] * prior_max:
                    continue
                z, med, mad = robust_z(math.log1p(float(t.usd)), [math.log1p(float(p.usd)) for p in prior])
                if z < cfg["z_threshold"]:
                    continue
                item = {"transaction_id": t.id, "direction": direction, "amount_usd": format(t.usd, "f"), "z": round(z, 3),
                        "baseline_median_usd": round(math.expm1(med), 2), "baseline_prior_max_usd": round(prior_max, 2),
                        "baseline_size": len(prior), "mad_log": round(mad, 4), "txn": t}
                flagged.append(item)
                if best is None or z > best["z"]:
                    best = item
        if not best:
            continue
        t = best["txn"]
        counterpart = t.destination if best["direction"] == "outbound" else t.origin
        reason = (f"{best['direction'].capitalize()} {best['amount_usd']} USD is {best['z']:.1f} robust standard deviations "
                  f"above this account's own median of {best['baseline_median_usd']} USD over {best['baseline_size']} "
                  f"earlier {best['direction']} transactions, and {float(t.usd) / best['baseline_prior_max_usd']:.1f}x "
                  f"its previous maximum")
        params = {k: v for k, v in cfg.items() if k not in ("description", "signal_name", "typology")}
        findings.append(Finding(
            detector=cfg["signal_name"], typology=cfg["typology"], score=best["z"], threshold=float(cfg["z_threshold"]),
            strength=config.strength(best["z"], cfg["z_threshold"], cfg["saturation"]),
            evidence=[f["transaction_id"] for f in sorted(flagged, key=lambda f: -f["z"])],
            accounts=_roles(account, counterparties=[counterpart]), window_start=t.at, window_end=t.at, amount_usd=t.usd,
            details={"reason": reason, "description": cfg["description"], "parameters": params,
                     "flagged_transactions": [{k: v for k, v in f.items() if k != "txn"} for f in flagged[:20]],
                     "note": "Ranking signal against the account's own history; not a probability of wrongdoing."},
            subject=account, transaction_id=t.id, source="anomaly_model", version=config.ANOMALY_VERSION,
        ))
    return findings
