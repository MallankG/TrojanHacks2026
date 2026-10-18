"""Readiness checklist, evidence-package hashing/determinism and the four-eyes rule (no database needed)."""

import copy
import csv
import io
import json
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.security import CurrentUser, get_current_user
from app.main import app
from app.services import evidence_package as pkg
from app.services.cases import check_four_eyes

TENANT = "11111111-1111-1111-1111-111111111111"
CASE = "22222222-2222-2222-2222-222222222222"
ANALYST = "33333333-3333-3333-3333-333333333333"
REVIEWER = "44444444-4444-4444-4444-444444444444"
REVIEWER_2 = "55555555-5555-5555-5555-555555555555"


def _material(**overrides) -> dict:
    txns = [
        {"id": f"t{i}", "transaction_ref": f"TX-{i}", "occurred_at": f"2026-07-0{i}T10:00:00+00:00", "amount": "9500.00",
         "currency": "USD", "amount_usd": "9500.00", "channel": "wire", "description": "=HYPERLINK(\"x\")" if i == 1 else "rent",
         "transaction_status": "posted", "origin_account_id": f"a{i}", "destination_account_id": f"a{i + 1}",
         "source_row_id": f"r{i}", "ingestion_run_id": "run1"}
        for i in (1, 2)
    ]
    material = {
        "case": {"id": CASE, "case_number": 7, "title": "[test-cases] fixture", "status": "open", "priority_band": "high",
                 "assigned_to": ANALYST, "due_at": None, "disposition": None, "disposed_by": None, "disposed_at": None,
                 "created_at": "2026-07-01T00:00:00+00:00", "updated_at": "2026-07-09T00:00:00+00:00",
                 "escalation_requested_at": None},
        "evidence": [
            {"id": "e1", "evidence_type": "transaction", "transaction_ids": ["t2", "t1"], "alert_id": "al1", "account_id": None,
             "risk_signal_id": None, "dq_issue_id": None, "policy_chunk_id": None, "explanation": "supporting",
             "payload": {}, "created_at": "2026-07-02T00:00:00+00:00"},
        ],
        "alerts": [{"id": "al1", "alert_number": 12, "typology": "structuring", "model_run_id": "m1"}],
        "risk_signals": [{"id": "s1", "model_run_id": "m1", "signal_name": "structuring_rule", "signal_version": "3.2",
                          "evidence_transaction_ids": ["t1"]}],
        "transactions": txns,
        "accounts": [{"id": f"a{i}", "account_ref": f"ACC-{i}"} for i in (1, 2, 3)],
        "model_runs": [{"id": "m1", "model_name": "typology-rules", "model_kind": "rule_engine", "model_version": "3.2"}],
        "data_quality_issues": [],
        "policy_chunks": [],
        "ingestion_runs": [{"id": "run1", "schema_version": "canonical-txn-v1", "completed_at": "2026-07-01T00:00:00+00:00",
                            "source_files": {"filename": "demo.csv", "checksum_sha256": "ab" * 32}}],
        "decisions": [],
        "narratives": [],
        "audit_events": [{"event_id": 1, "action": "case.created", "occurred_at": "2026-07-01T00:00:00+00:00"}],
        "unresolved": {},
    }
    material.update(overrides)
    return material


def _status(items: list[dict]) -> dict[str, str]:
    return {i["key"]: i["status"] for i in items}


# --- Checklist ---------------------------------------------------------------------------------
def test_empty_case_is_all_missing():
    empty = _material(evidence=[], alerts=[], risk_signals=[], transactions=[], accounts=[], model_runs=[])
    assert set(_status(pkg.build_checklist(empty)).values()) == {"missing"}
    assert pkg.checklist_summary(pkg.build_checklist(empty))["ready"] is False


def test_checklist_reflects_real_evidence():
    status = _status(pkg.build_checklist(_material()))
    assert status["source_transactions"] == "complete"
    assert status["path_evidence"] == "partial"  # three accounts connected, no saved path
    assert status["model_rule_version"] == "complete"
    assert status["dq_caveats_reviewed"] == "complete"  # no issues on the source rows
    assert status["policy_citation"] == "missing"  # never faked without a resolvable chunk
    assert status["counter_evidence"] == "missing"
    assert status["human_disposition"] == "missing"


def test_policy_citation_requires_resolvable_chunk():
    cite = {"id": "e9", "evidence_type": "policy_citation", "policy_chunk_id": "c1", "transaction_ids": [], "payload": {}}
    m = _material()
    m["evidence"] = m["evidence"] + [cite]
    assert _status(pkg.build_checklist(m))["policy_citation"] == "missing"
    m["policy_chunks"] = [{"id": "c1"}]
    assert _status(pkg.build_checklist(m))["policy_citation"] == "complete"


