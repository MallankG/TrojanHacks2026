"""Governance, model and audit center (PRD §10, §13 screens 3 and 8): lineage lookup, model registry,
pipelines, drift and data health, evidence exports and detector comparison. All endpoints are read-only.

Metrics are a demonstration on synthetic data. Scores are review-priority leads, never findings.
"""

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.routes.lineage import alert_lineage, transaction_lineage
from app.core.db import fetch_by_ids, get_db, get_one
from app.core.errors import NotFound
from app.core.security import ANY_CALLER, ANY_USER, CurrentUser
from app.services import governance as gov
from app.services.case_visibility import authorized_case_ids

router = APIRouter(tags=["governance"])

RUN_LIMIT = 200  # newest runs considered by the registry, pipelines and drift views
MAX_MATCHES = 20
DEMO_NOTE = "Demonstration metrics on synthetic data. They do not predict performance at a real institution."

MODEL_RUN_COLUMNS = ("id, model_name, model_kind, model_version, feature_set_version, dataset_version, code_version, "
                     "config, split_config, random_seed, metrics, metrics_are_demonstration, artifact_bucket, "
                     "artifact_path, status, error_message, started_at, completed_at")


# ------------------------------------------------------------------------------------------ data access


def _model_runs(tenant_id: str, limit: int = RUN_LIMIT, columns: str = MODEL_RUN_COLUMNS) -> list[dict]:
    return get_db().table("model_runs").select(columns).eq("tenant_id", tenant_id) \
        .order("started_at", desc=True).limit(limit).execute().data or []


def _ingestion_runs(tenant_id: str, limit: int = RUN_LIMIT) -> list[dict]:
    return get_db().table("ingestion_runs").select(
        "id, status, schema_version, pipeline_version, row_count, accepted_count, warning_count, rejected_count, "
        "error_message, started_at, completed_at, source_files(filename, source_system, data_owner)"
    ).eq("tenant_id", tenant_id).order("started_at", desc=True).limit(limit).execute().data or []


def _rpc(name: str, tenant_id: str, ids: list[str]) -> list[dict]:
    if not ids:
        return []
    return get_db().rpc(name, {"p_tenant_id": tenant_id, "p_run_ids": ids}).execute().data or []


def _alert_counts(tenant_id: str, run_ids: list[str]) -> dict[str, int]:
    """Alerts published per model run."""
    return {r["model_run_id"]: int(r["alert_count"]) for r in _rpc("governance_alert_counts", tenant_id, run_ids)}


def _signal_stats(tenant_id: str, run_ids: list[str]) -> dict[str, dict]:
    return gov.summarize_signal_stats(_rpc("governance_signal_stats", tenant_id, run_ids))


# ------------------------------------------------------------------------------------------- lineage


def _chain_for_transaction(user: CurrentUser, transaction_id: str) -> list[dict]:
    lin = transaction_lineage(transaction_id, user)
    run = lin["source"]["ingestion_run"] or {}
    file = run.get("source_files")
    return gov.build_chain(
        files=[file] if file else [], runs=[run] if run else [],
        rows=[lin["source"]["source_row"]] if lin["source"]["source_row"] else [],
        issues=lin["source"]["data_quality_issues"] or [], transactions=[lin["transaction"]], features=lin["features"],
        signals=lin["risk_signals"], model_runs=lin["model_runs"], alerts=lin["alerts"], cases=lin["cases"],
        evidence=lin["case_evidence"], narratives=lin["narratives"], decisions=lin["decisions"])


def _chain_for_alert(user: CurrentUser, alert_id: str) -> list[dict]:
    lin = alert_lineage(alert_id, user)
    runs = lin["source"]["ingestion_runs"]
    files = {f["filename"] + f.get("checksum_sha256", ""): f for f in (r.get("source_files") for r in runs) if f}
    alerts = lin.get("alerts") or [lin["alert"]]
    if not any(a.get("id") == lin["alert"]["id"] for a in alerts):
        alerts = [lin["alert"], *alerts]
    return gov.build_chain(
        files=list(files.values()), runs=runs, rows=lin["source"]["source_rows"], issues=lin["source"]["data_quality_issues"],
        transactions=lin["transactions"], features=None, signals=lin.get("risk_signals", []),
        model_runs=lin.get("model_runs", []), alerts=alerts, cases=lin.get("cases", []),
        evidence=lin.get("case_evidence", []), narratives=lin.get("narratives", []), decisions=lin.get("decisions", []))


