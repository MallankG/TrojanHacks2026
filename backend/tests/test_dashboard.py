"""Command-center aggregation, search parsing, notifications and role filtering (no database)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.security import CurrentUser, get_current_user
from app.main import app
from app.services import dashboard as dash

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
TENANT = "11111111-1111-1111-1111-111111111111"


def _alert(status, band, typology, amount=None, currency="USD", days_old=0.0):
    return {"id": f"{status}-{band}-{typology}-{days_old}", "status": status, "priority_band": band, "typology": typology,
            "total_amount": amount, "amount_currency": currency, "created_at": (NOW - timedelta(days=days_old)).isoformat()}


def test_summarize_alerts_counts_only_open_alerts():
    rows = [
        _alert("new", "critical", "layering", "48500.0000", days_old=8),
        _alert("under_review", "high", "structuring", "9400.5000", days_old=2),
        _alert("new", "high", "layering", None, days_old=1),
        _alert("in_case", "medium", "cycle", "12000.2500"),
        _alert("dismissed", "low", "layering", "100.10", "EUR"),
    ]
    s = dash.summarize_alerts(rows, NOW, sla_days=5)
    assert s["open_total"] == 3
    assert s["by_status"] == {"new": 2, "under_review": 1, "in_case": 1, "closed_no_action": 0, "dismissed": 1}
    assert s["by_priority_band"] == {"critical": 1, "high": 2, "medium": 0, "low": 0}
    assert s["review_first"] == 3
    assert s["by_typology"] == [{"typology": "layering", "count": 2}, {"typology": "structuring", "count": 1}]
    buckets = s["age"].pop("buckets")
    assert s["age"] == {"sla_days": 5, "oldest_hours": 192.0, "median_hours": 48.0, "past_sla": 1}
    assert [(b["label"], b["count"], b["past_sla"]) for b in buckets] == [
        ("Under 1 day", 0, False), ("1 to 3 days", 2, False), ("3 to 5 days", 0, False), ("5 to 10 days", 1, True), ("10+ days", 0, True),
    ]
    # Exact decimal sum, per currency, and the estimate is labelled as such.
    assert s["estimated_amount"]["by_currency"] == {"USD": "57900.5000"}
    assert s["estimated_amount"]["alerts_without_amount"] == 1
    assert s["estimated_amount"]["is_estimate"] is True


def test_summarize_alerts_keeps_currencies_apart_and_handles_empty():
    rows = [_alert("new", "low", "other", "0.10"), _alert("new", "low", "other", "0.20"), _alert("new", "low", "other", "5", "EUR")]
    assert dash.summarize_alerts(rows, NOW, 5)["estimated_amount"]["by_currency"] == {"EUR": "5", "USD": "0.30"}
    empty = dash.summarize_alerts([], NOW, 5)
    assert empty["open_total"] == 0 and empty["age"]["oldest_hours"] is None and empty["by_typology"] == []


def test_summarize_cases():
    s = dash.summarize_cases(["open", "pending_review", "pending_review", "closed"])
    assert s["total"] == 4 and s["open_total"] == 3 and s["pending_review"] == 2
    assert s["by_status"]["escalated"] == 0


def test_summarize_ingestion_and_flags():
    runs = [
        {"id": "r2", "status": "completed_with_errors", "row_count": 25, "accepted_count": 20, "warning_count": 2, "rejected_count": 5,
         "source_files": {"filename": "b.csv"}},
        {"id": "r1", "status": "failed", "row_count": 0, "accepted_count": 0, "warning_count": 0, "rejected_count": 0},
    ]
    ing = dash.summarize_ingestion(runs, {"error": 5, "warning": 1, "info": 1})
    assert ing["latest_run"]["filename"] == "b.csv" and ing["total_rows"] == 25 and ing["rejected_rate"] == 0.2
    assert ing["failed_runs"] == 1 and ing["partial_runs"] == 1
    assert ing["open_issues_by_severity"] == {"critical": 0, "error": 5, "warning": 1, "info": 1}
    flags = {f["key"]: f["status"] for f in dash.pipeline_flags(ing, {"ranker": None}, None, NOW)}
    assert flags == {"ingestion": "attention", "data_quality": "attention", "scoring": "not_run"}

    stale = {"ranker": dash.freshness_row({"id": "m", "completed_at": (NOW - timedelta(days=10)).isoformat()}, NOW)}
    assert stale["ranker"]["age_hours"] == 240.0
    flags = {f["key"]: f["status"] for f in dash.pipeline_flags(ing, stale, {"status": "completed"}, NOW)}
    assert flags["scoring"] == "attention"
    flags = {f["key"]: f["status"] for f in dash.pipeline_flags(ing, stale, {"status": "failed"}, NOW)}
    assert flags["scoring"] == "failed"


def test_empty_tenant_flags():
    ing = dash.summarize_ingestion([], {})
    assert ing["latest_run"] is None and ing["rejected_rate"] is None
    flags = {f["key"]: f["status"] for f in dash.pipeline_flags(ing, {"ranker": None}, None, NOW)}
    assert flags == {"ingestion": "not_run", "data_quality": "ok", "scoring": "not_run"}


@pytest.mark.parametrize(("q", "alert", "case"), [("AL-12", 12, None), ("case-0012", None, 12), ("12", 12, 12), ("ACC-7001", None, None)])
def test_parse_search_numbers(q, alert, case):
    parsed = dash.parse_search(q)
    assert (parsed["alert_number"], parsed["case_number"]) == (alert, case)


def test_parse_search_sanitises_and_matches_typologies():
    assert dash.parse_search("layer")["typologies"] == ["layering"]
    assert dash.parse_search("mule network")["typologies"] == ["mule_network"]
    assert dash.parse_search("ab")["typologies"] == []
    assert dash.parse_search("ACC-1,id.eq.x)(")["text"] == "ACC-1id.eq.x"
    assert len(dash.parse_search("x" * 200)["text"]) == dash.SEARCH_MAX_LENGTH
    assert dash.like_pattern("a_b%") == "%a\\_b\\%%"


def _notifications(**overrides):
    args = dict(
        can_investigate=True, can_review=True,
        pending_cases=[{"id": "c1", "case_number": 3, "title": "T", "updated_at": "2026-10-17T10:00:00+00:00"}], pending_total=7,
        ranker={"id": "m1", "model_name": "ranker", "model_version": "1", "completed_at": "2026-10-17T09:00:00+00:00"}, new_alerts=4,
        bad_runs=[{"id": "r1", "status": "failed", "started_at": "2026-10-17T08:00:00+00:00"}],
        critical_issues=2, latest_critical_at="2026-10-17T11:00:00+00:00",
    )
    return dash.build_notifications(**{**args, **overrides})


def test_notifications_newest_first_with_targets():
    items = _notifications()
    assert [i["kind"] for i in items] == ["critical_data_quality", "case_pending_review", "new_alerts", "ingestion_failed", "case_pending_review"]
    assert items[1]["title"] == "Review requested: CASE-0003" and items[1]["target_id"] == "c1"
    assert items[-1]["title"].startswith("6 more")


def test_notifications_respect_roles():
    kinds = {i["kind"] for i in _notifications(can_review=False, can_investigate=False)}
    assert kinds == {"critical_data_quality", "ingestion_failed"}


# --- Route-level role filtering -------------------------------------------------------------------
@pytest.fixture
def as_role():
    client = TestClient(app)

    def use(role: str):
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(id="u1", tenant_id=TENANT, role=role)
        return client

    yield use
    app.dependency_overrides.clear()


def _tables(db: MagicMock) -> set[str]:
    return {c.args[0] for c in db.table.call_args_list}


@pytest.mark.parametrize(("role", "sees_cases"), [("data_steward", False), ("analyst", True), ("reviewer", True)])
def test_search_case_records_need_a_case_role(as_role, role, sees_cases):
    db = MagicMock()
    with patch("app.api.routes.dashboard.get_db", return_value=db), patch("app.services.dashboard.get_db", return_value=db):
        response = as_role(role).get("/search", params={"q": "CASE-0003 layering"})
        assert response.status_code == 200
        response = as_role(role).get("/search", params={"q": "3"})
    assert response.json()["cases_included"] is sees_cases
    assert ("cases" in _tables(db)) is sees_cases


def test_search_validates_query(as_role):
    assert as_role("analyst").get("/search", params={"q": ""}).status_code == 422
    assert as_role("analyst").get("/search", params={"q": "x" * 101}).status_code == 422


def test_summary_hides_cases_from_stewards(as_role):
    with patch.object(dash, "latest_ranker", return_value=None), \
         patch.object(dash, "load_case_statuses", return_value=(["pending_review"], 1)) as cases, \
         patch.object(dash, "load_runs", return_value=([], 0)), \
         patch.object(dash, "load_open_dq_by_severity", return_value={}), \
         patch.object(dash, "load_freshness", return_value=({k: None for k in dash.MODEL_KINDS}, None)), \
         patch("app.services.dashboard.get_db", return_value=MagicMock()):
        steward = as_role("data_steward").get("/dashboard/summary").json()
        assert steward["cases"] is None and not cases.called
        reviewer = as_role("reviewer").get("/dashboard/summary", params={"sla_days": 3}).json()
    assert reviewer["cases"]["pending_review"] == 1
    assert reviewer["alerts"]["age"]["sla_days"] == 3 and reviewer["alerts"]["open_total"] == 0
    assert as_role("reviewer").get("/dashboard/summary", params={"sla_days": 0}).status_code == 422


def test_service_workers_cannot_read_the_dashboard():
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=None, tenant_id=TENANT, role="service")
    try:
        client = TestClient(app)
        for path in ("/dashboard/summary", "/notifications", "/search?q=ab"):
            assert client.get(path).status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_dashboard_requires_auth():
    client = TestClient(app)
    for path in ("/dashboard/summary", "/notifications", "/search?q=ab"):
        assert client.get(path).status_code == 401


LLM_ENV = ("LLM_PROVIDER", "GROQ_API_KEY", "GROQ_MODEL", "APPROVED_DATA_DESTINATIONS", "OLLAMA_HOST", "OLLAMA_MODEL",
           "LLM_TIMEOUT_S", "LLM_MAX_CALLS_PER_RUN")


@pytest.mark.parametrize(("env", "configured", "reason"), [
    ({"LLM_PROVIDER": "groq", "GROQ_API_KEY": "gsk_x", "APPROVED_DATA_DESTINATIONS": '["supabase","groq"]'}, True, None),
    ({"LLM_PROVIDER": "groq", "APPROVED_DATA_DESTINATIONS": '["groq"]'}, False, "Groq API key missing"),
    ({"LLM_PROVIDER": "groq", "GROQ_API_KEY": "gsk_x", "APPROVED_DATA_DESTINATIONS": '["supabase"]'}, False,
     "Groq not an approved data destination"),
    ({"LLM_PROVIDER": "template"}, False, "Template only (no LLM configured)"),
    ({}, True, None),  # default: local Ollama is configured (reachability is checked per call, not here)
    ({"LLM_PROVIDER": "openai"}, False, "LLM settings invalid"),
    ({"LLM_MAX_CALLS_PER_RUN": "abc"}, False, "LLM settings invalid"),
])
def test_llm_status_reports_what_the_agents_will_use(monkeypatch, env, configured, reason):
    from tracelayer_agents import api as agents_api
    for key in LLM_ENV:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    status = agents_api.llm_status()
    assert (status["configured"], status["reason"]) == (configured, reason)
    assert "gsk" not in str(status)  # never echoes the key
