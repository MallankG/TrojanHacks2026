import copy
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.services import passport

TENANT = "11111111-1111-1111-1111-111111111111"
HEADER = {"format": passport.FORMAT, "subject": {"type": "case", "id": "c1"}, "issued_at": "2026-10-09T14:10:00+00:00",
          "issued_by": "u1"}


def _sections() -> dict:
    bundle = {
        "transactions": [{"id": "t1", "transaction_ref": "TX-88201", "occurred_at": "2026-10-06T09:14:00+00:00",
                          "amount": "58200.0000", "currency": "USD", "source_row_id": "r1", "ingestion_run_id": "ir1"}],
        "data_quality_issues": [{"id": "dq1", "source_row_id": "r9", "issue_type": "missing_required_field",
                                 "field_name": "target_account", "severity": "error", "status": "open"}],
        "alerts": [{"id": "a1", "model_run_id": "m1", "priority_band": "review_first", "rank_score": "0.910000",
                    "ranking_strategy": "configurable_weights_v3.1"}],
        "risk_signals": [{"id": "s1", "model_run_id": "m1", "signal_name": "xgb", "signal_version": "1.4.2", "score": "0.91",
                          "threshold": "0.62", "triggered": True, "details": {"contributions": {"out_in_ratio_24h": 0.84}}}],
        "evidence": [{"id": "e1", "evidence_type": "transaction_path", "transaction_ids": ["t1"], "explanation": "path"},
                     {"id": "e2", "evidence_type": "analyst_note", "transaction_ids": [], "explanation": "note"}],
        "policy_citations": [{"id": "p1", "section": "SAR", "content": "text",
                              "policy_documents": {"source_url": "https://bsaaml.ffiec.gov/manual"}}],
    }
    runs = [{"id": "m1", "model_name": "xgb-aml", "model_kind": "supervised", "model_version": "1.4.2"}]
    narrative = {"id": "n1", "version": 2, "verification_status": "passed"}
    claims = [{"claim_index": 0, "claim_text": "$58,200.00 entered the path.", "claim_kind": "fact", "verification": "supported",
               "referenced_transaction_ids": ["t1"]}]
    decisions = [{"id": "d1", "decision": "escalate", "disposition": None, "decided_by": "u2",
                  "decided_at": "2026-10-09T14:20:00+00:00"}]
    return passport.build_sections(bundle, runs, narrative, claims, decisions)


def test_build_sections_maps_bundle():
    sections = _sections()
    assert set(sections) == set(passport.SECTIONS)
    assert sections["source_references"][0]["source_row_id"] == "r1"
    assert [p["evidence_id"] for p in sections["graph_paths"]] == ["e1"]
    assert sections["feature_contributions"][0]["contributions"] == {"out_in_ratio_24h": 0.84}
    assert sections["copilot_claims"]["claims"][0]["transaction_ids"] == ["t1"]


def test_seal_then_verify_round_trip():
    sealed = passport.seal(HEADER, _sections())
    assert sealed["signature"] is None
    result = passport.verify(sealed)
    assert result["valid"] and result["package_hash_matches"]
    assert all(r["status"] == "match" for r in result["sections"])


def test_verify_names_the_modified_section():
    sealed = passport.seal(HEADER, _sections())
    tampered = copy.deepcopy(sealed)
    tampered["sections"]["source_references"][0]["amount"] = "158200.0000"
    result = passport.verify(tampered)
    assert not result["valid"]
    statuses = {r["section"]: r["status"] for r in result["sections"]}
    assert statuses["source_references"] == "modified"
    assert [s for s, v in statuses.items() if v != "match"] == ["source_references"]


def test_verify_detects_header_and_manifest_edits():
    sealed = passport.seal(HEADER, _sections())
    header_edit = {**copy.deepcopy(sealed), "issued_by": "someone-else"}
    assert not passport.verify(header_edit)["valid"]
    # Rewriting a section hash to match edited content still breaks the package hash.
    forged = copy.deepcopy(sealed)
    forged["sections"]["human_decisions"] = []
    forged["manifest"]["sections"]["human_decisions"] = passport.sha256_hex([])
    result = passport.verify(forged)
    assert not result["valid"] and not result["package_hash_matches"]


def test_verify_rejects_foreign_documents_and_missing_sections():
    assert not passport.verify({"format": "other"})["valid"]
    sealed = passport.seal(HEADER, _sections())
    del sealed["sections"]["model"]
    statuses = {r["section"]: r["status"] for r in passport.verify(sealed)["sections"]}
    assert statuses["model"] == "missing"


def test_canonical_hash_vector_shared_with_frontend():
    # frontend/src/lib/domain/passport.ts must produce the same digest for the same value.
    value = {"b": [1, "two", None, True], "a": {"z": "é", "y": "58200.00"}}
    assert passport.canonical_json(value) == '{"a":{"y":"58200.00","z":"é"},"b":[1,"two",null,true]}'
    assert passport.sha256_hex(value) == "28a508364b4cf3bca025de943bfe5c03713b93581327230eb974aa0291d52940"


def test_verify_endpoint_is_stateless():
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [{"id": TENANT}]
    sealed = passport.seal(HEADER, _sections())
    with patch("app.core.security.get_db", return_value=db):
        response = TestClient(app).post("/evidence-passports/verify", json=sealed,
                                        headers={"X-Service-Key": "test-worker-key", "X-Tenant-Id": TENANT})
    assert response.status_code == 200
    assert response.json()["valid"] is True


def test_ecmascript_numeric_and_utf16_canonicalization():
    value = {"numbers": [1.0, 1e-7, 1e-6, -0.0, 1e20], "\ue000": 1, "\U0001f600": 2}
    assert passport.canonical_json(value) == '{"numbers":[1,1e-7,0.000001,0,100000000000000000000],"😀":2,"\ue000":1}'


def test_malformed_passport_containers_are_invalid():
    for field, value in [("manifest", "edited"), ("manifest", []), ("sections", []), ("sections", "edited")]:
        doc = passport.seal(HEADER, _sections())
        doc[field] = value
        assert passport.verify(doc)["valid"] is False
    doc = passport.seal(HEADER, _sections())
    doc["manifest"]["sections"] = ["edited"]
    assert passport.verify(doc)["valid"] is False
    doc = passport.seal(HEADER, _sections())
    doc["sections"]["model"] = float("inf")
    assert passport.verify(doc)["valid"] is False