def _chain_for_source_row(user: CurrentUser, row_id: str) -> list[dict]:
    """A source row that never became a transaction (rejected) still has a file, a run and its issues."""
    db, tid = get_db(), user.tenant_id
    row = get_one("source_rows", tid, row_id, "id, row_number, status, run_id")
    if not row:
        raise NotFound("Source row")
    txn = db.table("transactions").select("id").eq("tenant_id", tid).eq("source_row_id", row_id).limit(1).execute().data
    if txn:
        return _chain_for_transaction(user, txn[0]["id"])
    run = get_one("ingestion_runs", tid, row["run_id"], "id, status, schema_version, pipeline_version, started_at, "
                  "source_files(id, filename, checksum_sha256, source_system, data_owner, uploaded_at)") or {}
    issues = db.table("data_quality_issues").select("id, source_row_id, issue_type, severity, status, message") \
        .eq("tenant_id", tid).eq("source_row_id", row_id).execute().data or []
    return gov.build_chain(
        files=[run["source_files"]] if run.get("source_files") else [], runs=[run] if run else [], rows=[row], issues=issues,
        transactions=[], features=[], signals=[], model_runs=[], alerts=[], cases=[], evidence=[], narratives=[], decisions=[])


def _resolve(user: CurrentUser, ref: str) -> list[dict]:
    """Matches for a transaction_ref, alert number, case number, source-row reference or any of their ids."""
    db, tid = get_db(), user.tenant_id
    parsed = gov.classify_ref(ref)
    matches: list[dict] = []

    def txn_matches(query) -> None:
        for t in query.limit(MAX_MATCHES).execute().data or []:
            matches.append({"kind": "transaction", "id": t["id"], "label": t.get("transaction_ref") or t["id"][:8],
                            "detail": f"{t.get('amount')} {t.get('currency')} · {t.get('occurred_at')}"})

    def alert_matches(query) -> None:
        for a in query.limit(MAX_MATCHES).execute().data or []:
            matches.append({"kind": "alert", "id": a["id"], "label": f"AL-{a['alert_number']}",
                            "detail": f"{a.get('typology')} · {a.get('status')}"})

    def case_alerts(case: dict) -> None:
        if case["id"] not in authorized_case_ids(user, {case["id"]}):
            return
        links = db.table("case_alerts").select("alert_id").eq("tenant_id", tid).eq("case_id", case["id"]) \
            .limit(MAX_MATCHES).execute().data or []
        if not links:
            return
        start = len(matches)
        alert_matches(db.table("alerts").select(alert_cols).eq("tenant_id", tid).in_("id", [link["alert_id"] for link in links]))
        for m in matches[start:]:
            m["detail"] = f"in CASE-{case['case_number']:04d} · {m['detail']}"

    def row_matches(query) -> None:
        for r in query.limit(MAX_MATCHES).execute().data or []:
            run = r.get("ingestion_runs") or {}
            filename = (run.get("source_files") or {}).get("filename") or "upload"
            matches.append({"kind": "source_row", "id": r["id"], "label": f"{filename} row {r['row_number']}",
                            "detail": f"{r.get('status')} · run {r['run_id'][:8]}"})

    txn_cols = "id, transaction_ref, amount, currency, occurred_at"
    alert_cols = "id, alert_number, typology, status"
    row_cols = "id, row_number, status, run_id, ingestion_runs(source_files(filename))"
    kind = parsed["kind"]
    if kind == "uuid":
        uid = parsed["value"]
        txn_matches(db.table("transactions").select(txn_cols).eq("tenant_id", tid).eq("id", uid))
        alert_matches(db.table("alerts").select(alert_cols).eq("tenant_id", tid).eq("id", uid))
        row_matches(db.table("source_rows").select(row_cols).eq("tenant_id", tid).eq("id", uid))
        case = get_one("cases", tid, uid, "id, case_number")
        if case:
            case_alerts(case)
    elif kind == "alert_number":
        alert_matches(db.table("alerts").select(alert_cols).eq("tenant_id", tid).eq("alert_number", parsed["value"]))
    elif kind == "case_number":
        found = db.table("cases").select("id, case_number").eq("tenant_id", tid).eq("case_number", parsed["value"]).limit(1).execute().data
        if found:
            case_alerts(found[0])
    elif kind == "source_row":
        query = db.table("source_rows").select(row_cols).eq("tenant_id", tid).eq("row_number", parsed["value"])
        scope = parsed.get("scope")
        if scope:
            if gov.UUID_RE.match(scope):
                query = query.eq("run_id", scope.lower())
            else:
                files = db.table("source_files").select("id").eq("tenant_id", tid).ilike("filename", f"%{_like(scope)}%") \
                    .limit(MAX_MATCHES).execute().data or []
                runs = db.table("ingestion_runs").select("id").eq("tenant_id", tid) \
                    .in_("file_id", [f["id"] for f in files] or ["00000000-0000-0000-0000-000000000000"]).execute().data or []
                query = query.in_("run_id", [r["id"] for r in runs] or ["00000000-0000-0000-0000-000000000000"])
        row_matches(query.order("created_at", desc=True))
    # Any non-uuid text may also be a transaction reference ("TX-1001"), and plain digits an alert number.
    if kind != "uuid":
        txn_matches(db.table("transactions").select(txn_cols).eq("tenant_id", tid).eq("transaction_ref", ref.strip()))
        if kind == "text" and ref.strip().isdigit() and len(ref.strip()) <= 9:
            alert_matches(db.table("alerts").select(alert_cols).eq("tenant_id", tid).eq("alert_number", int(ref.strip())))
        if kind == "text" and not matches:
            txn_matches(db.table("transactions").select(txn_cols).eq("tenant_id", tid)
                        .ilike("transaction_ref", f"{_like(ref.strip())}%").order("transaction_ref"))
    unique = {(m["kind"], m["id"]): m for m in matches}
    return list(unique.values())[:MAX_MATCHES]


