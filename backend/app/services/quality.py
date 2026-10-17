"""Row-level data-quality rules and normalization to the canonical schema.

Each rule has a stable rule_id so issues can be traced back to the rule that produced them.
Severity 'error'/'critical' rejects the row; 'warning'/'info' keeps it with a caveat.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from app.services import canonical as c

RULES_VERSION = "dq-rules-1.0.0"
MAX_PLAUSIBLE_AMOUNT = Decimal("1000000000")  # USD-equivalent; above this is treated as impossible
MIN_TIMESTAMP = datetime(1990, 1, 1, tzinfo=UTC)
AMOUNT_QUANTUM = Decimal("0.0001")

TIMESTAMP_FORMATS = (
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y",
    "%Y-%m-%d",
)


@dataclass
class Issue:
    rule_id: str
    issue_type: str
    severity: str
    message: str
    field_name: str | None = None
    observed_value: str | None = None
    expected_value: str | None = None

    @property
    def rejects(self) -> bool:
        return self.severity in ("error", "critical")


@dataclass
class RowResult:
    row_number: int
    raw: dict
    normalized: dict | None
    issues: list[Issue] = field(default_factory=list)

    @property
    def status(self) -> str:
        if any(i.rejects for i in self.issues):
            return "rejected"
        return "accepted_with_warnings" if self.issues else "accepted"


def row_hash(raw: dict) -> str:
    return hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()


def schema_issues(headers: list[str], mapping: dict[str, str]) -> list[Issue]:
    issues: list[Issue] = []
    for required in c.REQUIRED_FIELDS:
        column = mapping.get(required)
        if not column:
            issues.append(
                Issue("DQ-SCH-001", "schema_mismatch", "critical", f"No source column mapped to required field '{required}'",
                      field_name=required, expected_value=c.FIELD_DESCRIPTIONS[required])
            )
        elif column not in headers:
            issues.append(
                Issue("DQ-SCH-002", "schema_mismatch", "critical", f"Mapped column '{column}' is not present in the file",
                      field_name=required, observed_value=column)
            )
    unmapped = [h for h in headers if h not in mapping.values()]
    if unmapped:
        issues.append(
            Issue("DQ-SCH-003", "schema_mismatch", "info", "Columns not mapped to the canonical schema (possible schema drift)",
                  observed_value=", ".join(unmapped[:20]))
        )
    return issues


def parse_timestamp(value: str) -> tuple[datetime | None, bool]:
    """Return (UTC datetime, timezone_was_assumed)."""
    text = value.strip()
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for fmt in TIMESTAMP_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None, False
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC), True
    return parsed.astimezone(UTC), False


def parse_amount(value: str) -> Decimal | None:
    text = value.strip().replace(",", "").replace("$", "")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    return amount if amount.is_finite() else None


def parse_bool(value: str) -> bool | None:
    text = value.strip().lower()
    if text in ("1", "true", "yes", "y", "t"):
        return True
    if text in ("0", "false", "no", "n", "f"):
        return False
    return None


def dedupe_hash(n: dict) -> str:
    parts = [n["occurred_at"].isoformat(), format(n["amount"], "f"), n["currency"], n["origin_ref"], n["destination_ref"],
             n["channel"]]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


class RowValidator:
    """Validates rows of one file. Keeps state for in-file duplicate detection."""

    def __init__(self, mapping: dict[str, str], existing_refs: set[str], now: datetime | None = None):
        self.mapping = mapping
        self.existing_refs = existing_refs
        self.seen_refs: set[str] = set()
        self.seen_hashes: set[str] = set()
        self.now = now or datetime.now(UTC)

    def _get(self, raw: dict, name: str) -> str:
        column = self.mapping.get(name)
        value = raw.get(column) if column else None
        return "" if value is None else str(value).strip()

    def _account(self, raw: dict, name: str, issues: list[Issue]) -> str | None:
        value = self._get(raw, name)
        if not value:
            issues.append(Issue("DQ-REQ-001", "missing_required_field", "error", f"Missing {name}", field_name=name))
            return None
        if value.lower() in c.NULL_ACCOUNT_TOKENS or not c.ACCOUNT_PATTERN.match(value):
            issues.append(
                Issue("DQ-ACC-001", "unresolved_account", "error", f"Account identifier for {name} cannot be resolved",
                      field_name=name, observed_value=value[:120], expected_value="2-64 chars [A-Za-z0-9_-:.]")
            )
            return None
        return value

    def validate(self, row_number: int, raw: dict) -> RowResult:
        issues: list[Issue] = []
        original: dict = {}

        # Timestamp
        ts_text = self._get(raw, "timestamp")
        occurred_at = None
        if not ts_text:
            issues.append(Issue("DQ-REQ-001", "missing_required_field", "error", "Missing timestamp", field_name="timestamp"))
        else:
            occurred_at, assumed_utc = parse_timestamp(ts_text)
            if occurred_at is None:
                issues.append(Issue("DQ-TS-001", "invalid_timestamp", "error", "Timestamp could not be parsed",
                                    field_name="timestamp", observed_value=ts_text[:64], expected_value="ISO-8601"))
            elif occurred_at < MIN_TIMESTAMP:
                issues.append(Issue("DQ-TS-002", "invalid_timestamp", "error", "Timestamp is implausibly old",
                                    field_name="timestamp", observed_value=ts_text))
                occurred_at = None
            else:
                if occurred_at > self.now + timedelta(days=1):
                    issues.append(Issue("DQ-TS-003", "invalid_timestamp", "warning", "Timestamp is in the future",
                                        field_name="timestamp", observed_value=ts_text))
                if assumed_utc or occurred_at.isoformat() != ts_text:
                    original["timestamp"] = ts_text
                if assumed_utc:
                    original["timezone_assumed"] = "UTC"
                    issues.append(Issue("DQ-TS-004", "invalid_timestamp", "info",
                                        "Timestamp has no timezone; interpreted as UTC",
                                        field_name="timestamp", observed_value=ts_text, expected_value="ISO-8601 with offset"))

        # Currency
        currency_text = self._get(raw, "currency")
        currency = None
        if not currency_text:
            issues.append(Issue("DQ-REQ-001", "missing_required_field", "error", "Missing currency", field_name="currency"))
        else:
            candidate = c.CURRENCY_NAMES.get(c.key(currency_text), currency_text.upper())
            if candidate not in c.KNOWN_CURRENCIES:
                issues.append(Issue("DQ-CUR-001", "invalid_currency", "error", "Unrecognized currency code",
                                    field_name="currency", observed_value=currency_text[:32], expected_value="ISO-4217"))
            else:
                currency = candidate
                if candidate != currency_text:
                    original["currency"] = currency_text

        # Amount
        amount_text = self._get(raw, "amount")
        amount = None
        if not amount_text:
            issues.append(Issue("DQ-REQ-001", "missing_required_field", "error", "Missing amount", field_name="amount"))
        else:
            parsed = parse_amount(amount_text)
            if parsed is None:
                issues.append(Issue("DQ-AMT-001", "invalid_amount", "error", "Amount is not numeric",
                                    field_name="amount", observed_value=amount_text[:64]))
            elif parsed <= 0:
                issues.append(Issue("DQ-AMT-002", "invalid_amount", "error", "Amount must be positive",
                                    field_name="amount", observed_value=amount_text, expected_value="> 0"))
            elif parsed.quantize(AMOUNT_QUANTUM) <= 0:
                issues.append(Issue("DQ-AMT-004", "invalid_amount", "error", "Amount rounds to zero at 4 decimal places",
                                    field_name="amount", observed_value=amount_text, expected_value=">= 0.0001"))
            else:
                amount = parsed.quantize(AMOUNT_QUANTUM)
                if amount != parsed:
                    original["amount"] = amount_text

        fx_rate = Decimal(c.DEMO_FX_TO_USD[currency]) if currency else None
        amount_usd = (amount * fx_rate).quantize(AMOUNT_QUANTUM) if amount is not None and fx_rate else None
        if amount_usd is not None and amount_usd > MAX_PLAUSIBLE_AMOUNT:
            issues.append(Issue("DQ-AMT-003", "invalid_amount", "error", "Amount exceeds plausible maximum",
                                field_name="amount", observed_value=amount_text, expected_value=f"<= {MAX_PLAUSIBLE_AMOUNT} USD"))
            amount = None

        origin = self._account(raw, "origin_account", issues)
        destination = self._account(raw, "destination_account", issues)

        # Channel
        channel_text = self._get(raw, "channel")
        channel = None
        if not channel_text:
            issues.append(Issue("DQ-REQ-001", "missing_required_field", "error", "Missing channel", field_name="channel"))
        else:
            channel = c.CHANNEL_ALIASES.get(c.key(channel_text))
            if channel is None:
                channel = "other"
                issues.append(Issue("DQ-CHN-001", "invalid_format", "warning", "Unknown channel mapped to 'other'",
                                    field_name="channel", observed_value=channel_text[:64]))
            if c.key(channel_text) != channel.replace("_", ""):
                original["channel"] = channel_text

        status_text = self._get(raw, "status")
        status = c.STATUS_ALIASES.get(c.key(status_text), "posted") if status_text else "posted"
        if status_text and c.key(status_text) not in c.STATUS_ALIASES:
            issues.append(Issue("DQ-STS-001", "invalid_format", "warning", "Unknown status treated as 'posted'",
                                field_name="status", observed_value=status_text[:32]))

        direction_text = self._get(raw, "direction").lower()
        direction = direction_text if direction_text in c.DIRECTIONS else None

        # Duplicates
        ref = self._get(raw, "transaction_id") or None
        if ref:
            if ref in self.seen_refs:
                issues.append(Issue("DQ-DUP-001", "duplicate_record", "error", "Duplicate transaction_id within file",
                                    field_name="transaction_id", observed_value=ref))
            elif ref in self.existing_refs:
                issues.append(Issue("DQ-DUP-002", "duplicate_record", "error", "transaction_id already ingested",
                                    field_name="transaction_id", observed_value=ref))
            self.seen_refs.add(ref)

        if any(i.rejects for i in issues):
            return RowResult(row_number, raw, None, issues)

        normalized = {
            "transaction_ref": ref,
            "occurred_at": occurred_at,
            "amount": amount,
            "currency": currency,
            "amount_usd": amount_usd,
            "fx_rate_to_usd": fx_rate,
            "channel": channel,
            "direction": direction,
            "transaction_status": status,
            "origin_ref": origin,
            "destination_ref": destination,
            "origin_bank": self._get(raw, "origin_bank") or None,
            "destination_bank": self._get(raw, "destination_bank") or None,
            "origin_name": self._get(raw, "origin_name") or None,
            "destination_name": self._get(raw, "destination_name") or None,
            "description": self._get(raw, "description")[:1000] or None,
            "original_values": original,
        }
        normalized["dedupe_hash"] = dedupe_hash(normalized)
        if not ref and normalized["dedupe_hash"] in self.seen_hashes:
            issues.append(Issue("DQ-DUP-003", "duplicate_record", "warning",
                                "Identical event already seen in this file (no transaction_id to disambiguate)"))
        self.seen_hashes.add(normalized["dedupe_hash"])

        label_text = self._get(raw, "is_suspicious")
        if label_text:
            label = parse_bool(label_text)
            typology = self._get(raw, "typology").lower() or None
            if label is not None:
                normalized["label"] = {
                    "is_suspicious": label,
                    "typology": typology if label and typology in c.TYPOLOGIES else None,
                }
        return RowResult(row_number, raw, normalized, issues)
