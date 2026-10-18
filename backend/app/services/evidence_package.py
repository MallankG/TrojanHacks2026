"""Investigation readiness checklist and the hashed SAR-support evidence package (PRD §9, §14, §22.4, §23.20).

The package is a human-reviewed case-support artifact, never a FinCEN filing. Every file is built
deterministically from database records so an unchanged case re-exports to the same hashes; the
export id, creator and time live in `evidence_exports` and the audit trail, not inside the hashed files.
"""

import csv
import hashlib
import io
import json
from decimal import Decimal
from typing import Any, Literal

from app.core.db import fetch_by_ids, get_db, to_json
from app.services.cases import TXN_FIELDS

PACKAGE_SCHEMA = "tracelayer.evidence-package/v1"
CHECKLIST_VERSION = "readiness-checklist/v1"
MAX_AUDIT_EVENTS = 1000  # PostgREST's default max rows; the manifest records truncation

# Reads and exports are logged too, but including them would make every export change the next one's hash.
AUDIT_READ_ACTIONS = ("case.evidence_viewed", "case.evidence_bundle_accessed", "case.exported", "case.checklist_viewed",
                      "case.exports_listed")

# Analyst notes tagged with one of these payload kinds satisfy the matching checklist item.
NOTE_KINDS = ("counter_evidence", "dq_caveats_reviewed")

ALERT_FIELDS = (
    "id, alert_number, scope, typology, priority_band, priority_rank, rank_score, ranking_strategy, window_start, window_end, "
    "total_amount, amount_currency, transaction_count, connected_account_count, has_dq_caveat, dq_caveat_notes, limitations, "
    "explanation, model_run_id, status, created_at"
)
SIGNAL_FIELDS = (
    "id, model_run_id, source, signal_name, signal_version, typology, transaction_id, account_id, score, threshold, triggered, "
    "window_start, window_end, details, evidence_transaction_ids, created_at"
)
MODEL_RUN_FIELDS = "id, model_name, model_kind, model_version, feature_set_version, dataset_version, code_version, status, completed_at"
DQ_FIELDS = (
    "id, run_id, source_row_id, field_name, issue_type, severity, rule_id, message, observed_value, expected_value, status, "
    "remediation_note, resolved_at, created_at"
)
ACCOUNT_FIELDS = "id, account_ref, institution_ref, is_internal, account_type, status, jurisdiction, customer_risk_rating"

Status = Literal["complete", "partial", "missing"]


class UnresolvedReferences(Exception):
    """Evidence points at ids that do not resolve under the caller's tenant; the package is refused."""

    def __init__(self, missing: dict[str, list[str]]):
        super().__init__("Unresolved evidence references")
        self.missing = missing


