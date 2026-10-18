"""Cases.get_alert: an alert's evidence is only what is linked to it, never substituted or invented."""
import pytest

from app.services.orchestration import adapters

TENANT, ALERT = "t1", "a1b2c3d4-0000-0000-0000-000000000000"
ALERT_ROW = {"id": ALERT, "rank_score": 0.9, "component_signals": [], "typology": "fan_out",
             "priority_band": "high", "has_dq_caveat": False, "ranking_strategy": "rrf",
             "model_run_id": None, "priority_rank": 1}


class _Query:
    def __init__(self, rows, filters):
        self.rows, self.filters = rows, filters

    def select(self, *_):
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def in_(self, col, vals):
        self.filters.append((col, tuple(vals)))
        return self

    def execute(self):
        return type("R", (), {"data": self.rows})()


class _Db:
    def __init__(self, tables):
        self.tables, self.filters = tables, {}

    def table(self, name):
        return _Query(self.tables.get(name, []), self.filters.setdefault(name, []))


class _Ctx:
    tid = TENANT

    def snapshot(self):
        return "snap1"

    def transactions(self):
        return [{"id": "unrelated-1"}, {"id": "unrelated-2"}]


def _cases(monkeypatch, tables):
    db = _Db(tables)
    monkeypatch.setattr(adapters, "get_db", lambda: db)
    monkeypatch.setattr(adapters, "get_one", lambda table, tid, rid: ALERT_ROW)
    return adapters.Cases(_Ctx()), db


def test_alert_without_links_is_an_error_not_other_transactions(monkeypatch):
    cases, _ = _cases(monkeypatch, {})
    with pytest.raises(ValueError, match="no linked transactions"):
        cases.get_alert(ALERT)


def test_signal_evidence_ids_are_used_and_tenant_scoped(monkeypatch):
    cases, db = _cases(monkeypatch, {
        "alert_signals": [{"signal_id": "s1"}],
        "risk_signals": [{"transaction_id": None, "evidence_transaction_ids": ["tx-1", "tx-2"]}],
    })
    assert cases.get_alert(ALERT).tx_ids == ["tx-1", "tx-2"]
    assert ("tenant_id", TENANT) in db.filters["risk_signals"]
