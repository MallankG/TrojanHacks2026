from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from app.main import app
from app.core.security import CurrentUser, get_current_user
from app.services.governance import demo

TENANT = "11111111-1111-1111-1111-111111111111"
PRODUCT = "22222222-2222-2222-2222-222222222222"
ACTOR = "33333333-3333-3333-3333-333333333333"
URL = f"/governance/products/{PRODUCT}/replacements"


def body():
    product = demo("healthy")["product"]
    product["contract"]["version"] = "payments-v2"
    return {"product": product, "reason": "Repair the timestamp contract after source review"}


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_replacement_requires_authenticated_steward():
    client = TestClient(app)
    assert client.post(URL, json=body()).status_code == 401
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(ACTOR, TENANT, "analyst")
    assert client.post(URL, json=body()).status_code == 403


def test_replacement_uses_atomic_rpc_and_authenticated_scope():
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(ACTOR, TENANT, "data_steward")
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = {"revision": 2, "replaces_product_id": PRODUCT}
    payload = body()
    with patch("app.api.routes.governance_contracts.get_db", return_value=db):
        response = TestClient(app).post(URL, json=payload)
    assert response.status_code == 201
    assert response.json()["revision"] == 2
    db.rpc.assert_called_once_with("replace_governance_product", {
        "p_tenant_id": TENANT, "p_product_id": PRODUCT, "p_actor_id": ACTOR,
        "p_definition": payload["product"], "p_reason": payload["reason"],
    })
    db.table.assert_not_called()


def test_stale_replacement_returns_conflict():
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(ACTOR, TENANT, "data_steward")
    db = MagicMock()
    db.rpc.return_value.execute.side_effect = APIError({"code": "23505", "message": "Product already replaced", "details": None, "hint": None})
    with patch("app.api.routes.governance_contracts.get_db", return_value=db):
        response = TestClient(app).post(URL, json=body())
    assert response.status_code == 409
