"""Source-feed summaries, the DQ rule catalog, mapping preview and their auth gates (no database)."""

import re
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.core.security import CurrentUser, get_current_user
from app.main import app
from app.services import canonical, data_ops, quality

client = TestClient(app)
TENANT = "11111111-1111-1111-1111-111111111111"
NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)

IBM_CSV = (
    "Timestamp,From Bank,Account,To Bank,Account,Amount Paid,Payment Currency,Payment Format\n"
    "2026/10/01 09:05,TLBANK,ACC-1,B2,ACC-2,950.00,US Dollar,Wire\n"
    "2026/10/01 09:10,TLBANK,ACC-3,B2,ACC-4,12.5,Monopoly Money,ACH\n"
).encode()


def as_role(role: str):
    user = CurrentUser(id="00000000-0000-0000-0000-000000000001", tenant_id=TENANT, role=role)
    app.dependency_overrides[get_current_user] = lambda: user


def teardown_function():
    app.dependency_overrides.clear()


# --- Rule catalog ------------------------------------------------------------------------------------


def test_rule_catalog_matches_rules_that_run():
    source = Path(quality.__file__).read_text(encoding="utf-8")
    raised = set(re.findall(r'Issue\(\s*"(DQ-[A-Z]+-\d{3})",\s*"(\w+)",\s*"(\w+)"', source))
    catalog = {(r.rule_id, r.issue_type, r.severity) for r in quality.RULE_CATALOG}
    assert raised and raised == catalog
    assert len({r.rule_id for r in quality.RULE_CATALOG}) == len(quality.RULE_CATALOG)


def test_rule_outcomes():
    by_id = {r["rule_id"]: r for r in quality.rule_catalog()}
    assert by_id["DQ-SCH-001"]["outcome"].startswith("Run fails")
    assert by_id["DQ-CUR-001"]["outcome"].startswith("Row rejected")
    assert by_id["DQ-CHN-001"]["outcome"] == "Kept with a caveat"


def test_rules_endpoint_any_user():
    as_role("reviewer")
    body = client.get("/data-quality/rules").json()
    assert body["rules_version"] == quality.RULES_VERSION
    assert body["total"] == len(quality.RULE_CATALOG)


def test_rules_and_sources_need_auth():
    assert client.get("/data-quality/rules").status_code == 401
    assert client.get("/ingestion/sources").status_code == 401


# --- Headers and mapping -----------------------------------------------------------------------------


def test_dedupe_headers_like_pandas():
    assert canonical.dedupe_headers(["Account", "Account", "Account.1", "Account"]) == [
        "Account", "Account.1", "Account.1.1", "Account.2",
    ]


def test_empty_override_unmaps_field():
    headers = ["timestamp", "amount", "description"]
    assert "description" in canonical.auto_map(headers)
    assert "description" not in canonical.auto_map(headers, {"description": ""})


def test_preview_ibm_export_with_repeated_account_column():
    headers, sample = data_ops.parse_preview(IBM_CSV, complete=True)
    assert headers[2:5] == ["Account", "To Bank", "Account.1"]
    preview = data_ops.build_preview(headers, sample, None, set())
    assert preview["mapping"]["origin_account"] == "Account"
    assert preview["mapping"]["destination_account"] == "Account.1"
    assert preview["missing_required"] == [] and preview["can_upload"]
    fields = {f["field"]: f for f in preview["fields"]}
    assert fields["amount"]["match"] == "alias" and fields["timestamp"]["match"] == "exact"
    statuses = [r["status"] for r in preview["rows"]]
    assert statuses[0] in ("accepted", "accepted_with_warnings")
    assert statuses[1] == "rejected"
    assert any(i["rule_id"] == "DQ-CUR-001" for i in preview["rows"][1]["issues"])


def test_preview_override_and_missing_required():
    headers, sample = data_ops.parse_preview(b"when,amt,ccy,src,dst\n2026-10-01T00:00:00Z,5,USD,AA1,BB2\n", True)
    preview = data_ops.build_preview(headers, sample, {"timestamp": "when", "amount": "amt"}, set())
    assert preview["missing_required"] == ["origin_account", "destination_account", "channel"]
    assert not preview["can_upload"]
    assert preview["rows"][0]["status"] is None  # rows are not validated while the schema is blocked
    assert {f["field"]: f["match"] for f in preview["fields"]}["timestamp"] == "manual"


def test_preview_drops_cut_last_line_and_caps_rows():
    body = "timestamp,amount\n" + "".join(f"2026-10-01,{i}\n" for i in range(1, 60)) + "2026-10-0"
    headers, sample = data_ops.parse_preview(body.encode(), complete=False)
    assert headers == ["timestamp", "amount"]
    assert len(sample) == data_ops.PREVIEW_ROWS


