"""One detection run: load the tenant's transactions, run typology rules, the robust anomaly baseline and
(when available) the XGBoost scorer, write each as a model run with its signals, then fuse the signals into
ranked alerts under a ranker run. Scores are review leads, never findings of wrongdoing."""

from datetime import UTC, datetime, timedelta

from app.core import audit
from app.core.db import get_db, insert_many, to_json, with_actor
from app.core.security import CurrentUser
from app.schemas import AlertBatch
from app.services import alerts as alert_service

from . import anomaly, config, supervised
from .detectors import Finding, run_rules
from .fusion import fuse
from .ledger import Ledger, Txn

TXN_FIELDS = "id, transaction_ref, occurred_at, amount, currency, amount_usd, channel, origin_account_id, destination_account_id"
PAGE = 1000


def load_ledger(tenant_id: str, window_days: int, max_transactions: int) -> Ledger:
    """The most recent transactions (bounded), then everything inside the look-back window from the latest one."""
    db = get_db()
    rows: list[dict] = []
    while len(rows) < max_transactions:
        batch = (db.table("transactions").select(TXN_FIELDS).eq("tenant_id", tenant_id)
                 .order("occurred_at", desc=True).order("id")
                 .range(len(rows), min(len(rows) + PAGE, max_transactions) - 1).execute().data)
        rows += batch
        if len(batch) < PAGE:
            break
    txns = [Txn.from_row(r) for r in rows]
    if txns:
        cutoff = max(t.at for t in txns) - timedelta(days=window_days)
        txns = [t for t in txns if t.at >= cutoff]
    return Ledger(txns)


def _start_run(user: CurrentUser, name: str, kind: str, version: str, dataset: str, cfg: dict) -> dict:
    row = {"tenant_id": user.tenant_id, "model_name": name, "model_kind": kind, "model_version": version,
           "dataset_version": dataset, "code_version": config.ENGINE_VERSION, "config": cfg, "status": "running",
           "triggered_by": user.id}
    return with_actor(get_db().table("model_runs").insert(to_json(row)), user).execute().data[0]


def _finish_run(user: CurrentUser, run_id: str, status: str, metrics: dict, error: str | None = None) -> None:
    changes = {"status": status, "metrics": metrics, "metrics_are_demonstration": True,
               "completed_at": datetime.now(UTC).isoformat(), "error_message": error}
    with_actor(get_db().table("model_runs").update(to_json(changes)).eq("tenant_id", user.tenant_id).eq("id", run_id), user).execute()


def _write_signals(user: CurrentUser, run_id: str, findings: list[Finding]) -> None:
    rows = [{
        "tenant_id": user.tenant_id, "model_run_id": run_id, "source": f.source, "signal_name": f.detector,
        "signal_version": f.version, "typology": f.typology, "transaction_id": f.transaction_id,
        "account_id": f.subject, "score": f.score, "threshold": f.threshold, "triggered": f.triggered,
        "window_start": f.window_start, "window_end": f.window_end,
        "details": {**f.details, "strength": round(f.strength, 4), "accounts": f.accounts, "amount_usd": str(f.amount_usd)},
        "evidence_transaction_ids": f.evidence,
    } for f in findings]
    for f, row in zip(findings, insert_many("risk_signals", to_json(rows), actor=user), strict=True):
        f.signal_id = row["id"]


def run_detection(user: CurrentUser, window_days: int | None = None, max_transactions: int | None = None) -> dict:
    limits = config.LIMITS
    window = min(window_days or config.DEFAULT_WINDOW_DAYS, limits["max_window_days"])
    cap = min(max_transactions or limits["max_transactions"], limits["max_transactions"])
    ledger = load_ledger(user.tenant_id, window, cap)
    dataset = (f"tenant-txns:{len(ledger.txns)}:{ledger.start.isoformat() if ledger.start else 'none'}"
               f"..{ledger.end.isoformat() if ledger.end else 'none'}")
    runs: dict[str, str] = {}
    current: str | None = None
    try:
        current = runs["rule_engine"] = _start_run(user, "typology-rules", "rule_engine", config.RULES_VERSION, dataset,
                                                   {"detectors": config.DETECTORS, "limits": limits})["id"]
        rules = run_rules(ledger)
        _write_signals(user, current, rules)
        _finish_run(user, current, "completed", {"signals": len(rules), "transactions": len(ledger.txns)})

        current = runs["anomaly"] = _start_run(user, "robust-amount-baseline", "anomaly", config.ANOMALY_VERSION, dataset,
                                               config.ANOMALY)["id"]
        anomalies = anomaly.amount_deviation(ledger)
        _write_signals(user, current, anomalies)
        _finish_run(user, current, "completed", {"signals": len(anomalies)})
        current = None

        sup = supervised.score(ledger, {t for f in rules for t in f.evidence})
        if sup.skipped_reason is None:
            current = runs["supervised"] = _start_run(user, sup.model_card.get("model_name", "xgboost"), "supervised",
                                                      str(sup.model_card.get("model_version", "unknown")), dataset,
                                                      {"artifact": config.SUPERVISED["artifact_dir"]})["id"]
            _write_signals(user, current, sup.findings)
            _finish_run(user, current, "completed", {"signals": len(sup.findings), "scored": sup.scored})
            current = None

        current = runs["ranker"] = _start_run(user, "alert-fusion", "ranker", config.POLICY_VERSION, dataset, config.snapshot())["id"]
        fused = fuse(rules, anomalies, sup.findings)
        batch = AlertBatch.model_validate({"model_run_id": current, "alerts": fused})
        alert_service.validate_batch(user, batch)
        created = alert_service.publish(user, batch) if batch.alerts else []
        _finish_run(user, current, "completed", {"alerts": len(created)})
        current = None
    except Exception as exc:
        if current:
            _finish_run(user, current, "failed", {}, str(exc)[:500])
        raise

    by_typology: dict[str, int] = {}
    for a in created:
        by_typology[a["typology"]] = by_typology.get(a["typology"], 0) + 1
    summary = {
        "model_runs": runs, "ranker_run_id": runs["ranker"], "transactions_considered": len(ledger.txns),
        "rule_signals": len(rules), "anomaly_signals": len(anomalies),
        "supervised": {"signals": len(sup.findings), "skipped_reason": sup.skipped_reason},
        "alerts": len(created), "alerts_by_typology": by_typology, "window_days": window,
    }
    audit.record(user, "detection.run", "model_run", runs["ranker"], summary)
    return summary
