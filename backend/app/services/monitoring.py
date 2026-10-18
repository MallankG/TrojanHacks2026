"""Bounded batch monitoring; input drift is a proxy, performance requires reviewed labels."""
from bisect import bisect_right
from collections import Counter
from datetime import UTC, datetime
import math
from typing import Literal

from pydantic import Field, model_validator

from app.services.governance_superpowers import StrictModel


class Observation(StrictModel):
    features: dict[str, float | str | None] = Field(max_length=30)
    score: float = Field(ge=0, le=1)
    label: Literal[0, 1] | None = None

    @model_validator(mode="after")
    def bounded(self):
        if any(len(k) > 100 or (isinstance(v, str) and len(v) > 100) for k, v in self.features.items()):
            raise ValueError("Feature names and categorical values are limited to 100 characters")
        return self


class DriftCheck(StrictModel):
    reference_snapshot: str = Field(min_length=1, max_length=120)
    current_snapshot: str = Field(min_length=1, max_length=120)
    model_version: str = Field(min_length=1, max_length=120)
    feature_version: str = Field(min_length=1, max_length=120)
    reference: list[Observation] = Field(min_length=1, max_length=1000)
    current: list[Observation] = Field(min_length=1, max_length=1000)
    feature_types: dict[str, Literal["numeric", "categorical"]] = Field(min_length=1, max_length=30)
    score_kind: Literal["ranking", "calibrated_probability"] = "ranking"
    threshold: float = Field(default=.62, ge=0, le=1)
    top_k: int = Field(default=20, ge=1, le=1000)
    minimum_samples: int = Field(default=30, ge=10, le=1000)

    @model_validator(mode="after")
    def types(self):
        for row in self.reference + self.current:
            for name, kind in self.feature_types.items():
                value = row.features.get(name)
                if value is not None and ((kind == "numeric" and not isinstance(value, (int, float))) or (kind == "categorical" and not isinstance(value, str))):
                    raise ValueError("Feature values must match feature_types")
        return self


