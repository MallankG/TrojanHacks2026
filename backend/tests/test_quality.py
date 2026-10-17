from datetime import UTC, datetime
from decimal import Decimal

from app.services import canonical
from app.services.quality import RowValidator, parse_timestamp, schema_issues

HEADERS = ["transaction_id", "timestamp", "amount", "currency", "channel", "origin_account", "destination_account"]
MAPPING = canonical.auto_map(HEADERS)


def row(**overrides):
    base = {
        "transaction_id": "TX-1", "timestamp": "2026-07-01T10:00:00Z", "amount": "9500.00", "currency": "USD",
        "channel": "Wire", "origin_account": "ACC-1", "destination_account": "ACC-2",
    }
    return {**base, **overrides}


def validate(*rows, existing=frozenset()):
    v = RowValidator(MAPPING, set(existing), now=datetime(2026, 10, 1, tzinfo=UTC))
    return [v.validate(i + 1, r) for i, r in enumerate(rows)]


def rule_ids(result):
    return {i.rule_id for i in result.issues}


def test_valid_row_is_normalized():
    (r,) = validate(row())
    assert r.status == "accepted"
    assert r.normalized["amount"] == Decimal("9500.0000")
    assert r.normalized["channel"] == "wire"
    assert r.normalized["occurred_at"] == datetime(2026, 7, 1, 10, tzinfo=UTC)
    assert r.normalized["amount_usd"] == Decimal("9500.0000")


def test_rejections():
    cases = {
        "DQ-REQ-001": row(timestamp=""),
        "DQ-TS-001": row(timestamp="2026-13-45 99:99"),
        "DQ-CUR-001": row(currency="USX"),
        "DQ-AMT-001": row(amount="abc"),
        "DQ-AMT-002": row(amount="-5"),
        "DQ-AMT-003": row(amount="9999999999999"),
        "DQ-ACC-001": row(origin_account="UNKNOWN"),
    }
    for rule, raw in cases.items():
        (r,) = validate(raw)
        assert r.status == "rejected", rule
        assert rule in rule_ids(r), rule
        assert r.normalized is None


def test_warnings_keep_row():
    (r,) = validate(row(channel="Carrier Pigeon"))
    assert r.status == "accepted_with_warnings"
    assert r.normalized["channel"] == "other"
    (naive,) = validate(row(timestamp="2026-07-01 10:00:00"))
    assert "DQ-TS-004" in rule_ids(naive)
    assert naive.normalized["original_values"]["timezone_assumed"] == "UTC"


def test_duplicates_in_file_and_already_ingested():
    first, second = validate(row(), row())
    assert first.status == "accepted"
    assert "DQ-DUP-001" in rule_ids(second)
    (r,) = validate(row(), existing={"TX-1"})
    assert "DQ-DUP-002" in rule_ids(r)


def test_currency_names_and_fx():
    (r,) = validate(row(currency="Euro", amount="100"))
    assert r.normalized["currency"] == "EUR"
    assert r.normalized["amount_usd"] == Decimal("108.0000")
    assert r.normalized["original_values"]["currency"] == "Euro"


def test_labels_are_parsed_only_for_known_typologies():
    mapping = canonical.auto_map(HEADERS + ["is_suspicious", "typology"])
    v = RowValidator(mapping, set())
    r = v.validate(1, {**row(), "is_suspicious": "1", "typology": "structuring"})
    assert r.normalized["label"] == {"is_suspicious": True, "typology": "structuring"}
    r = v.validate(2, {**row(transaction_id="TX-2"), "is_suspicious": "1", "typology": "made_up"})
    assert r.normalized["label"]["typology"] is None


def test_schema_mismatch_is_critical():
    issues = schema_issues(["foo", "bar"], canonical.auto_map(["foo", "bar"]))
    assert any(i.rule_id == "DQ-SCH-001" and i.severity == "critical" for i in issues)


def test_auto_map_ibm_style_headers():
    headers = ["Timestamp", "From Bank", "Account", "To Bank", "Account.1", "Amount Paid", "Payment Currency",
               "Payment Format", "Is Laundering"]
    mapping = canonical.auto_map(headers)
    assert mapping["origin_account"] == "Account"
    assert mapping["destination_account"] == "Account.1"
    assert mapping["amount"] == "Amount Paid"
    assert mapping["channel"] == "Payment Format"
    assert mapping["is_suspicious"] == "Is Laundering"


def test_parse_timestamp_formats():
    assert parse_timestamp("2026/07/01 10:00")[0] == datetime(2026, 7, 1, 10, tzinfo=UTC)
    ts, assumed = parse_timestamp("2026-07-01T10:00:00-04:00")
    assert ts == datetime(2026, 7, 1, 14, tzinfo=UTC) and not assumed


def test_amount_that_rounds_to_zero_is_rejected():
    (r,) = validate(row(amount="0.00001"))
    assert r.status == "rejected"
    assert "DQ-AMT-004" in rule_ids(r)
