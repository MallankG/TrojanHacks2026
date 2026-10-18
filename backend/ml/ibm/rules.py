"""PRD typology-rule baseline on HI-Small, in two transparent variants.

  rules             hand-set thresholds, score = number of rules fired (ablation)
  rules_calibrated  each rule's threshold picked on the TRAIN period (best single-rule F1 over a quantile
                    grid); score = sum of fired rules weighted by their train precision. Val is untouched
                    until the operating threshold is chosen, as for every other model.
Every rule is "signal >= threshold" on a time-respecting feature, so each alert is explainable.
cycle_3 is retired: its train-graph count let train rows see later train edges; reciprocal (strictly past) remains.

    python -m ml.ibm.rules --cache ml/artifacts/ibm/cache --out ml/artifacts/ibm
"""
import numpy as np
import pandas as pd

from .common import cli, evaluate_and_save, load_cache

# rule -> (signal where larger = more suspicious, hand-set threshold)
SIGNALS = {
    "fan_in": (lambda f: f.d_in_cp, 10),
    "fan_out": (lambda f: f.o_out_cp, 10),
    "dst_inflow_burst_24h": (lambda f: f.d_in_cnt_24h, 5),
    "src_outflow_burst_1h": (lambda f: f.o_out_cnt_1h, 5),
    "pass_through": (lambda f: -np.abs(np.log(f.pass_through_24h.clip(1e-6))), np.log(1.2) * -1),
    "reciprocal": (lambda f: f.pair_reciprocal, 1),
    # a first-ever event has no history (secs_since_last is filled), so it is new, not dormant
    "dormant_then_large": (lambda f: np.minimum(f.o_secs_since_last / 86400, f.amt_vs_o_mean_7d).where(f.o_n_events_to_date > 0, 0), 3),
}
QUANTILES = (0.5, 0.75, 0.9, 0.95, 0.98, 0.99, 0.995, 0.999)


def calibrate(sig, y):
    """Threshold candidates are quantiles of the values ABOVE the signal's minimum: zero-inflated signals
    (e.g. reciprocal) would otherwise calibrate to thr = min and fire on every edge."""
    best = (-1.0, None, 0.0)
    pool = sig[sig > sig.min()]
    if len(pool) == 0:
        return {"threshold": float("inf"), "train_f1": 0.0, "train_precision": 0.0}
    for q in (0.0,) + QUANTILES:
        thr = float(np.quantile(pool, q))
        fired = sig >= thr
        tp = (fired & (y == 1)).sum()
        f1 = 2 * tp / max(fired.sum() + (y == 1).sum(), 1)
        if f1 > best[0]:
            best = (f1, thr, tp / max(fired.sum(), 1))
    return {"threshold": best[1], "train_f1": float(best[0]), "train_precision": float(best[2])}


def hits(fired, y, te):
    return {k: {"fired_test": int(v[te].sum()), "precision_test": float(y[te][v[te] == 1].mean()) if v[te].sum() else 0.0,
                "recall_test": float(v[te][y[te] == 1].mean())} for k, v in fired.items()}


if __name__ == "__main__":
    a = cli(__doc__)
    f, y, (tr, va, te), amt = load_cache(a.cache, ["temporal"])
    sig = {k: s(f).to_numpy(float) for k, (s, _) in SIGNALS.items()}
    tie = 1e-3 * np.log1p(amt) / np.log1p(amt).max()  # amount breaks ties, never outranks a rule

    fired = {k: (sig[k] >= thr).astype(int) for k, (_, thr) in SIGNALS.items()}
    evaluate_and_save("rules", y, sum(fired.values()) + tie, va, te, a.out, {"rule_hits": hits(fired, y, te)})

    cal = {k: calibrate(sig[k][tr], y[tr]) for k in SIGNALS}
    fired = {k: (sig[k] >= c["threshold"]).astype(int) for k, c in cal.items()}
    score = sum(cal[k]["train_precision"] * fired[k] for k in SIGNALS) + tie
    evaluate_and_save("rules_calibrated", y, score, va, te, a.out, {"calibration": cal, "rule_hits": hits(fired, y, te)})
    for k, c in cal.items():
        print(f"  {k:22s} thr {c['threshold']:.4g}  train F1 {100 * c['train_f1']:.2f}  train precision {100 * c['train_precision']:.2f}")