def _like(text: str) -> str:
    """Escape LIKE wildcards and the PostgREST filter separators in user text."""
    return "".join(f"\\{c}" if c in "%_\\" else c for c in text).replace(",", "").replace("(", "").replace(")", "")


@router.get("/lineage/lookup")
def lineage_lookup(ref: str = Query(min_length=1, max_length=200, description="transaction_ref, AL-<n>, CASE-<n>, "
                                    "'<file>#<row>' / 'row <n>', or any transaction, alert, source-row or case id"),
                   user: CurrentUser = Depends(ANY_CALLER)) -> dict:
    """Resolves a reference to ids and returns the ordered lineage chain for the first match."""
    matches = _resolve(user, ref)
    if not matches:
        return {"ref": ref, "parsed": gov.classify_ref(ref)["kind"], "matches": [], "selected": None, "chain": []}
    selected = matches[0]
    builders = {"transaction": _chain_for_transaction, "alert": _chain_for_alert, "source_row": _chain_for_source_row}
    chain = builders[selected["kind"]](user, selected["id"])
    return {"ref": ref, "parsed": gov.classify_ref(ref)["kind"], "matches": matches, "selected": selected, "chain": chain}


# ------------------------------------------------------------------------------------ model registry


@router.get("/models/registry")
def model_registry(user: CurrentUser = Depends(ANY_USER)) -> dict:
    runs = _model_runs(user.tenant_id)
    ids = [r["id"] for r in runs]
    entries = gov.group_registry(runs, _signal_stats(user.tenant_id, ids), _alert_counts(user.tenant_id, ids))
    return {
        "models": entries,
        "run_count": len(runs),
        "run_limit": RUN_LIMIT,
        "offline_model_cards": gov.read_model_cards(),
        "note": DEMO_NOTE,
        "offline_note": "Offline training results from the ML artifacts folder (read-only). They were not produced by "
                        "a run recorded in this workspace.",
    }


# ----------------------------------------------------------------------------------------- pipelines


