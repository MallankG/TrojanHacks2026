"""Optional XGBoost transaction-risk signal from the ML teammate's artifacts (no torch, no GNN).

Uses `ml/tracelayer/features.py` (numpy/pandas only) to build the frozen fs-1.0 feature schema and the trained booster
in `ml/artifacts/tracelayer/xgboost.json`. If anything is missing or fails to load, the run continues without it and
records why. The ML code is read, never modified.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from app.services.detection import config
from app.services.detection.detectors import Finding, _roles
from app.services.detection.ledger import Ledger

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[3]


@dataclass
class SupervisedResult:
    findings: list[Finding]
    model_card: dict
    skipped_reason: str | None = None
    scored: int = 0


def _load(artifact_dir: Path):
    import xgboost as xgb  # noqa: PLC0415 - optional dependency

    from ml.tracelayer import features as ml_features  # noqa: PLC0415 - teammate code, imported only when available

    card = json.loads((artifact_dir / "model_card.json").read_text(encoding="utf-8"))
    booster = xgb.Booster()
    booster.load_model(str(artifact_dir / "xgboost.json"))
    return xgb, ml_features, card, booster


def synthetic_score(ledger: Ledger, keep_ids: set[str], cfg: dict | None = None) -> SupervisedResult:
    """Score every transaction; return signals for those at or above the review threshold, plus keep_ids."""
    cfg = cfg or config.SUPERVISED
    artifact_dir = BACKEND_DIR / "ml/artifacts/tracelayer"
    try:
        import numpy as np  # noqa: PLC0415
        import pandas as pd  # noqa: PLC0415

        xgb, ml_features, card, booster = _load(artifact_dir)
    except Exception as exc:  # noqa: BLE001 - optional signal: never break the run
        logger.info("Supervised signal skipped: %s", exc)
        return SupervisedResult([], {}, f"Model artifacts unavailable: {type(exc).__name__}: {exc}"[:500])
    if not ledger.txns:
        return SupervisedResult([], card, "No transactions to score")
    try:
        tx = pd.DataFrame({
            "transaction_id": [t.id for t in ledger.txns],
            "timestamp": pd.to_datetime([t.at for t in ledger.txns], utc=True),
            "amount": [float(t.usd) for t in ledger.txns],
            "channel": [t.channel for t in ledger.txns],
            "origin_account_id": [t.origin for t in ledger.txns],
            "destination_account_id": [t.destination for t in ledger.txns],
        })
        accounts = sorted(ledger.accounts())
        acc = pd.DataFrame({
            "account_id": accounts,
            "account_type": ["unknown"] * len(accounts),  # not in the canonical schema
            "open_date": pd.to_datetime([ledger.first_seen(a) for a in accounts], utc=True),
        })
        feats = ml_features.build_features(tx, acc)
        cols = card["feature_columns"]
        dm = xgb.DMatrix(feats[cols].to_numpy(np.float32), feature_names=cols)
        prob = booster.predict(dm)
        contrib = booster.predict(dm, pred_contribs=True)[:, :-1]
    except Exception as exc:  # noqa: BLE001
        logger.exception("Supervised scoring failed")
        return SupervisedResult([], card, f"Scoring failed: {type(exc).__name__}: {exc}"[:500])

    thresholds = card.get("thresholds", {}).get("xgboost", {})
    operating = float(thresholds.get("operating", 0.5))
    review = min(float(thresholds.get("review_recall90", operating)), operating)
    findings = []
    order = np.argsort(-prob, kind="stable")
    for idx in order:
        t = ledger.txns[int(idx)]
        p = float(prob[idx])
        if p < review and t.id not in keep_ids:
            continue
        top = np.argsort(-contrib[idx])[:5]
        findings.append(Finding(
            detector=cfg["signal_name"], typology="model_anomaly", score=round(p, 6), threshold=round(operating, 6),
            strength=round(p, 4), evidence=[t.id], accounts=_roles(t.origin, counterparties=[t.destination]),
            window_start=t.at, window_end=t.at, amount_usd=t.usd, triggered=p >= operating,
            details={
                "reason": f"Model score {p:.3f} against operating threshold {operating:.3f}",
                "model_version": card.get("model_version"), "feature_version": card.get("feature_version"),
                "review_threshold": round(review, 6),
                "top_features": [{"feature": cols[j], "contribution": round(float(contrib[idx, j]), 4)}
                                 for j in top if contrib[idx, j] > 0],
                "caveat": cfg["caveat"],
            },
            subject=t.origin, transaction_id=t.id, source="supervised_model", version=card.get("model_version", "unknown"),
        ))
    # Bound the write: keep every transaction other signals need, then the highest scores.
    needed = [f for f in findings if f.transaction_id in keep_ids]
    rest = [f for f in findings if f.transaction_id not in keep_ids][: max(0, cfg["max_signals"] - len(needed))]
    return SupervisedResult(needed + rest, card, None, scored=len(ledger.txns))


def score(ledger: Ledger, keep_ids: set[str], cfg: dict | None = None) -> SupervisedResult:
    from .benchmarks import score as benchmark_score
    result=benchmark_score(ledger,keep_ids,names=('xgb_temporal',))['xgb_temporal']
    return SupervisedResult(result.findings,result.card,result.skipped_reason,result.scored)