def test_dq_counter_evidence_and_disposition_progress():
    issues = [{"id": "q1", "source_row_id": "r1", "status": "open", "issue_type": "missing_field"},
              {"id": "q2", "source_row_id": "r2", "status": "acknowledged", "issue_type": "future_date"}]
    m = _material(data_quality_issues=issues)
    assert _status(pkg.build_checklist(m))["dq_caveats_reviewed"] == "partial"
    m["data_quality_issues"] = [{**issues[0]}]
    assert _status(pkg.build_checklist(m))["dq_caveats_reviewed"] == "missing"

    note = lambda kind, i: {"id": i, "evidence_type": "analyst_note", "transaction_ids": [], "payload": {"kind": kind},  # noqa: E731
                            "policy_chunk_id": None}
    m["evidence"] = m["evidence"] + [note("dq_caveats_reviewed", "n1"), note("counter_evidence", "n2")]
    status = _status(pkg.build_checklist(m))
    assert status["dq_caveats_reviewed"] == "complete" and status["counter_evidence"] == "complete"

    m["case"] = {**m["case"], "status": "pending_review"}
    assert _status(pkg.build_checklist(m))["human_disposition"] == "partial"
    m["case"] = {**m["case"], "disposition": "monitor"}
    assert _status(pkg.build_checklist(m))["human_disposition"] == "complete"


def test_signals_without_versioned_runs_are_partial():
    m = _material(model_runs=[])
    assert _status(pkg.build_checklist(m))["model_rule_version"] == "partial"


# --- Package hashing ---------------------------------------------------------------------------
def test_package_is_deterministic_and_order_independent():
    files_a, manifest_a = pkg.build_package(_material())
    shuffled = _material()
    for key in ("transactions", "accounts", "evidence"):
        shuffled[key] = list(reversed(shuffled[key]))
    files_b, _ = pkg.build_package(shuffled)
    assert files_a == files_b
    assert pkg.sha256sums(files_a) == pkg.sha256sums(files_b)
    # The manifest lists every other file with its hash.
    listed = {f["name"]: f["sha256"] for f in manifest_a["files"]}
    assert listed == {n: pkg.sha256_hex(d) for n, d in files_a.items() if n != "manifest.json"}
    assert json.loads(files_a["manifest.json"]) == json.loads(pkg.canonical_json(manifest_a))


def test_new_note_changes_hashes_but_volatile_fields_do_not():
    base = pkg.sha256_hex(pkg.build_package(_material())[0]["manifest.json"])
    touched = _material()
    touched["case"] = {**touched["case"], "updated_at": "2026-08-01T00:00:00+00:00"}
    assert pkg.sha256_hex(pkg.build_package(touched)[0]["manifest.json"]) == base

    noted = _material()
    noted["evidence"] = noted["evidence"] + [{"id": "e2", "evidence_type": "analyst_note", "transaction_ids": [], "payload": {},
                                              "explanation": "new note", "created_at": "2026-07-03T00:00:00+00:00"}]
    files, _ = pkg.build_package(noted)
    assert pkg.sha256_hex(files["manifest.json"]) != base


def test_transaction_schedule_csv_is_safe_and_exact():
    files, manifest = pkg.build_package(_material())
    rows = list(csv.DictReader(io.StringIO(files["transactions.csv"].decode())))
    assert [r["id"] for r in rows] == ["t1", "t2"]
    assert rows[0]["description"].startswith("'=")  # formula neutralised
    assert rows[0]["amount"] == "9500.00"
    assert rows[0]["source_file"] == "demo.csv"
    entities = list(csv.DictReader(io.StringIO(files["entities.csv"].decode())))
    assert {e["id"]: e["total_in"] for e in entities}["a2"] == "9500.00 USD"
    assert "graph_snapshot" in " ".join(manifest["missing"])
    assert manifest["artifact"].endswith("not a FinCEN filing.")


def test_csv_cell_keeps_negative_numbers():
    assert pkg._csv_cell("-12.50") == "-12.50"
    assert pkg._csv_cell("-cmd") == "'-cmd"
    assert pkg._csv_cell("@SUM(A1)") == "'@SUM(A1)"


# --- Four-eyes ---------------------------------------------------------------------------------
def _user(uid: str, role: str) -> CurrentUser:
    return CurrentUser(id=uid, tenant_id=TENANT, role=role)


