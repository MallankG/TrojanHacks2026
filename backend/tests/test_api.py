"""API-level checks that do not need a database: auth gates, request validation and OpenAPI generation."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
TENANT = "11111111-1111-1111-1111-111111111111"


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_openapi_builds():
    spec = client.get("/openapi.json").json()
    assert "/cases/{case_id}/evidence-bundle" in spec["paths"]


def test_requires_auth():
    assert client.get("/alerts").status_code == 401
    assert client.get("/me", headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401


def test_rejects_wrong_service_key():
    response = client.get("/alerts", headers={"X-Service-Key": "nope", "X-Tenant-Id": TENANT})
    assert response.status_code == 401


def _service_db():
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [{"id": TENANT}]
    return db


def test_service_key_cannot_record_human_decisions():
    with patch("app.core.security.get_db", return_value=_service_db()):
        response = client.post(
            f"/cases/{TENANT}/decisions",
            headers={"X-Service-Key": "test-worker-key", "X-Tenant-Id": TENANT},
            json={"decision": "accept", "disposition": "monitor"},
        )
    assert response.status_code == 403


def test_service_key_requires_tenant_uuid():
    response = client.get("/alerts", headers={"X-Service-Key": "test-worker-key", "X-Tenant-Id": "bad"})
    assert response.status_code == 400


def test_body_validation():
    with patch("app.core.security.get_db", return_value=_service_db()):
        headers = {"X-Service-Key": "test-worker-key", "X-Tenant-Id": TENANT}
        bad_feature = {"features": [{"entity_type": "transaction", "feature_set_version": "v1",
                                     "as_of_timestamp": "2026-07-01T00:00:00Z", "features": {}}]}
        assert client.post("/features", headers=headers, json=bad_feature).status_code == 422
        assert client.post("/policy/search", headers=headers, json={}).status_code == 422
        assert client.post("/model-runs", headers=headers, json={"model_name": "x", "unknown": 1}).status_code == 422
