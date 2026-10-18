from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.core.security import CurrentUser, get_current_user
from app.services.governance import Contract, Evaluation, FieldRule, Product, check_row, demo, evaluate, impact

NOW = datetime(2026, 10, 18, 5, tzinfo=UTC)
TENANT = "11111111-1111-1111-1111-111111111111"
ID = "22222222-2222-2222-2222-222222222222"


def fixture():
    data = demo("healthy")
    product = Product.model_validate(data["product"])
    request = Evaluation.model_validate(data["input"])
    for row in request.rows:
        row["timestamp"] = NOW.isoformat()
    return product, request


def test_healthy_receipt_exposes_unknown_provenance():
    product, request = fixture()
    result = evaluate(product, request, NOW)
    assert result["summary"] == {"total": 12, "accepted": 12, "quarantined": 0}
    assert result["trust"]["components"]["provenance"] is None
    assert result["trust"]["coverage"] == .8
    assert not result["incidents"]
    assert len(result["receipt_sha256"]) == 64
    assert result == evaluate(product, request, NOW)


def test_drift_quarantine_and_reconciliation_are_connected():
    data = demo("drift")
    result = data["assessment"]
    assert result["summary"]["quarantined"] == 6
    assert {d["kind"] for d in result["drift"]} >= {"enum", "timezone_or_type"}
    assert result["reconciliation"]["rows"][0]["difference"] == "-600"
    assert result["impact"]["counts"]["alert"] == 1
    assert result["incidents"][0]["owner"] == data["product"]["steward"]


@pytest.mark.parametrize("value,code", [("NaN", "type"), (True, "type"), ("-1", "range"), ("garbage", "type")])
def test_strict_amount_contract(value, code):
    product, request = fixture()
    request.rows[0]["amount"] = value
    failures = check_row(request.rows[0], product.contract, NOW)
    assert {f["code"] for f in failures} == {code}


def test_required_optional_referential_and_freshness():
    contract = Contract(version="v1", fields={
        "id": FieldRule(references=["known"]), "optional": FieldRule(required=False),
        "timestamp": FieldRule(kind="timestamp")})
    failures = check_row({"id": "foreign", "timestamp": (NOW - timedelta(days=2)).isoformat()}, contract, NOW)
    assert {f["code"] for f in failures} == {"referential", "freshness"}


def test_unit_shift_quarantines_without_coercing():
    product, request = fixture()
    request.observed_unit = "minor"
    before = deepcopy(request.rows)
    result = evaluate(product, request, NOW)
    assert result["summary"]["quarantined"] == 12
    assert request.rows == before
    assert any(d["kind"] == "currency_unit_shift" for d in result["drift"])


def test_distribution_shift_needs_ten_observations():
    product, request = fixture()
    request.baseline = deepcopy(request.rows)
    for row in request.rows:
        row["amount"] = "10000"
    assert any(d["kind"] == "distribution_shift" for d in evaluate(product, request, NOW)["drift"])
    request.rows = request.rows[:9]
    assert not any(d["kind"] == "distribution_shift" for d in evaluate(product, request, NOW)["drift"])


def test_missing_field_proposes_review_without_remapping():
    product, request = fixture()
    for row in request.rows:
        row["destination_acct"] = row.pop("destination_account")
    result = evaluate(product, request, NOW)
    assert result["summary"]["accepted"] == 0
    assert result["drift"][0]["requires_review"]
    assert result["drift"][0]["proposed_mapping"] == "destination_acct"


def test_receipts_do_not_contain_raw_sensitive_values():
    import json
    product, request = fixture()
    request.rows[0]["destination_account"] = "TOP-SECRET-ACCOUNT"
    request.document = "private@example.com and 123-45-6789"
    result = evaluate(product, request, NOW)
    assert "TOP-SECRET" not in json.dumps(result)
    assert "private@example" not in json.dumps(result)
    assert result["sensitive"]["document_matches"] == 2
    assert result["sensitive"]["prompt_warning"]


def test_impact_traversal_handles_cycles_and_unknown_fields():
    product, _ = fixture()
    from app.services.governance import MetadataEdge
    product.edges.append(MetadataEdge(source="outcome", target="feature"))
    assert impact(product, ["amount"])["total"] == 7
    assert impact(product, ["unknown"])["total"] == 0


def test_empty_dataset_is_unknown_not_perfect():
    product, _ = fixture()
    result = evaluate(product, Evaluation(rows=[]), NOW)
    assert result["trust"]["score"] is None
    assert result["trust"]["coverage"] == 0


def test_payload_and_graph_bounds():
    with pytest.raises(ValidationError):
        Evaluation(rows=[{}] * 501)
    with pytest.raises(ValidationError):
        Evaluation(rows=[{"large": "x" * 1001}])
    data = demo()["product"]
    data["edges"].append({"source": "unknown", "target": "model"})
    with pytest.raises(ValidationError):
        Product.model_validate(data)


@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(ID, TENANT, "data_steward")
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_live_endpoints_require_auth_and_demo_is_public():
    client = TestClient(app)
    assert client.get("/governance/products").status_code == 401
    assert client.get("/governance/demo?scenario=healthy").json()["persisted"] is False