def test_four_eyes_blocks_the_requester_only():
    case = {"escalation_requested_by": REVIEWER}
    with pytest.raises(HTTPException) as exc:
        check_four_eyes(case, _user(REVIEWER, "reviewer"), "escalate", None)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException):
        check_four_eyes(case, _user(REVIEWER, "reviewer"), "accept", "monitor")
    check_four_eyes(case, _user(REVIEWER, "reviewer"), "edit", None)  # non-final edits are fine
    check_four_eyes(case, _user(REVIEWER_2, "reviewer"), "escalate", "escalate_for_sar_consideration")
    check_four_eyes({"escalation_requested_by": None}, _user(REVIEWER, "reviewer"), "escalate", None)


@pytest.fixture
def as_user():
    def use(user: CurrentUser):
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app)

    yield use
    app.dependency_overrides.clear()


def test_analyst_cannot_dispose(as_user):
    client = as_user(_user(ANALYST, "analyst"))
    with patch("app.api.routes.cases.get_one", return_value={"id": CASE, "status": "open", "escalation_requested_by": None}):
        response = client.post(f"/cases/{CASE}/decisions", json={"decision": "accept", "disposition": "monitor", "notes": "x"})
    assert response.status_code == 403


def test_requester_cannot_approve_own_escalation(as_user):
    client = as_user(_user(REVIEWER, "reviewer"))
    case = {"id": CASE, "status": "pending_review", "escalation_requested_by": REVIEWER}
    with patch("app.api.routes.cases.get_one", return_value=case), patch("app.api.routes.cases.insert_many") as insert:
        response = client.post(f"/cases/{CASE}/decisions", json={"decision": "escalate", "notes": "mine"})
    assert response.status_code == 403
    assert "Four-eyes" in response.json()["detail"]
    insert.assert_not_called()


def test_other_reviewer_can_decide(as_user):
    client = as_user(_user(REVIEWER_2, "reviewer"))
    case = {"id": CASE, "status": "pending_review", "escalation_requested_by": REVIEWER}
    decision = {"id": "d1", "decision": "escalate"}
    with patch("app.api.routes.cases.get_one", return_value=case), \
            patch("app.api.routes.cases._active_agent_run", return_value=False), \
            patch("app.api.routes.cases._latest_narrative", return_value=[]), \
            patch("app.api.routes.cases.insert_many", return_value=[decision]):
        response = client.post(f"/cases/{CASE}/decisions", json={"decision": "escalate", "notes": "agree"})
    assert response.status_code == 201


def test_decision_blocked_while_agent_review_active(as_user):
    client = as_user(_user(REVIEWER_2, "reviewer"))
    case = {"id": CASE, "status": "pending_review", "escalation_requested_by": REVIEWER}
    with patch("app.api.routes.cases.get_one", return_value=case), \
            patch("app.api.routes.cases._active_agent_run", return_value=True), \
            patch("app.api.routes.cases.insert_many") as insert:
        response = client.post(f"/cases/{CASE}/decisions", json={"decision": "escalate", "notes": "agree"})
    assert response.status_code == 409
    insert.assert_not_called()


def test_final_decision_needs_passed_latest_narrative(as_user):
    client = as_user(_user(REVIEWER_2, "reviewer"))
    case = {"id": CASE, "status": "pending_review", "escalation_requested_by": REVIEWER}
    with patch("app.api.routes.cases.get_one", return_value=case), \
            patch("app.api.routes.cases._active_agent_run", return_value=False), \
            patch("app.api.routes.cases._latest_narrative", return_value=[{"id": CASE, "verification_status": "failed"}]), \
            patch("app.api.routes.cases.insert_many") as insert:
        response = client.post(f"/cases/{CASE}/decisions", json={"decision": "accept", "notes": "ok"})
    assert response.status_code == 422
    insert.assert_not_called()


def test_export_refuses_unresolved_references(as_user):
    client = as_user(_user(ANALYST, "analyst"))
    material = _material(unresolved={"transactions": ["t-missing"]})
    with patch("app.api.routes.case_workflow.get_one", return_value={"id": CASE}), \
            patch("app.services.evidence_package.load_case_material", return_value=copy.deepcopy(material)):
        response = client.post(f"/cases/{CASE}/evidence-export")
    assert response.status_code == 409
    assert response.json()["detail"]["unresolved_refs"] == {"transactions": ["t-missing"]}


def test_due_at_must_be_timezone_aware(as_user):
    client = as_user(_user(ANALYST, "analyst"))
    with patch("app.api.routes.cases.get_one", return_value={"id": CASE, "status": "open"}):
        response = client.patch(f"/cases/{CASE}", json={"due_at": "2026-11-01T12:00:00"})
    assert response.status_code == 422


def test_escalation_request_requires_reason(as_user):
    client = as_user(_user(ANALYST, "analyst"))
    assert client.post(f"/cases/{CASE}/escalation-request", json={"reason": ""}).status_code == 422
