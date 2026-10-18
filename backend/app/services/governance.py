"""Governance aggregations: model registry, pipeline timeline, data health and drift, exports, detector
comparison and the ordered lineage chain.

Everything here is a pure function over rows the router already fetched, so it can be tested without a
database. Metrics come from synthetic data and are labelled as a demonstration, never as validation.
"""

import json
import math
import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

# Fewer runs than this and a trend is not a trend; the UI says "not enough runs" instead of drawing one.
MIN_RUNS_FOR_TREND = 3
SCORE_BINS = 10
# Population stability index thresholds used by the drift view (0.10 watch, 0.25 drift).
PSI_WATCH, PSI_DRIFT = 0.10, 0.25
MAX_RESULTS_MARKDOWN = 20_000

ARTIFACTS_DIR = Path(__file__).resolve().parents[2] / "ml" / "artifacts"


# ---------------------------------------------------------------------------------------------- helpers


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def duration_seconds(started: str | None, completed: str | None) -> float | None:
    start, end = parse_time(started), parse_time(completed)
    if not start or not end:
        return None
    return max((end - start).total_seconds(), 0.0)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


def _metric_key(key: str) -> str:
    """Normalise metric names: 'precision_at_100', 'P@100' and 'precision@100' are the same metric."""
    k = key.strip().lower().replace("-", "_").replace(" ", "_")
    k = k.replace("_at_", "@").replace("at_k", "@k")
    k = re.sub(r"^p@", "precision@", k)
    k = re.sub(r"^r@", "recall@", k)
    k = {"auprc": "pr_auc", "average_precision": "pr_auc", "auroc": "roc_auc", "false_alerts_per_1k": "false_alerts_per_1000_tx"}.get(k, k)
    return k


# Scalars in a metrics blob that are settings or sizes, not performance metrics.
NOT_METRICS = {"threshold", "seed", "random_seed", "n", "n_rows", "rows", "positives", "epoch", "epochs", "rounds"}
HEADLINE_ORDER = ("pr_auc", "roc_auc", "precision", "recall", "f1", "false_alerts_per_1000_tx")


def headline_metrics(metrics: dict | None) -> dict[str, float]:
    """Flat, normalised scalar metrics from a model run's free-form `metrics` JSON.

    Workers write metrics either flat or split by evaluation window; the test window wins, then
    validation, then the top level. Nested dicts other than the window are ignored (e.g. confusion matrix).
    """
    if not isinstance(metrics, dict):
        return {}
    sources = [metrics]
    for window in ("val", "validation", "test"):
        if isinstance(metrics.get(window), dict):
            sources.append(metrics[window])
    out: dict[str, float] = {}
    for source in sources:  # later sources (test) override earlier ones
        for key, value in source.items():
            number, name = _number(value), _metric_key(str(key))
            if number is not None and name not in NOT_METRICS:
                out[name] = number
    return out


def ordered_metric_names(rows: Iterable[dict[str, float]]) -> list[str]:
    """Metric names across rows: headline metrics first, then @K metrics by K, then the rest."""
    names = {name for row in rows for name in row}

    def sort_key(name: str):
        if name in HEADLINE_ORDER:
            return (0, HEADLINE_ORDER.index(name), 0, name)
        at = re.match(r"^(precision|recall)@(\d+)$", name)
        if at:
            return (1, 0 if at.group(1) == "precision" else 1, int(at.group(2)), name)
        return (2, 0, 0, name)

    return sorted(names, key=sort_key)


# ------------------------------------------------------------------------------------- model registry


def summarize_signal_stats(stats: list[dict]) -> dict[str, dict]:
    """Per model run: total and triggered signal counts, typology coverage and score range."""
    out: dict[str, dict] = {}
    for row in stats:
        run = out.setdefault(row["model_run_id"], {"signal_count": 0, "triggered_count": 0, "scored_count": 0,
                                                   "score_min": None, "score_max": None, "sources": set(),
                                                   "typologies": defaultdict(int)})
        n = int(row.get("signal_count") or 0)
        run["signal_count"] += n
        run["scored_count"] += int(row.get("scored_count") or 0)
        if row.get("source"):
            run["sources"].add(row["source"])
        if row.get("triggered"):
            run["triggered_count"] += n
            run["typologies"][row.get("typology") or "unspecified"] += n
        for bound, pick in (("score_min", min), ("score_max", max)):
            value = _number(row.get(bound))
            if value is not None:
                run[bound] = value if run[bound] is None else pick(run[bound], value)
    for run in out.values():
        run["sources"] = sorted(run["sources"])
        run["typologies"] = dict(sorted(run["typologies"].items(), key=lambda kv: (-kv[1], kv[0])))
    return out


