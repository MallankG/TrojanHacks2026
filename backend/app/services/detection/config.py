"""Versioned, documented configuration for the detection run (typology rules, anomaly baseline, alert fusion).

Every parameter here is a review setting, not a legal threshold. In particular the structuring band is a configurable
review band chosen for the synthetic demo; it does not encode any reporting requirement. Each run stores the exact
configuration it used in `model_runs.config`, so a run can be reproduced and compared with later settings.
"""

from copy import deepcopy

ENGINE_VERSION = "detection-engine-1.0.0"
RULES_VERSION = "typology-rules-1.0.0"
ANOMALY_VERSION = "robust-baseline-1.0.0"
POLICY_VERSION = "priority-policy-1.0.0"

# Server-side bounds for one run (the request may lower them, never raise them).
LIMITS = {
    "max_transactions": 20_000,   # most recent transactions in scope
    "max_window_days": 400,       # look-back from the latest transaction in scope
    "max_signals_per_detector": 300,
    "max_alerts": 500,
    "dfs_expansion_budget": 50_000,  # per path/cycle search
}

DEFAULT_WINDOW_DAYS = 180

# Each detector reports a raw `score` against a `threshold` (the values in risk_signals) and a normalized
# `strength` in [0.5, 1] once it fires:  strength = 0.5 + 0.5 * clamp((score/threshold - 1) / (saturation - 1), 0, 1).
# So a detector exactly at its threshold has strength 0.5, and one at `saturation` x threshold has strength 1.
DETECTORS = {
    "rapid_pass_through": {
        "typology": "rapid_in_out",
        "description": "An inflow is forwarded out of the same account within a short hold window.",
        "min_inflow_usd": 5000,
        "max_hold_hours": 48,
        "min_forward_ratio": 0.8,
        "max_forward_ratio": 1.25,
        "score": "number of pass-through episodes", "threshold": 1, "saturation": 3,
    },
    "funnel_fan_in": {
        "typology": "funnel",
        "description": "Many distinct senders into one account in a short window, well above the account's own baseline, "
                       "with most of the money leaving to few destinations.",
        "window_days": 7,
        "min_senders": 6,
        "min_inflow_usd": 10000,
        "baseline_multiple": 3,
        "min_outflow_ratio": 0.7,
        "outflow_grace_hours": 72,
        "top_destinations": 2,
        "min_destination_concentration": 0.8,
        "score": "distinct senders in window", "threshold": 6, "saturation": 2,
    },
    "fan_out_dispersal": {
        "typology": "fan_out",
        "description": "One account pays many mostly never-before-paid recipients within a day, funded by a recent inflow.",
        "window_hours": 24,
        "min_recipients": 5,
        "min_new_recipient_share": 0.6,
        "min_total_usd": 10000,
        "funding_lookback_hours": 72,
        "min_funded_ratio": 0.7,
        "score": "distinct recipients in window", "threshold": 5, "saturation": 2,
    },
    "bounded_cycle": {
        "typology": "cycle",
        "description": "Money returns to its starting account through 3-6 time-ordered hops with amount continuity.",
        "min_hops": 3,
        "max_hops": 6,
        "max_gap_hours": 72,
        "max_cycle_days": 14,
        "min_amount_usd": 5000,
        "min_amount_ratio": 0.7,
        "score": "repetitions of the same account cycle", "threshold": 1, "saturation": 3,
    },
    "layering_chain": {
        "typology": "layering",
        "description": "A multi-hop, time-respecting chain of transfers where each hop keeps most of the previous amount.",
        "min_hops": 4,
        "max_hops": 6,
        "max_gap_hours": 48,
        "min_start_usd": 10000,
        "min_amount_ratio": 0.85,
        "path_limit": 2000,
        "score": "hops in the chain", "threshold": 4, "saturation": 1.5,
    },
    "structuring_band": {
        "typology": "structuring",
        "description": "Repeated cash deposits inside a configurable review band just below a round amount, "
                       "with no deposits above it in the same window.",
        "band_low_usd": 8000,
        "band_high_usd": 10000,
        "window_days": 7,
        "min_deposits": 3,
        "min_in_band_share": 0.75,
        "score": "in-band cash deposits in window", "threshold": 3, "saturation": 2,
    },
    "dormant_reactivation": {
        "typology": "dormant_reactivation",
        "description": "An account silent for a long gap suddenly moves much more than its pre-dormancy activity.",
        "min_gap_days": 30,
        "min_prior_events": 2,
        "reactivation_days": 14,
        "min_reactivation_usd": 10000,
        "min_volume_multiple": 3,
        "score": "dormant gap in days", "threshold": 30, "saturation": 2,
    },
    "new_beneficiary_large_payment": {
        "typology": "scam_proceeds",
        "description": "An established account sends an unusually large payment to a never-before-paid beneficiary, "
                       "which forwards most of it on quickly.",
        "min_amount_usd": 5000,
        "min_prior_outflows": 5,
        "min_median_multiple": 5,
        "max_beneficiary_age_days": 30,      # beneficiary first observed at most this long before the payment
        "max_payer_recent_inflow_ratio": 0.5,  # payer is not just forwarding an inflow it received in forward_hours
        "forward_hours": 72,
        "min_forward_ratio": 0.7,
        "score": "payment / payer's median prior outflow", "threshold": 5, "saturation": 4,
    },
}