def test_preview_mapping_endpoint_roles_and_validation():
    files = {"file": ("ibm.csv", IBM_CSV, "text/csv")}
    as_role("reviewer")
    assert client.post("/ingestion/preview-mapping", files=files).status_code == 403
    as_role("analyst")
    bad = client.post("/ingestion/preview-mapping", files=files, data={"field_mapping": '{"nope": "x"}'})
    assert bad.status_code == 422
    assert client.post("/ingestion/preview-mapping", files={"file": ("e.csv", b"", "text/csv")}).status_code == 422
    with patch("app.services.data_ops.existing_refs", return_value=set()):
        ok = client.post("/ingestion/preview-mapping", files=files)
    assert ok.status_code == 200 and ok.json()["mapping"]["destination_account"] == "Account.1"


def test_reviewer_cannot_upload():
    as_role("reviewer")
    response = client.post("/ingestion/upload", files={"file": ("x.csv", IBM_CSV, "text/csv")},
                           data={"source_system": "s", "data_owner": "o"})
    assert response.status_code == 403


# --- Source feeds ------------------------------------------------------------------------------------


def _file(fid, source, uploaded, owner="Ops"):
    return {"id": fid, "source_system": source, "data_owner": owner, "schema_version": "canonical-txn-v1",
            "is_synthetic": True, "uploaded_at": uploaded}


def _run(rid, fid, status, rows, accepted, warnings, rejected, started):
    return {"id": rid, "file_id": fid, "status": status, "row_count": rows, "accepted_count": accepted,
            "warning_count": warnings, "rejected_count": rejected, "started_at": started}


def test_summarize_sources_groups_and_statuses():
    files = [
        _file("f1", "ach", "2026-10-17T10:00:00+00:00"),
        _file("f2", "ach", "2026-10-16T10:00:00+00:00", owner="Payments"),
        _file("f3", "wire", "2026-10-10T10:00:00+00:00"),
        _file("f4", "card", "2026-10-17T11:00:00+00:00"),
        _file("f5", "p2p", "2026-10-17T11:30:00+00:00"),
    ]
    runs = [
        _run("r1", "f1", "completed", 10, 10, 1, 0, "2026-10-17T10:00:01+00:00"),
        _run("r2", "f2", "completed_with_errors", 30, 25, 2, 5, "2026-10-16T10:00:01+00:00"),
        _run("r3", "f3", "completed", 5, 5, 0, 0, "2026-10-10T10:00:01+00:00"),
        _run("r4", "f4", "failed", 8, 0, 0, 8, "2026-10-17T11:00:01+00:00"),
        _run("r5", "f5", "completed_with_errors", 4, 3, 0, 1, "2026-10-17T11:30:01+00:00"),
    ]
    feeds = {f["source_system"]: f for f in data_ops.summarize_sources(files, runs, {"ach": 3}, NOW, 24)}
    ach = feeds["ach"]
    assert ach["owners"] == ["Ops", "Payments"] and ach["run_count"] == 2 and ach["file_count"] == 2
    assert (ach["rows"], ach["accepted"], ach["warnings"], ach["rejected"]) == (40, 35, 3, 5)
    assert ach["accepted_rate"] == 0.875 and ach["rejected_rate"] == 0.125
    assert ach["status"] == "current" and ach["latest_run"]["id"] == "r1"  # latest run had no rejections
    assert ach["open_issue_count"] == 3 and ach["hours_since_upload"] == 2.0
    assert feeds["wire"]["status"] == "late" and feeds["wire"]["open_issue_count"] == 0
    assert feeds["card"]["status"] == "failed"
    assert feeds["p2p"]["status"] == "has_rejections"
    relaxed = data_ops.summarize_sources(files, runs, {}, NOW, 24 * 30)
    assert relaxed[0]["source_system"] == "p2p"  # newest upload first
    assert {f["source_system"]: f["status"] for f in relaxed}["wire"] == "current"  # within a 30-day SLA


def test_summarize_sources_without_runs():
    (feed,) = data_ops.summarize_sources([_file("f1", "cash", "2026-10-17T10:00:00+00:00")], [], {}, NOW, 24)
    assert feed["status"] == "no_runs" and feed["accepted_rate"] is None and feed["latest_run"] is None


def test_sources_endpoint_validates_sla():
    as_role("reviewer")
    assert client.get("/ingestion/sources?freshness_sla_hours=0").status_code == 422


def test_sources_endpoint_shapes_response():
    as_role("reviewer")
    db = MagicMock()
    with patch("app.api.routes.data_ops.get_db", return_value=db), \
         patch("app.api.routes.data_ops.fetch_all", side_effect=[
             [_file("f1", "ach", "2026-10-17T10:00:00+00:00")],
             [_run("r1", "f1", "completed", 10, 10, 0, 0, "2026-10-17T10:00:01+00:00")],
         ]), \
         patch("app.services.data_ops.open_issue_counts", return_value={"ach": 2}):
        body = client.get("/ingestion/sources?freshness_sla_hours=48").json()
    assert body["freshness_sla_hours"] == 48 and body["total"] == 1
    assert body["items"][0]["open_issue_count"] == 2