def psi(reference: list[float], current: list[float]) -> float | None:
    if not reference or not current:
        return None
    ordered = sorted(reference)
    # Bins are pinned to the reference; current values never move the boundaries.
    cuts = sorted(set([ordered[0], math.nextafter(ordered[-1], math.inf)] + [ordered[min(len(ordered) - 1, len(ordered) * i // 10)] for i in range(1, 10)]))
    r = Counter(bisect_right(cuts, v) for v in reference)
    c = Counter(bisect_right(cuts, v) for v in current)
    n = len(cuts) + 1
    smoothing = .5
    return sum(((c[i] + smoothing) / (len(current) + n * smoothing) - (r[i] + smoothing) / (len(reference) + n * smoothing))
               * math.log(((c[i] + smoothing) / (len(current) + n * smoothing)) / ((r[i] + smoothing) / (len(reference) + n * smoothing))) for i in range(n))


def performance(rows: list[Observation], request: DriftCheck) -> dict:
    labeled = [r for r in rows if r.label is not None]
    base = {"labeled": len(labeled), "total": len(rows), "coverage": len(labeled) / len(rows), "status": "insufficient_labels"}
    if len(labeled) < request.minimum_samples or {r.label for r in labeled} != {0, 1}:
        return {**base, "average_precision": None, "precision_at_k": None, "recall_at_k": None, "brier": None}
    from sklearn.metrics import average_precision_score
    ranked = sorted(labeled, key=lambda r: r.score, reverse=True)
    k = min(request.top_k, len(ranked))
    positives = sum(r.label for r in ranked)
    hits = sum(r.label for r in ranked[:k])
    return {**base, "status": "measured_on_labeled_subset", "average_precision": float(average_precision_score([r.label for r in labeled], [r.score for r in labeled])),
            "precision_at_k": hits / k, "recall_at_k": hits / positives, "k": k,
            "positive_rate": positives / len(labeled),
            "brier": sum((r.score - r.label) ** 2 for r in labeled) / len(labeled) if request.score_kind == "calibrated_probability" else None}


def drift_report(request: DriftCheck) -> dict:
    features = []
    for name, kind in request.feature_types.items():
        reference = [r.features.get(name) for r in request.reference]
        current = [r.features.get(name) for r in request.current]
        r, c = [v for v in reference if v is not None], [v for v in current if v is not None]
        missing_delta = sum(v is None for v in current) / len(current) - sum(v is None for v in reference) / len(reference)
        enough = min(len(r), len(c)) >= request.minimum_samples
        if kind == "numeric":
            statistic = psi(r, c) if enough else None
            method = "PSI (reference deciles/support; 0.5 pseudocount)"
            watch, alert = .10, .25
        else:
            rc, cc = Counter(r), Counter(c)
            statistic = .5 * sum(abs(rc[v] / len(r) - cc[v] / len(c)) for v in rc.keys() | cc.keys()) if enough else None
            method = "Total variation distance"
            watch, alert = .10, .20
        status = "insufficient_data" if statistic is None else "drift" if statistic >= alert else "watch" if statistic >= watch else "stable"
        if missing_delta >= .05:
            status = "quality_issue"
        features.append({"feature": name, "kind": kind, "method": method, "value": statistic, "status": status,
                         "reference_count": len(r), "current_count": len(c), "missing_rate_delta": missing_delta,
                         "unseen_category_rate": sum(v not in set(r) for v in c) / len(c) if kind == "categorical" and c else None,
                         "watch_threshold": watch, "drift_threshold": alert})
    score_psi = psi([r.score for r in request.reference], [r.score for r in request.current]) if min(len(request.reference), len(request.current)) >= request.minimum_samples else None
    baseline, current = performance(request.reference, request), performance(request.current, request)
    delta = current["average_precision"] - baseline["average_precision"] if current["average_precision"] is not None and baseline["average_precision"] is not None else None
    return {"as_of": datetime.now(UTC).isoformat(), "reference_snapshot": request.reference_snapshot, "current_snapshot": request.current_snapshot,
            "model_version": request.model_version, "feature_version": request.feature_version, "features": features,
            "prediction": {"psi": score_psi, "reference_alert_rate": sum(r.score >= request.threshold for r in request.reference) / len(request.reference),
                           "current_alert_rate": sum(r.score >= request.threshold for r in request.current) / len(request.current), "threshold": request.threshold},
            "performance": {"reference": baseline, "current": current, "average_precision_delta": delta,
                            "status": "unknown_without_labels" if delta is None else "review_degradation" if delta < -.05 else "measured"},
            "caveats": ["Input/score drift does not prove model degradation or concept drift.", "Thresholds are prototype review settings; calibrate to seasonality, sample size and review capacity.",
                        "Performance is measured only on labeled rows; investigate label delay, selection bias and prevalence before comparing.", "Ranking scores are not calibrated probabilities; Brier score is omitted unless explicitly declared calibrated.",
                        "Caller must pin the same model, features and mature outcome definitions for both windows. No automatic retraining or promotion."],
            "evidence_refs": [request.reference_snapshot, request.current_snapshot]}


SLO_POLICIES = {
    "ingestion_delay": {"max_ms": 1800000, "success_target": .99, "fallback": "Hold late batches; display the last accepted watermark."},
    "score_latency": {"max_ms": 2000, "success_target": .99, "fallback": "Keep the prior completed score run; mark it stale."},
    "graph_query": {"max_ms": 2000, "success_target": .99, "fallback": "Offer the pinned graph snapshot and transaction timeline."},
    "copilot_verification": {"max_ms": 5000, "success_target": .99, "fallback": "Block unsupported claims; allow manual review."},
    "policy_retrieval": {"max_ms": 3000, "success_target": .99, "fallback": "Show retrieval unavailable; never invent citations."},
    "evidence_export": {"max_ms": 5000, "success_target": .99, "fallback": "Keep evidence in the case; retry export explicitly."},
}


class ServiceMetric(StrictModel):
    operation: Literal["ingestion_delay", "score_latency", "graph_query", "copilot_verification", "policy_retrieval", "evidence_export"]
    duration_ms: float = Field(ge=0, le=604800000)
    success: bool
    source_ref: str = Field(min_length=1, max_length=160)
    observed_at: datetime

    @model_validator(mode="after")
    def clock(self):
        if self.observed_at.tzinfo is None or self.observed_at > datetime.now(UTC):
            raise ValueError("observed_at must be timezone-aware and not in the future")
        return self


def slo_report(samples: list[dict], now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(UTC)
    result = []
    for operation, policy in SLO_POLICIES.items():
        rows = [s for s in samples if s["operation"] == operation]
        values = sorted(float(s["duration_ms"]) for s in rows)
        good = sum(s["success"] and s["duration_ms"] <= policy["max_ms"] for s in rows)
        last_time = max((datetime.fromisoformat(s["observed_at"]) for s in rows), default=None)
        last = last_time.isoformat() if last_time else None
        stale = bool(last_time and (now - last_time).total_seconds() > 3600)
        ratio = good / len(rows) if rows else None
        status = "unknown" if len(rows) < 10 else "stale" if stale else "breach" if ratio < policy["success_target"] else "within_target"
        result.append({"operation": operation, **policy, "samples": len(rows), "good_ratio": ratio,
                       "p95_ms": values[max(0, math.ceil(.95 * len(values)) - 1)] if values else None,
                       "last_observed": last, "status": status})
    return result