ANOMALY = {
    "signal_name": "robust_amount_deviation",
    "typology": "behavioral_deviation",
    "description": "Robust z-score of a transaction's log-amount against the same account's own prior transactions in "
                   "the same direction (median and MAD, history only), required to also exceed the prior maximum.",
    "min_history": 10,
    "z_threshold": 3.5,       # Iglewicz-Hoaglin modified z-score convention
    "min_prior_max_multiple": 2,
    "min_amount_usd": 1000,
    "saturation": 2,
}

SUPERVISED = {
    "signal_name": "xgboost_transaction_risk",
    "artifact_dir": "ml/artifacts/tracelayer",
    "max_signals": 300,
    "caveat": "The canonical schema has no account type or open date. The model's account-type features are all zero and "
              "account age is approximated from first observation, so these inputs are outside the model's training "
              "distribution. Treat the score as weak context only.",
}

# Alert fusion (ranker). Weights are explicit and versioned; nothing else contributes to rank_score.
POLICY = {
    "version": POLICY_VERSION,
    "formula": "rank_score = min(1, typology_scale * max_rule(typology_weight * strength) + corroboration_bonus * "
               "(distinct rule detectors - 1, capped at corroboration_cap) + anomaly_weight * max anomaly strength + "
               "supervised_weight * max model score + amount_weight * amount_factor); amount_factor = "
               "min(1, log10(1 + amount_usd) / log10(1 + amount_saturation_usd))",
    "typology_scale": 0.6,
    "typology_weights": {
        "layering": 1.0, "cycle": 0.95, "scam_proceeds": 0.95, "structuring": 0.85, "funnel": 0.85, "fan_out": 0.85,
        "dormant_reactivation": 0.8, "rapid_in_out": 0.75, "behavioral_deviation": 0.5, "model_anomaly": 0.5,
    },
    # Alert typology is named by the most specific explanation present, independent of which signal scored highest.
    "typology_precedence": [
        "cycle", "layering", "scam_proceeds", "structuring", "dormant_reactivation", "funnel", "fan_out", "rapid_in_out",
        "behavioral_deviation", "model_anomaly",
    ],
    "network_typologies": ["layering", "cycle", "funnel", "fan_out"],
    "corroboration_bonus": 0.06,
    "corroboration_cap": 0.18,
    "anomaly_weight": 0.12,
    "supervised_weight": 0.04,
    "amount_weight": 0.06,
    "amount_saturation_usd": 250000,
    "bands": {"critical": 0.75, "high": 0.6, "medium": 0.45},
    "standalone_anomaly_min_strength": 0.75,
    "standalone_supervised": False,
    "cluster_window_days": 7,
    "limitations": "Signals are leads for human review, not findings of wrongdoing. Paths and cycles are inferred from "
                   "timing and amounts and do not prove that the same funds moved through commingled balances. "
                   "Only transactions loaded into TraceLayer are visible; activity elsewhere may explain the pattern.",
}


def snapshot() -> dict:
    """The full configuration as stored on each run."""
    return deepcopy({"engine_version": ENGINE_VERSION, "limits": LIMITS, "detectors": DETECTORS, "anomaly": ANOMALY,
                     "supervised": SUPERVISED, "policy": POLICY})


def strength(score: float, threshold: float, saturation: float) -> float:
    if threshold <= 0 or saturation <= 1:
        return 1.0
    excess = (score / threshold - 1) / (saturation - 1)
    return round(0.5 + 0.5 * min(1.0, max(0.0, excess)), 4)
