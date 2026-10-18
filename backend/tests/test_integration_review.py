from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services import graph
from app.services.detection.ledger import Txn
from app.services.detection.run import load_ledger, run_detection
from app.core.security import CurrentUser


def txn(i, currency="USD", usd="100"):
    return {"id": str(i), "occurred_at": "2026-10-01T00:00:00Z", "amount": "1000000", "currency": currency,
            "amount_usd": usd, "origin_account_id": "a", "destination_account_id": "b"}


def query_mock(rows):
    q = MagicMock()
    for method in ("table", "select", "eq", "order", "in_", "or_", "gte", "lte"):
        getattr(q, method).return_value = q
    q.range.side_effect = lambda start, end: SimpleNamespace(execute=lambda: SimpleNamespace(data=rows[start:end + 1]))
    return q


def test_graph_only_reads_pages_inside_cap():
    q = query_mock([txn(i) for i in range(100)])
    with patch.object(graph, "get_db", return_value=q), patch.object(graph, "PAGE_SIZE", 10):
        result = graph._edges_touching("tenant", {"a"}, None, None, cap=23)
    assert len(result) == 23
    assert [c.args for c in q.range.call_args_list] == [(0, 9), (10, 19), (20, 22)]


def test_graph_checks_deadline_between_pages():
    q = query_mock([txn(i) for i in range(100)])
    with patch.object(graph, "get_db", return_value=q), patch.object(graph, "PAGE_SIZE", 10), \
            patch.object(graph.time, "monotonic", side_effect=[0, 0, 11]):
        result = graph._edges_touching("tenant", {"a"}, None, None, cap=23, deadline=10)
    assert len(result) == 10
    assert q.range.call_count == 1


def test_graph_zero_cap_does_not_read():
    with patch.object(graph, "get_db") as db:
        assert graph._edges_touching("tenant", {"a"}, None, None, cap=0) == []
    db.assert_not_called()


def test_non_usd_missing_conversion_cannot_become_usd():
    with pytest.raises(ValueError, match="no USD conversion"):
        Txn.from_row(txn(1, "JPY", None))
    assert str(Txn.from_row(txn(2, "USD", None)).usd) == "1000000"
    assert str(Txn.from_row(txn(3, "JPY", "6500")).usd) == "6500"


def test_ledger_excludes_missing_fx_and_counts_caveat():
    q = query_mock([txn(1, "JPY", None), txn(2), txn(3, "JPY", "6500")])
    with patch("app.services.detection.run.get_db", return_value=q):
        ledger = load_ledger("tenant", 30, 100)
    assert [t.id for t in ledger.txns] == ["2", "3"]
    assert ledger.excluded_no_usd == 1


def test_run_reports_missing_fx_even_with_no_alerts():
    q = query_mock([txn(1, "JPY", None)])
    with patch("app.services.detection.run.get_db", return_value=q), \
            patch("app.services.detection.run._start_run", return_value={"id": "00000000-0000-0000-0000-000000000001"}), \
            patch("app.services.detection.run._finish_run") as finish, \
            patch("app.services.detection.run.audit.record"):
        result = run_detection(CurrentUser(id="user", tenant_id="tenant", role="admin"))
    assert result["excluded_no_usd"] == 1
    assert result["transactions_considered"] == 0
    assert "USD conversion is missing" in result["data_quality_caveat"]
    assert finish.call_args.args[3]["excluded_no_usd"] == 1