EMPTY_SIGNALS = {"signal_count": 0, "triggered_count": 0, "scored_count": 0, "score_min": None, "score_max": None,
                 "sources": [], "typologies": {}}


def group_registry(runs: list[dict], signals: dict[str, dict], alert_counts: dict[str, int]) -> list[dict]:
    """Model runs grouped by (model_name, model_version), newest run first inside each entry."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for run in runs:
        groups[(run["model_name"], run["model_version"])].append(run)
    entries = []
    for (name, version), group in groups.items():
        group.sort(key=lambda r: r.get("started_at") or "", reverse=True)
        latest = group[0]
        completed = [r for r in group if r.get("status") == "completed"]
        reference = completed[0] if completed else latest
        entries.append({
            "model_name": name,
            "model_version": version,
            "model_kind": latest.get("model_kind"),
            "status": latest.get("status"),
            "run_count": len(group),
            "completed_run_count": len(completed),
            "feature_set_version": reference.get("feature_set_version"),
            "dataset_version": reference.get("dataset_version"),
            "code_version": reference.get("code_version"),
            "config": reference.get("config") or {},
            "split_config": reference.get("split_config") or {},
            "random_seed": reference.get("random_seed"),
            "metrics": reference.get("metrics") or {},
            "headline_metrics": headline_metrics(reference.get("metrics")),
            "metrics_are_demonstration": reference.get("metrics_are_demonstration", True),
            "artifact_bucket": reference.get("artifact_bucket"),
            "artifact_path": reference.get("artifact_path"),
            "last_run": {k: latest.get(k) for k in ("id", "status", "started_at", "completed_at", "error_message")},
            "runs": [{
                "id": r["id"], "status": r.get("status"), "started_at": r.get("started_at"), "completed_at": r.get("completed_at"),
                "duration_seconds": duration_seconds(r.get("started_at"), r.get("completed_at")),
                "dataset_version": r.get("dataset_version"), "error_message": r.get("error_message"),
                "alert_count": alert_counts.get(r["id"], 0),
                **{k: v for k, v in signals.get(r["id"], EMPTY_SIGNALS).items() if k in ("signal_count", "triggered_count")},
            } for r in group],
        })
    entries.sort(key=lambda e: e["last_run"].get("started_at") or "", reverse=True)
    return entries


def _card_summary(card: dict) -> dict:
    """The parts of an offline model card worth showing; drops per-epoch history and feature importances."""
    metrics = card.get("metrics") if isinstance(card.get("metrics"), dict) else {}
    detectors = []
    for name, windows in metrics.items():
        if not isinstance(windows, dict):
            continue
        test = windows.get("test") if isinstance(windows.get("test"), dict) else {}
        detectors.append({
            "name": name,
            "test": headline_metrics({"test": test}),
            "validation": headline_metrics({"val": windows.get("val")}) if isinstance(windows.get("val"), dict) else {},
            "recall_by_typology": {k: v for k, v in (test.get("recall_by_typology") or {}).items() if _number(v) is not None},
            "thresholds": (card.get("thresholds") or {}).get(name),
        })
    return {
        "model_version": card.get("model_version"),
        "feature_version": card.get("feature_version"),
        "created_at": card.get("created_at"),
        "seed": card.get("seed"),
        "dataset": card.get("dataset"),
        "split": card.get("split"),
        "selected_model": card.get("selected_model"),
        "rules": card.get("rules") if isinstance(card.get("rules"), list) else [],
        "feature_count": len(card.get("feature_columns") or []),
        "detectors": detectors,
        "limitations": card.get("limitations") if isinstance(card.get("limitations"), list) else [],
    }


def read_model_cards(root: Path = ARTIFACTS_DIR) -> list[dict]:
    """Offline training results from the ML teammate's artifact folders (read-only, never written).

    Each folder may hold a `model_card.json` and/or a `RESULTS.md`. These are offline results on synthetic
    data, not runs recorded in this tenant's database.
    """
    cards = []
    if not root.is_dir():
        return cards
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        card_path, results_path = folder / "model_card.json", folder / "RESULTS.md"
        if not card_path.is_file() and not results_path.is_file():
            continue
        entry: dict[str, Any] = {"artifact_set": folder.name, "source": "offline_training", "metrics_are_demonstration": True,
                                 "card": None, "card_error": None, "results_markdown": None}
        if card_path.is_file():
            try:
                entry["card"] = _card_summary(json.loads(card_path.read_text(encoding="utf-8")))
            except (OSError, ValueError) as exc:
                entry["card_error"] = f"model_card.json could not be read: {exc.__class__.__name__}"
        if results_path.is_file():
            try:
                text = results_path.read_text(encoding="utf-8")
                entry["results_markdown"] = text[:MAX_RESULTS_MARKDOWN]
                entry["results_truncated"] = len(text) > MAX_RESULTS_MARKDOWN
            except OSError:
                pass
        cards.append(entry)
    return cards


# ------------------------------------------------------------------------------------------ pipelines


def pipeline_timeline(ingestion_runs: list[dict], model_runs: list[dict], agent_runs: list[dict]) -> list[dict]:
    """Ingestion, model and agent runs as one timeline, newest first."""
    items = []
    for run in ingestion_runs:
        file = run.get("source_files") or {}
        items.append({
            "kind": "ingestion", "id": run["id"], "name": file.get("filename") or "CSV upload",
            "version": run.get("pipeline_version") or run.get("schema_version"), "status": run.get("status"),
            "started_at": run.get("started_at"), "completed_at": run.get("completed_at"),
            "duration_seconds": duration_seconds(run.get("started_at"), run.get("completed_at")),
            "rows": {k: run.get(k) for k in ("row_count", "accepted_count", "warning_count", "rejected_count")},
            "error_message": run.get("error_message"),
        })
    for run in model_runs:
        items.append({
            "kind": "model", "id": run["id"], "name": run.get("model_name"), "version": run.get("model_version"),
            "model_kind": run.get("model_kind"), "status": run.get("status"),
            "started_at": run.get("started_at"), "completed_at": run.get("completed_at"),
            "duration_seconds": duration_seconds(run.get("started_at"), run.get("completed_at")),
            "rows": {"signal_count": run.get("signal_count"), "triggered_count": run.get("triggered_count"),
                     "alert_count": run.get("alert_count")},
            "dataset_version": run.get("dataset_version"), "error_message": run.get("error_message"),
        })
    for run in agent_runs:
        started = run.get("started_at") or run.get("created_at")
        items.append({
            "kind": "agent", "id": run["id"], "name": run.get("graph_name"), "version": run.get("graph_version"),
            "status": run.get("status"), "started_at": started, "completed_at": run.get("completed_at"),
            "duration_seconds": duration_seconds(started, run.get("completed_at")),
            "rows": {"step_count": run.get("step_count")}, "current_step": run.get("current_step"),
            "case_id": run.get("case_id"), "error_message": run.get("error_message"),
        })
    items.sort(key=lambda i: i.get("started_at") or "", reverse=True)
    return items


def pipeline_summary(items: list[dict]) -> dict:
    by_kind: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for item in items:
        by_kind[item["kind"]][item.get("status") or "unknown"] += 1
    return {kind: dict(statuses) for kind, statuses in by_kind.items()}


# ----------------------------------------------------------------------------------- drift and health


def run_health(ingestion_runs: list[dict], dq_mix: list[dict]) -> dict:
    """Per ingestion run, oldest first: rejected and warning rates plus the issue-type mix."""
    mix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    severity: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in dq_mix:
        n = int(row.get("issue_count") or 0)
        mix[row["run_id"]][row["issue_type"]] += n
        severity[row["run_id"]][row["severity"]] += n
    runs = []
    for run in sorted(ingestion_runs, key=lambda r: r.get("started_at") or ""):
        total = int(run.get("row_count") or 0)
        rejected, warnings = int(run.get("rejected_count") or 0), int(run.get("warning_count") or 0)
        runs.append({
            "run_id": run["id"], "filename": (run.get("source_files") or {}).get("filename"), "status": run.get("status"),
            "started_at": run.get("started_at"), "row_count": total, "rejected_count": rejected, "warning_count": warnings,
            "rejected_rate": rejected / total if total else None, "warning_rate": warnings / total if total else None,
            "issue_mix": dict(mix.get(run["id"], {})), "issue_severity": dict(severity.get(run["id"], {})),
            "issue_count": sum(mix.get(run["id"], {}).values()),
        })
    rated = [r for r in runs if r["rejected_rate"] is not None]
    trend: dict[str, Any] = {"status": "not_enough_runs", "runs_needed": MIN_RUNS_FOR_TREND, "runs_available": len(rated)}
    if len(rated) >= MIN_RUNS_FOR_TREND:
        baseline = sum(r["rejected_rate"] for r in rated[:-1]) / (len(rated) - 1)
        latest = rated[-1]["rejected_rate"]
        trend = {"status": "ok", "runs_available": len(rated), "baseline_rejected_rate": baseline,
                 "latest_rejected_rate": latest, "change": latest - baseline}
    issue_totals: dict[str, int] = defaultdict(int)
    for run in runs:
        for issue_type, n in run["issue_mix"].items():
            issue_totals[issue_type] += n
    return {"runs": runs, "rejected_rate_trend": trend,
            "issue_totals": dict(sorted(issue_totals.items(), key=lambda kv: (-kv[1], kv[0])))}


def histograms(rows: list[dict]) -> dict[str, list[int]]:
    """{model_run_id: [count per bin]} with SCORE_BINS bins over [0, 1]."""
    out: dict[str, list[int]] = {}
    for row in rows:
        bins = out.setdefault(row["model_run_id"], [0] * SCORE_BINS)
        index = min(max(int(row["bin"]), 1), SCORE_BINS) - 1
        bins[index] += int(row.get("signal_count") or 0)
    return out


def psi(expected: list[int], actual: list[int], floor: float = 1e-4) -> float | None:
    """Population stability index between two histograms with the same bins; None when either is empty."""
    e_total, a_total = sum(expected), sum(actual)
    if not e_total or not a_total or len(expected) != len(actual):
        return None
    value = 0.0
    for e, a in zip(expected, actual):
        pe, pa = max(e / e_total, floor), max(a / a_total, floor)
        value += (pa - pe) * math.log(pa / pe)
    return value


def psi_status(value: float | None) -> str | None:
    if value is None:
        return None
    return "drift" if value >= PSI_DRIFT else "watch" if value >= PSI_WATCH else "stable"


def score_drift(model_runs: list[dict], hists: dict[str, list[int]]) -> list[dict]:
    """Score distribution per model run with signals, plus PSI against the previous run of the same model."""
    by_model: dict[str, list[dict]] = defaultdict(list)
    for run in model_runs:
        if hists.get(run["id"]):
            by_model[run["model_name"]].append(run)
    out = []
    for name, runs in by_model.items():
        runs.sort(key=lambda r: r.get("started_at") or "")
        history = []
        for i, run in enumerate(runs):
            value = psi(hists[runs[i - 1]["id"]], hists[run["id"]]) if i else None
            history.append({"model_run_id": run["id"], "model_version": run.get("model_version"), "started_at": run.get("started_at"),
                            "histogram": hists[run["id"]], "scored_count": sum(hists[run["id"]]),
                            "psi_vs_previous": value, "psi_status": psi_status(value)})
        out.append({"model_name": name, "model_kind": runs[-1].get("model_kind"), "runs": history,
                    "comparison": "ok" if len(runs) >= 2 else "not_enough_runs"})
    out.sort(key=lambda m: m["runs"][-1].get("started_at") or "", reverse=True)
    return out


# --------------------------------------------------------------------------------------------- exports


def exports_from_audit(events: list[dict], cases: dict[str, dict]) -> list[dict]:
    """Case exports recorded in the audit log (`case.exported`), newest first, with any recorded hashes."""
    out = []
    for event in events:
        meta = event.get("metadata") or {}
        case = cases.get(event.get("object_id") or "") or {}
        out.append({
            "event_id": event.get("event_id"), "audit_id": event.get("id"), "case_id": event.get("object_id"),
            "case_number": case.get("case_number"), "case_title": case.get("title"),
            "path": meta.get("path"), "sha256": meta.get("sha256"), "manifest_sha256": meta.get("manifest_sha256"),
            "actor_id": event.get("actor_id"), "actor_type": event.get("actor_type"),
            "occurred_at": event.get("occurred_at"), "request_id": event.get("request_id"),
        })
    out.sort(key=lambda e: e.get("occurred_at") or "", reverse=True)
    return out


# -------------------------------------------------------------------------------- detector comparison


def detector_comparison(model_runs: list[dict], signals: dict[str, dict], alert_counts: dict[str, int]) -> dict:
    """Latest completed run per (model_name, model_version), metrics side by side, signal and typology coverage."""
    latest: dict[tuple[str, str], dict] = {}
    for run in model_runs:
        if run.get("status") != "completed":
            continue
        key = (run["model_name"], run["model_version"])
        if key not in latest or (run.get("completed_at") or "") > (latest[key].get("completed_at") or ""):
            latest[key] = run
    detectors = []
    for run in sorted(latest.values(), key=lambda r: (r.get("model_kind") or "", r["model_name"])):
        stats = signals.get(run["id"], EMPTY_SIGNALS)
        detectors.append({
            "model_run_id": run["id"], "model_name": run["model_name"], "model_version": run["model_version"],
            "model_kind": run.get("model_kind"), "dataset_version": run.get("dataset_version"),
            "completed_at": run.get("completed_at"), "metrics": headline_metrics(run.get("metrics")),
            "metrics_are_demonstration": run.get("metrics_are_demonstration", True),
            "signal_count": stats["signal_count"], "triggered_count": stats["triggered_count"],
            "typologies": stats["typologies"], "alert_count": alert_counts.get(run["id"], 0),
        })
    typologies = sorted({t for d in detectors for t in d["typologies"]})
    return {
        "detectors": detectors,
        "metric_names": ordered_metric_names(d["metrics"] for d in detectors),
        "typologies": typologies,
        "datasets": sorted({d["dataset_version"] for d in detectors if d.get("dataset_version")}),
    }


# ------------------------------------------------------------------------------------------- lineage

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
ALERT_RE = re.compile(r"^(?:al|alert)[-_ #]?(\d{1,9})$", re.IGNORECASE)
CASE_RE = re.compile(r"^case[-_ #]?(\d{1,9})$", re.IGNORECASE)
# "file.csv#12", "file.csv row 12", "row 12", "<run id prefix>:12"
ROW_RE = re.compile(r"^(?:(?P<scope>.+?)\s*(?:#|:|\s+row\s*)|row\s*)(?P<row>\d{1,9})$", re.IGNORECASE)


def classify_ref(ref: str) -> dict:
    """What kind of reference the user typed. A plain value may be a transaction_ref or an alert number."""
    text = ref.strip()
    if UUID_RE.match(text):
        return {"kind": "uuid", "value": text.lower()}
    if m := ALERT_RE.match(text):
        return {"kind": "alert_number", "value": int(m.group(1))}
    if m := CASE_RE.match(text):
        return {"kind": "case_number", "value": int(m.group(1))}
    if m := ROW_RE.match(text):
        return {"kind": "source_row", "scope": (m.group("scope") or "").strip() or None, "value": int(m.group("row"))}
    return {"kind": "text", "value": text}


def _node(key: str, title: str, items: list[dict], note: str | None = None) -> dict:
    return {"key": key, "title": title, "linked": bool(items), "count": len(items), "items": items, "note": note}


def build_chain(*, files: list[dict], runs: list[dict], rows: list[dict], issues: list[dict], transactions: list[dict],
                features: list[dict] | None, signals: list[dict], model_runs: list[dict], alerts: list[dict],
                cases: list[dict], evidence: list[dict], narratives: list[dict], decisions: list[dict]) -> list[dict]:
    """Lineage as ordered nodes: source file -> ingestion run -> source row (+DQ issues) -> transaction ->
    features -> signals and model runs -> alerts -> cases -> evidence -> narratives -> decisions.

    A node with no items is a gap: the link does not exist yet and is never invented.
    """
    issues_by_row: dict[str, list[dict]] = defaultdict(list)
    for issue in issues:
        issues_by_row[issue.get("source_row_id") or ""].append(issue)
    run_names = {m["id"]: f"{m.get('model_name')} {m.get('model_version')}" for m in model_runs}
    return [
        _node("source_file", "Source file", [{
            "id": f.get("id"), "label": f.get("filename"), "detail": f"SHA-256 {f['checksum_sha256'][:6]}…{f['checksum_sha256'][-4:]}" if f.get("checksum_sha256") else None,
            "owner": f.get("data_owner"), "system": f.get("source_system"), "at": f.get("uploaded_at")} for f in files]),
        _node("ingestion_run", "Ingestion run", [{
            "id": r.get("id"), "label": f"Batch ingestion ({str(r.get('id'))[:8]})", "status": r.get("status"),
            "detail": f"schema {r.get('schema_version')}" + (f" · pipeline {r['pipeline_version']}" if r.get("pipeline_version") else ""),
            "at": r.get("started_at")} for r in runs]),
        _node("source_row", "Source row and data quality", [{
            "id": r.get("id"), "label": f"Row {r.get('row_number')}", "status": r.get("status"),
            "detail": f"{len(issues_by_row.get(r.get('id') or '', []))} data-quality issue(s)",
            "issues": [{"id": i.get("id"), "issue_type": i.get("issue_type"), "severity": i.get("severity"),
                        "status": i.get("status"), "message": i.get("message")} for i in issues_by_row.get(r.get("id") or "", [])],
        } for r in rows]),
        _node("transaction", "Canonical transaction", [{
            "id": t.get("id"), "label": t.get("transaction_ref") or str(t.get("id"))[:8],
            "detail": " ".join(str(x) for x in (t.get("amount"), t.get("currency"), t.get("channel")) if x) or None,
            "at": t.get("occurred_at")} for t in transactions]),
        _node("features", "Features", [{
            "id": f.get("id"), "label": f.get("feature_set_version"), "detail": f"as of {f.get('as_of_timestamp')}",
            "at": f.get("computed_at")} for f in (features or [])],
            note="Features are listed for transaction lineage only." if features is None else None),
        _node("signals", "Signals and model runs", [{
            "id": s.get("id"), "label": s.get("signal_name"), "status": "triggered" if s.get("triggered") else "not triggered",
            "detail": " · ".join(x for x in (s.get("typology"), run_names.get(s.get("model_run_id"))) if x) or None,
            "score": s.get("score"), "model_run_id": s.get("model_run_id")} for s in signals]),
        _node("alerts", "Alerts", [{
            "id": a.get("id"), "label": f"AL-{a.get('alert_number')}", "status": a.get("status"),
            "detail": " · ".join(x for x in (a.get("typology"), a.get("priority_band")) if x) or None,
            "model_run": run_names.get(a.get("model_run_id"))} for a in alerts]),
        _node("cases", "Cases", [{
            "id": c.get("id"), "label": f"CASE-{int(c['case_number']):04d}" if c.get("case_number") is not None else str(c.get("id"))[:8],
            "status": c.get("status"), "detail": c.get("title")} for c in cases]),
        _node("evidence", "Evidence", [{
            "id": e.get("id"), "label": e.get("evidence_type"), "detail": e.get("explanation"),
            "owner": e.get("added_by_type"), "at": e.get("created_at")} for e in evidence]),
        _node("narratives", "Narratives", [{
            "id": n.get("id"), "label": f"Version {n.get('version')}", "status": n.get("verification_status"),
            "detail": n.get("author_type"), "at": n.get("created_at")} for n in narratives]),
        _node("decisions", "Reviewer decisions", [{
            "id": d.get("id"), "label": d.get("decision"), "status": d.get("disposition"),
            "at": d.get("decided_at")} for d in decisions]),
    ]