# --- Hashing --------------------------------------------------------------------------------------
def canonical_json(value: Any) -> bytes:
    """Stable bytes for hashing: sorted keys, no whitespace, UTF-8, money as exact strings."""
    return json.dumps(to_json(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _csv_cell(value: Any) -> str:
    """CSV text that a spreadsheet will not execute (free text is untrusted input)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(to_json(value)) if not isinstance(value, str) else value
    if text and text[0] in "=+-@\t\r" and not _is_number(text):
        return "'" + text
    return text


def _is_number(text: str) -> bool:
    try:
        return Decimal(text).is_finite()
    except (ArithmeticError, ValueError):
        return False


def to_csv(header: list[str], rows: list[dict]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([_csv_cell(row.get(col)) for col in header])
    return buffer.getvalue().encode("utf-8")


# --- Loading --------------------------------------------------------------------------------------
def load_case_material(tenant_id: str, case_id: str, include_audit: bool = False) -> dict:
    """Every record the checklist and the package are computed from, scoped to the tenant."""
    db = get_db()
    case = db.table("cases").select("*").eq("tenant_id", tenant_id).eq("id", case_id).single().execute().data
    evidence = db.table("case_evidence").select("*").eq("tenant_id", tenant_id).eq("case_id", case_id).order("created_at").order("id").execute().data
    case_alert_ids = [r["alert_id"] for r in db.table("case_alerts").select("alert_id").eq("tenant_id", tenant_id).eq("case_id", case_id).execute().data]

    alert_ids = set(case_alert_ids) | {e["alert_id"] for e in evidence if e["alert_id"]}
    alerts = fetch_by_ids("alerts", tenant_id, alert_ids, ALERT_FIELDS)
    signal_ids = {e["risk_signal_id"] for e in evidence if e["risk_signal_id"]}
    signals = fetch_by_ids("risk_signals", tenant_id, signal_ids, SIGNAL_FIELDS)

    txn_ids = {t for e in evidence for t in (e["transaction_ids"] or [])}
    txn_ids |= {t for s in signals for t in (s["evidence_transaction_ids"] or [])}
    transactions = fetch_by_ids("transactions", tenant_id, txn_ids, TXN_FIELDS)

    account_ids = {t["origin_account_id"] for t in transactions} | {t["destination_account_id"] for t in transactions}
    evidence_account_ids = {e["account_id"] for e in evidence if e["account_id"]}
    accounts = fetch_by_ids("accounts", tenant_id, account_ids | evidence_account_ids, ACCOUNT_FIELDS)

    run_ids = {a["model_run_id"] for a in alerts} | {s["model_run_id"] for s in signals}
    model_runs = fetch_by_ids("model_runs", tenant_id, run_ids, MODEL_RUN_FIELDS)

    row_ids = sorted({t["source_row_id"] for t in transactions if t.get("source_row_id")})
    dq_issues: list[dict] = []
    for start in range(0, len(row_ids), 150):
        batch = row_ids[start : start + 150]
        dq_issues += db.table("data_quality_issues").select(DQ_FIELDS).eq("tenant_id", tenant_id).in_("source_row_id", batch).execute().data
    evidence_dq_ids = {e["dq_issue_id"] for e in evidence if e["dq_issue_id"]}
    dq_issues += fetch_by_ids("data_quality_issues", tenant_id, evidence_dq_ids, DQ_FIELDS)
    dq_issues = list({i["id"]: i for i in dq_issues}.values())

    chunk_ids = sorted({e["policy_chunk_id"] for e in evidence if e["policy_chunk_id"]})
    policy_chunks = []
    if chunk_ids:
        rows = (db.table("policy_chunks").select("id, tenant_id, section, content, policy_documents(title, publisher, source_url)")
                .in_("id", chunk_ids).execute().data)
        policy_chunks = [c for c in rows if c["tenant_id"] in (None, tenant_id)]

    run_ingestion_ids = {t["ingestion_run_id"] for t in transactions if t.get("ingestion_run_id")}
    ingestion_runs = fetch_by_ids(
        "ingestion_runs", tenant_id, run_ingestion_ids,
        "id, schema_version, pipeline_version, status, completed_at, source_files(filename, checksum_sha256, source_system, data_owner)",
    )

    decisions = (db.table("case_decisions").select("id, decision, disposition, narrative_id, notes, decided_by, decided_at")
                 .eq("tenant_id", tenant_id).eq("case_id", case_id).order("decided_at").execute().data)
    narratives = (db.table("case_narratives")
                  .select("id, version, author_type, content, llm_model, prompt_version, verification_status, verified_at, created_at")
                  .eq("tenant_id", tenant_id).eq("case_id", case_id).order("version").execute().data)
    narrative_refs = {d["narrative_id"] for d in decisions if d["narrative_id"]}
    known_narratives = {n["id"] for n in narratives}

    material = {
        "case": case, "evidence": evidence, "alerts": alerts, "risk_signals": signals, "transactions": transactions,
        "accounts": accounts, "model_runs": model_runs, "data_quality_issues": dq_issues, "policy_chunks": policy_chunks,
        "ingestion_runs": ingestion_runs, "decisions": decisions, "narratives": narratives,
        "unresolved": _unresolved({
            "alerts": (alert_ids, {a["id"] for a in alerts}),
            "risk_signals": (signal_ids, {s["id"] for s in signals}),
            "transactions": (txn_ids, {t["id"] for t in transactions}),
            "accounts": (evidence_account_ids, {a["id"] for a in accounts}),
            "data_quality_issues": (evidence_dq_ids, {i["id"] for i in dq_issues}),
            "policy_chunks": (set(chunk_ids), {c["id"] for c in policy_chunks}),
            "narratives": (narrative_refs, known_narratives),
        }),
    }
    if include_audit:
        material["audit_events"] = (
            db.table("audit_events").select("event_id, actor_id, actor_type, action, occurred_at, metadata")
            .eq("tenant_id", tenant_id).eq("object_type", "case").eq("object_id", case_id)
            .not_.in_("action", list(AUDIT_READ_ACTIONS)).order("event_id").limit(MAX_AUDIT_EVENTS).execute().data
        )
    return material


def _unresolved(pairs: dict[str, tuple[set, set]]) -> dict[str, list[str]]:
    out = {}
    for name, (wanted, found) in pairs.items():
        missing = sorted({str(i) for i in wanted if i} - {str(i) for i in found})
        if missing:
            out[name] = missing
    return out


# --- Readiness checklist (PRD §23.20) -------------------------------------------------------------
def _item(key: str, label: str, status: Status, detail: str, refs: list[str] | None = None) -> dict:
    return {"key": key, "label": label, "status": status, "detail": detail, "evidence_refs": sorted(refs or [])}


def _notes_of_kind(evidence: list[dict], kind: str) -> list[dict]:
    return [e for e in evidence if e["evidence_type"] == "analyst_note" and (e.get("payload") or {}).get("kind") == kind]


def build_checklist(m: dict) -> list[dict]:
    """Complete / partial / missing per required evidence category, computed only from resolvable references."""
    case, evidence, transactions = m["case"], m["evidence"], m["transactions"]
    items = []

    # 1. Source transactions, each traceable to its raw source row.
    txn_ids = [t["id"] for t in transactions]
    unlinked = [t["id"] for t in transactions if not t.get("source_row_id")]
    if not transactions:
        items.append(_item("source_transactions", "Source transactions", "missing", "No transactions are attached to this case yet."))
    elif unlinked:
        items.append(_item("source_transactions", "Source transactions", "partial",
                           f"{len(transactions)} transaction(s) attached; {len(unlinked)} lack a source-row reference.", txn_ids))
    else:
        items.append(_item("source_transactions", "Source transactions", "complete",
                           f"{len(transactions)} transaction(s), each linked to its source row.", txn_ids))

    # 2. Path evidence: a saved transaction path, not just loose transactions.
    paths = [e for e in evidence if e["evidence_type"] == "transaction_path"]
    if paths:
        items.append(_item("path_evidence", "Path evidence", "complete", f"{len(paths)} saved transaction path(s).", [e["id"] for e in paths]))
    elif len({t["origin_account_id"] for t in transactions} | {t["destination_account_id"] for t in transactions}) >= 3:
        items.append(_item("path_evidence", "Path evidence", "partial",
                           "Transactions connect three or more accounts, but no graph path has been saved to the case.", txn_ids))
    else:
        items.append(_item("path_evidence", "Path evidence", "missing", "No saved transaction path."))

    # 3. Model and rule versions behind every alert and signal.
    runs = {r["id"]: r for r in m["model_runs"]}
    sources = m["alerts"] + m["risk_signals"]
    versioned = [s for s in sources if s.get("model_run_id") in runs and runs[s["model_run_id"]].get("model_version")]
    if not sources:
        items.append(_item("model_rule_version", "Model and rule versions", "missing", "No alerts or detector signals on the case."))
    elif len(versioned) < len(sources) or not m["risk_signals"]:
        detail = ("Alerts are attached but no component signals were recorded." if not m["risk_signals"]
                  else f"{len(sources) - len(versioned)} alert/signal output(s) have no versioned model run.")
        items.append(_item("model_rule_version", "Model and rule versions", "partial", detail, sorted(runs)))
    else:
        labels = sorted({f"{r['model_name']} {r['model_version']}" for r in runs.values()})
        items.append(_item("model_rule_version", "Model and rule versions", "complete", "Pinned: " + ", ".join(labels) + ".", sorted(runs)))

    # 4. Data-quality caveats reviewed.
    dq = m["data_quality_issues"]
    open_dq = [i for i in dq if i["status"] == "open"]
    reviewed_notes = _notes_of_kind(evidence, "dq_caveats_reviewed")
    if not transactions and not dq:
        items.append(_item("dq_caveats_reviewed", "Data-quality caveats reviewed", "missing", "No source transactions to check yet."))
    elif not dq:
        items.append(_item("dq_caveats_reviewed", "Data-quality caveats reviewed", "complete",
                           "No data-quality issues on the case's source rows."))
    elif reviewed_notes or not open_dq:
        refs = [i["id"] for i in dq] + [n["id"] for n in reviewed_notes]
        items.append(_item("dq_caveats_reviewed", "Data-quality caveats reviewed", "complete",
                           f"{len(dq)} issue(s) reviewed" + (" in an analyst note." if reviewed_notes else " by a data steward."), refs))
    elif len(open_dq) < len(dq):
        items.append(_item("dq_caveats_reviewed", "Data-quality caveats reviewed", "partial",
                           f"{len(open_dq)} of {len(dq)} data-quality issue(s) are still open.", [i["id"] for i in open_dq]))
    else:
        items.append(_item("dq_caveats_reviewed", "Data-quality caveats reviewed", "missing",
                           f"{len(dq)} open data-quality issue(s) affect this case's source rows.", [i["id"] for i in open_dq]))

    # 5. Policy citation: only a resolvable policy chunk counts. Never inferred.
    chunks = {c["id"] for c in m["policy_chunks"]}
    cited = [e for e in evidence if e["evidence_type"] == "policy_citation" and e["policy_chunk_id"] in chunks]
    if cited:
        items.append(_item("policy_citation", "Policy citation", "complete", f"{len(cited)} cited policy passage(s).",
                           [e["policy_chunk_id"] for e in cited]))
    else:
        items.append(_item("policy_citation", "Policy citation", "missing",
                           "No policy passage is cited. Policy retrieval attaches citations once it is connected."))

    # 6. Counter-evidence / benign explanation considered.
    counter = _notes_of_kind(evidence, "counter_evidence")
    if counter:
        items.append(_item("counter_evidence", "Counter-evidence and benign explanation", "complete",
                           f"{len(counter)} counter-evidence note(s).", [e["id"] for e in counter]))
    else:
        items.append(_item("counter_evidence", "Counter-evidence and benign explanation", "missing",
                           "Record why the activity might be legitimate, or why that explanation does not fit."))

    # 7. Human disposition by a reviewer.
    decisions = m["decisions"]
    if case.get("disposition"):
        refs = [d["id"] for d in decisions if d.get("disposition")]
        items.append(_item("human_disposition", "Human disposition", "complete",
                           f"Reviewer recorded: {case['disposition'].replace('_', ' ')}.", refs))
    elif decisions or case.get("escalation_requested_at") or case.get("status") in ("pending_review", "escalated"):
        items.append(_item("human_disposition", "Human disposition", "partial",
                           "Sent for review or a decision is in progress; no final disposition yet.", [d["id"] for d in decisions]))
    else:
        items.append(_item("human_disposition", "Human disposition", "missing", "No reviewer decision yet."))
    return items


def checklist_summary(items: list[dict]) -> dict:
    counts = {s: sum(1 for i in items if i["status"] == s) for s in ("complete", "partial", "missing")}
    return {"version": CHECKLIST_VERSION, "counts": counts, "ready": counts["complete"] == len(items), "total": len(items)}


# --- Package --------------------------------------------------------------------------------------
def _sorted(rows: list[dict], *keys: str) -> list[dict]:
    return sorted(rows, key=lambda r: tuple(str(r.get(k) or "") for k in keys))


def build_package(m: dict) -> tuple[dict[str, bytes], dict]:
    """Return ({file name: bytes}, manifest). Pure: same material in, same bytes out."""
    case = m["case"]
    accounts = {a["id"]: a for a in m["accounts"]}
    runs = {r["id"]: r for r in m["ingestion_runs"]}
    dq_by_row: dict[str, list[dict]] = {}
    for issue in m["data_quality_issues"]:
        if issue.get("source_row_id"):
            dq_by_row.setdefault(issue["source_row_id"], []).append(issue)

    transactions = _sorted(m["transactions"], "occurred_at", "id")
    schedule = []
    for t in transactions:
        run = runs.get(t.get("ingestion_run_id")) or {}
        source = run.get("source_files") or {}
        issues = dq_by_row.get(t.get("source_row_id") or "", [])
        schedule.append({
            **t,
            "origin_account_ref": (accounts.get(t["origin_account_id"]) or {}).get("account_ref"),
            "destination_account_ref": (accounts.get(t["destination_account_id"]) or {}).get("account_ref"),
            "source_file": source.get("filename"), "source_system": source.get("source_system"),
            "source_checksum_sha256": source.get("checksum_sha256"),
            "dq_issue_count": len(issues), "dq_issue_types": ";".join(sorted({i["issue_type"] for i in issues})),
        })
    schedule_cols = ["id", "transaction_ref", "occurred_at", "amount", "currency", "amount_usd", "channel", "transaction_status",
                     "origin_account_id", "origin_account_ref", "destination_account_id", "destination_account_ref", "description",
                     "source_row_id", "ingestion_run_id", "source_file", "source_system", "source_checksum_sha256",
                     "dq_issue_count", "dq_issue_types"]

    # Related entity table with exact-decimal flow totals per currency.
    flows: dict[str, dict] = {}
    for t in transactions:
        for account_id, direction in ((t["origin_account_id"], "out"), (t["destination_account_id"], "in")):
            f = flows.setdefault(account_id, {"in": {}, "out": {}, "count": 0})
            f["count"] += 1
            f[direction][t["currency"]] = f[direction].get(t["currency"], Decimal(0)) + Decimal(str(t["amount"]))
    entities = []
    for account_id in sorted(set(flows) | set(accounts)):
        a, f = accounts.get(account_id) or {"id": account_id}, flows.get(account_id, {"in": {}, "out": {}, "count": 0})
        entities.append({**a, "id": account_id, "transaction_count": f["count"],
                         "total_in": ";".join(f"{format(v, 'f')} {c}" for c, v in sorted(f["in"].items())),
                         "total_out": ";".join(f"{format(v, 'f')} {c}" for c, v in sorted(f["out"].items()))})
    entity_cols = ["id", "account_ref", "institution_ref", "is_internal", "account_type", "status", "jurisdiction",
                   "customer_risk_rating", "transaction_count", "total_in", "total_out"]

    dq_cols = ["id", "source_row_id", "run_id", "field_name", "issue_type", "severity", "rule_id", "message", "observed_value",
               "expected_value", "status", "remediation_note", "resolved_at", "created_at"]
    checklist = build_checklist(m)
    narratives = [{**{k: v for k, v in n.items() if k != "content"},
                   "content_sha256": sha256_hex((n.get("content") or "").encode("utf-8")), "content": n.get("content")}
                  for n in _sorted(m["narratives"], "version")]

    files: dict[str, bytes] = {
        "transactions.csv": to_csv(schedule_cols, schedule),
        "entities.csv": to_csv(entity_cols, entities),
        "data_quality_issues.csv": to_csv(dq_cols, _sorted(m["data_quality_issues"], "created_at", "id")),
        "detector_outputs.json": canonical_json({
            "model_runs": _sorted(m["model_runs"], "id"),
            "alerts": _sorted(m["alerts"], "alert_number"),
            "risk_signals": [{**s, "evidence_transaction_ids": sorted(s.get("evidence_transaction_ids") or [])}
                             for s in _sorted(m["risk_signals"], "signal_name", "id")],
        }),
        "evidence.json": canonical_json({
            "evidence": [{**e, "transaction_ids": sorted(e.get("transaction_ids") or [])} for e in _sorted(m["evidence"], "created_at", "id")],
            "policy_citations": _sorted(m["policy_chunks"], "id"),
        }),
        "narratives.json": canonical_json(narratives),
        "decisions.json": canonical_json(_sorted(m["decisions"], "decided_at", "id")),
        "audit_timeline.json": canonical_json(m.get("audit_events", [])),
    }
    rows = {"transactions.csv": len(schedule), "entities.csv": len(entities), "data_quality_issues.csv": len(m["data_quality_issues"])}
    file_entries = [{"name": name, "sha256": sha256_hex(data), "bytes": len(data),
                     "content_type": "text/csv" if name.endswith(".csv") else "application/json",
                     **({"rows": rows[name]} if name in rows else {})} for name, data in sorted(files.items())]

    model_runs = _sorted(m["model_runs"], "id")
    lineage = [{"ingestion_run_id": r["id"], "schema_version": r.get("schema_version"), "pipeline_version": r.get("pipeline_version"),
                "completed_at": r.get("completed_at"), **{f"source_{k}": v for k, v in (r.get("source_files") or {}).items()}}
               for r in _sorted(m["ingestion_runs"], "id")]
    watermarks = sorted(r["completed_at"] for r in m["ingestion_runs"] if r.get("completed_at"))
    latest_narrative = narratives[-1] if narratives else None
    missing = []
    if not latest_narrative:
        missing.append("narrative: no narrative revision drafted")
    if not m["policy_chunks"]:
        missing.append("policy_citations: no policy passage cited")
    if len(m.get("audit_events", [])) >= MAX_AUDIT_EVENTS:
        missing.append(f"audit_timeline: truncated to the first {MAX_AUDIT_EVENTS} events")
    missing.append("graph_snapshot:graph snapshots are not persisted yet; path evidence is the saved transaction ids")

    manifest = {
        "schema": PACKAGE_SCHEMA,
        "artifact": "Human-reviewed SAR-support evidence package. A case-support artifact, not a FinCEN filing.",
        "case": {k: case.get(k) for k in ("id", "case_number", "title", "status", "priority_band", "assigned_to", "due_at",
                                          "disposition", "disposed_by", "disposed_at", "created_at")},
        "source_watermark": watermarks[-1] if watermarks else None,
        "source_lineage": lineage,
        "versions": {
            "model_runs": [{k: r.get(k) for k in ("id", "model_name", "model_kind", "model_version", "feature_set_version", "dataset_version")}
                           for r in model_runs],
            "feature_set_versions": sorted({r["feature_set_version"] for r in model_runs if r.get("feature_set_version")}),
            "signal_versions": sorted({f"{s['signal_name']}@{s['signal_version']}" for s in m["risk_signals"]}),
            "policy_chunk_ids": sorted(c["id"] for c in m["policy_chunks"]),
            "narrative_revision": latest_narrative["version"] if latest_narrative else None,
            "narrative_sha256": latest_narrative["content_sha256"] if latest_narrative else None,
            "graph_snapshot": None,
            "checklist": CHECKLIST_VERSION,
        },
        "evidence_refs": {
            "transaction_ids": sorted(t["id"] for t in m["transactions"]),
            "alert_ids": sorted(a["id"] for a in m["alerts"]),
            "account_ids": sorted(a["id"] for a in m["accounts"]),
            "risk_signal_ids": sorted(s["id"] for s in m["risk_signals"]),
            "dq_issue_ids": sorted(i["id"] for i in m["data_quality_issues"]),
            "decision_ids": sorted(d["id"] for d in m["decisions"]),
            "narrative_ids": sorted(n["id"] for n in m["narratives"]),
        },
        "checklist": [{"key": i["key"], "status": i["status"]} for i in checklist],
        "files": file_entries,
        "missing": missing,
        "notes": [
            "Synthetic data. Signals indicate patterns for review, not proof of wrongdoing.",
            "Free-text fields are untrusted input. CSV cells that start with a formula character are prefixed with an apostrophe.",
            "Hashes show tamper evidence. Storage is not claimed to be immutable.",
            "Read and export audit events are excluded from audit_timeline.json so an unchanged case re-exports to the same hashes.",
        ],
    }
    files["manifest.json"] = canonical_json(manifest)
    return files, manifest


def sha256sums(files: dict[str, bytes]) -> bytes:
    """`sha256sum`-compatible listing of every file; its hash is the package hash."""
    return "".join(f"{sha256_hex(data)}  {name}\n" for name, data in sorted(files.items())).encode("utf-8")