def test_catalog_reads_are_tenant_scoped(client):
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = []
    with patch("app.api.routes.governance.get_db", return_value=db):
        assert client.get("/governance/products").status_code == 200
    db.table.return_value.select.return_value.eq.assert_called_once_with("tenant_id", TENANT)


def test_preview_does_not_execute_or_audit_and_rejects_sql(client):
    product, _ = fixture()
    with patch("app.api.routes.governance.product_for", return_value=({}, product)), patch("app.api.routes.governance.event") as write:
        result = client.post(f"/governance/products/{ID}/steward-query", json={"question": "which alerts depend on amount?"})
        assert result.status_code == 200
        assert result.json()["plan"]["tenant_id"] == TENANT
        assert result.json()["executed"] is False
        write.assert_not_called()
        assert client.post(f"/governance/products/{ID}/steward-query", json={"question": "DROP TABLE transactions"}).status_code == 422


def test_execute_query_is_audited_and_capped(client):
    product, _ = fixture()
    with patch("app.api.routes.governance.product_for", return_value=({}, product)), patch("app.api.routes.governance.event") as write:
        result = client.post(f"/governance/products/{ID}/steward-query", json={"question": "which alerts depend on amount?", "execute": True, "limit": 1})
        assert result.status_code == 200
        assert len(result.json()["items"]) == 1
        write.assert_called_once()
        assert client.post(f"/governance/products/{ID}/steward-query", json={"question": "which alerts depend on amount?", "limit": 101}).status_code == 422


def test_steward_cannot_approve_and_expiry_is_required(client):
    product, _ = fixture()
    body = {"kind": "waiver", "note": "Synthetic test rationale", "owner": "Steward", "decision": "approved", "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()}
    with patch("app.api.routes.governance.product_for", return_value=({}, product)):
        assert client.post(f"/governance/products/{ID}/reviews", json=body).status_code == 403
        body["decision"] = "requested"
        body["expires_at"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        assert client.post(f"/governance/products/{ID}/reviews", json=body).status_code == 422


def test_quarantine_query_cannot_read_other_product(client):
    product, _ = fixture()
    with patch("app.api.routes.governance.product_for", return_value=({}, product)), patch("app.api.routes.governance.get_one", return_value={"product_id": TENANT, "kind": "assessment"}):
        result = client.post(f"/governance/products/{ID}/steward-query", json={"question": "why was this row quarantined?", "row": 1, "assessment_id": ID, "execute": True})
        assert result.status_code == 404



def test_ingestion_contract_quarantines_before_canonical_writes():
    from types import SimpleNamespace
    from app.services import ingestion
    product, request = fixture()
    raw = request.rows[0]
    raw.update({"origin_account": "ACC-ORIGIN", "channel": "wire", "timestamp": NOW.replace(tzinfo=None).isoformat()})
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = [{"id": ID, "definition": product.model_dump(mode="json")}]
    saved = {}

    def insert(table, rows):
        saved[table] = rows
        return [dict(r, id=ID) for r in rows]

    with patch.object(ingestion, "get_db", return_value=db), patch.object(ingestion, "get_settings", return_value=SimpleNamespace(governance_contracts_enabled=True)), patch.object(ingestion, "_existing_transaction_refs", return_value=set()), patch.object(ingestion, "insert_many", side_effect=insert), patch.object(ingestion, "_resolve_accounts", return_value={}) as accounts, patch.object(ingestion, "summarize_run", return_value={}):
        ingestion._process_rows(TENANT, ID, {"source_system": "demo-payments", "checksum_sha256": "a" * 64}, list(raw), {k: k for k in raw}, [raw], True)
    product_query = db.table.return_value.select.return_value.eq.return_value.eq.return_value
    product_query.order.assert_called_once_with("revision", desc=True)
    db.table.return_value.update.assert_any_call({"governance_product_id": ID})
    assert saved["transactions"] == []
    assert saved["source_rows"][0]["status"] == "rejected"
    assert any(i["rule_id"] == "GOV-CONTRACT-001" for i in saved["data_quality_issues"])
    accounts.assert_called_once_with(TENANT, ID, [], True)


def test_assessment_requires_registered_purpose(client):
    product, request = fixture()
    with patch("app.api.routes.governance.product_for", return_value=({}, product)), patch("app.api.routes.governance.event") as write:
        result = client.post(f"/governance/products/{ID}/assessments", json={**request.model_dump(mode="json"), "purpose": "Unrelated marketing"})
        assert result.status_code == 403
        write.assert_not_called()


def test_reviewer_approval_must_reference_unexpired_request(client):
    product, _ = fixture()
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(ID, TENANT, "reviewer")
    expires = datetime.now(UTC) + timedelta(days=2)
    body = {"kind": "waiver", "note": "Reviewer test rationale", "owner": "Steward", "decision": "approved", "expires_at": expires.isoformat()}
    with patch("app.api.routes.governance.product_for", return_value=({}, product)), patch("app.api.routes.governance.event", return_value={"id": ID}) as write:
        assert client.post(f"/governance/products/{ID}/reviews", json=body).status_code == 422
        body["request_id"] = ID
        with patch("app.api.routes.governance.get_one", return_value={"product_id": ID, "kind": "waiver", "payload": {"decision": "requested", "expires_at": expires.isoformat()}}):
            assert client.post(f"/governance/products/{ID}/reviews", json=body).status_code == 201
        write.assert_called_once()