@router.get("/governance/pipelines")
def pipelines(limit: int = Query(default=50, ge=1, le=RUN_LIMIT), user: CurrentUser = Depends(ANY_USER)) -> dict:
    tid = user.tenant_id
    model_runs = _model_runs(tid, limit, "id, model_name, model_kind, model_version, dataset_version, status, "
                                         "error_message, started_at, completed_at")
    ids = [r["id"] for r in model_runs]
    stats, alerts = _signal_stats(tid, ids), _alert_counts(tid, ids)
    for run in model_runs:
        s = stats.get(run["id"], gov.EMPTY_SIGNALS)
        run.update(signal_count=s["signal_count"], triggered_count=s["triggered_count"], alert_count=alerts.get(run["id"], 0))
    agent_runs = get_db().table("agent_runs").select(
        "id, case_id, graph_name, graph_version, status, current_step, steps, error_message, started_at, completed_at, created_at"
    ).eq("tenant_id", tid).order("created_at", desc=True).limit(limit).execute().data or []
    allowed_cases = authorized_case_ids(user, {r["case_id"] for r in agent_runs})
    agent_runs = [r for r in agent_runs if r["case_id"] in allowed_cases]
    for run in agent_runs:
        steps = run.pop("steps", None)
        run["step_count"] = len(steps) if isinstance(steps, list) else 0
    items = gov.pipeline_timeline(_ingestion_runs(tid, limit), model_runs, agent_runs)[: limit * 3]
    return {"items": items, "summary": gov.pipeline_summary(items), "limit": limit}


# ---------------------------------------------------------------------------------- drift and health


@router.get("/governance/health")
def data_health(user: CurrentUser = Depends(ANY_USER)) -> dict:
    tid = user.tenant_id
    runs = _ingestion_runs(tid)
    health = gov.run_health(runs, _rpc("governance_dq_mix", tid, [r["id"] for r in runs]))
    model_runs = _model_runs(tid, columns="id, model_name, model_kind, model_version, status, started_at")
    hists = gov.histograms(_rpc("governance_score_histogram", tid, [r["id"] for r in model_runs]))
    return {
        **health,
        "score_distributions": gov.score_drift(model_runs, hists),
        "min_runs_for_trend": gov.MIN_RUNS_FOR_TREND,
        "psi_thresholds": {"watch": gov.PSI_WATCH, "drift": gov.PSI_DRIFT},
        "score_bins": gov.SCORE_BINS,
        "note": "Computed from this workspace's own runs. Synthetic data; a trend needs at least "
                f"{gov.MIN_RUNS_FOR_TREND} runs.",
    }


# -------------------------------------------------------------------------------------------- exports


@router.get("/governance/exports")
def exports(limit: int = Query(default=100, ge=1, le=500), user: CurrentUser = Depends(ANY_USER)) -> dict:
    tid = user.tenant_id
    events = get_db().table("audit_events").select("*").eq("tenant_id", tid).eq("action", "case.exported") \
        .order("occurred_at", desc=True).limit(limit).execute().data or []
    allowed_cases = authorized_case_ids(user, {e["object_id"] for e in events if gov.UUID_RE.match(e.get("object_id") or "")})
    events = [e for e in events if e.get("object_id") in allowed_cases]
    case_rows = fetch_by_ids("cases", tid, {e["object_id"] for e in events if gov.UUID_RE.match(e.get("object_id") or "")},
                             "id, case_number, title")
    actor_ids = {e["actor_id"] for e in events if e.get("actor_id")}
    actors = {p["id"]: p for p in fetch_by_ids("profiles", tid, actor_ids, "id, display_name, role")} if actor_ids else {}
    items = gov.exports_from_audit(events, {c["id"]: c for c in case_rows})
    for item in items:
        profile: dict[str, Any] = actors.get(item.get("actor_id") or "") or {}
        item["actor_name"], item["actor_role"] = profile.get("display_name"), profile.get("role")
    return {"items": items, "limit": limit,
            "hashed_count": sum(1 for i in items if i.get("sha256") or i.get("manifest_sha256"))}


# -------------------------------------------------------------------------------- detector comparison


@router.get("/governance/detectors")
def detectors(user: CurrentUser = Depends(ANY_USER)) -> dict:
    tid = user.tenant_id
    runs = _model_runs(tid)
    completed = [r["id"] for r in runs if r.get("status") == "completed"]
    comparison = gov.detector_comparison(runs, _signal_stats(tid, completed), _alert_counts(tid, completed))
    overlap_rows = _rpc("governance_signal_overlap", tid, [d["model_run_id"] for d in comparison["detectors"]])
    overlap = overlap_rows[0] if overlap_rows else {"rules_only": 0, "both_sources": 0, "models_only": 0}
    cards = [c for c in gov.read_model_cards() if c.get("card")]
    return {**comparison, "overlap": overlap, "offline_model_cards": cards, "note": DEMO_NOTE}
