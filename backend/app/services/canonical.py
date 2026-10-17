"""Canonical transaction schema (canonical-txn-v1), column auto-mapping and value normalization tables."""

import re

REQUIRED_FIELDS = ("timestamp", "amount", "currency", "origin_account", "destination_account", "channel")
OPTIONAL_FIELDS = (
    "transaction_id",
    "origin_bank",
    "destination_bank",
    "origin_name",
    "destination_name",
    "description",
    "status",
    "direction",
    "is_suspicious",
    "typology",
)
CANONICAL_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS

FIELD_DESCRIPTIONS = {
    "transaction_id": "Source-system transaction identifier (unique per tenant)",
    "timestamp": "Event time; ISO-8601 preferred. Naive times are interpreted as UTC",
    "amount": "Positive amount in the transaction currency",
    "currency": "ISO-4217 code (common currency names are normalized)",
    "origin_account": "Sending account identifier",
    "destination_account": "Receiving account identifier",
    "channel": "ach, wire, card, cash, check, p2p, internal_transfer, crypto, other",
    "origin_bank": "Sending institution identifier",
    "destination_bank": "Receiving institution identifier",
    "origin_name": "Sending party name (used for entity resolution)",
    "destination_name": "Receiving party name (used for entity resolution)",
    "description": "Free-text memo (treated as untrusted input)",
    "status": "posted, pending, reversed, failed",
    "direction": "inbound, outbound, internal, external",
    "is_suspicious": "Ground-truth label for synthetic/benchmark data only",
    "typology": "Ground-truth typology for synthetic/benchmark data only",
}

# Aliases are compared after lower-casing and stripping non-alphanumerics.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "transaction_id": ("transactionid", "txnid", "txid", "id", "transactionref", "reference"),
    "timestamp": ("timestamp", "datetime", "time", "date", "occurredat", "transactiondate", "postedat"),
    "amount": ("amount", "amountpaid", "amt", "value", "transactionamount"),
    "currency": ("currency", "paymentcurrency", "ccy", "currencycode"),
    "origin_account": ("originaccount", "fromaccount", "account", "sender", "senderaccount", "sourceaccount", "nameorig"),
    "destination_account": (
        "destinationaccount", "toaccount", "account1", "receiver", "receiveraccount", "beneficiaryaccount", "namedest",
    ),
    "channel": ("channel", "paymentformat", "paymenttype", "type", "method", "rail"),
    "origin_bank": ("originbank", "frombank", "senderbank"),
    "destination_bank": ("destinationbank", "tobank", "receiverbank", "beneficiarybank"),
    "origin_name": ("originname", "sendername", "originatorname", "fromname"),
    "destination_name": ("destinationname", "receivername", "beneficiaryname", "toname"),
    "description": ("description", "memo", "narrative", "remittanceinfo"),
    "status": ("status", "transactionstatus"),
    "direction": ("direction",),
    "is_suspicious": ("issuspicious", "islaundering", "isfraud", "label", "suspicious"),
    "typology": ("typology", "pattern", "launderingtype"),
}

CHANNEL_ALIASES = {
    "ach": "ach", "achcredit": "ach", "achdebit": "ach", "directdeposit": "ach",
    "wire": "wire", "wiretransfer": "wire", "swift": "wire", "fedwire": "wire",
    "card": "card", "creditcard": "card", "debitcard": "card", "pos": "card",
    "cash": "cash", "cashdeposit": "cash", "cashwithdrawal": "cash", "atm": "cash",
    "check": "check", "cheque": "check",
    "p2p": "p2p", "zelle": "p2p", "venmo": "p2p", "peertopeer": "p2p",
    "internal": "internal_transfer", "internaltransfer": "internal_transfer", "booktransfer": "internal_transfer",
    "reinvestment": "internal_transfer",
    "crypto": "crypto", "bitcoin": "crypto", "cryptocurrency": "crypto",
    "other": "other",
}

STATUS_ALIASES = {
    "posted": "posted", "settled": "posted", "completed": "posted", "complete": "posted", "success": "posted",
    "pending": "pending", "processing": "pending",
    "reversed": "reversed", "returned": "reversed", "chargeback": "reversed",
    "failed": "failed", "rejected": "failed", "declined": "failed",
}

DIRECTIONS = {"inbound", "outbound", "internal", "external"}

TYPOLOGIES = {
    "rapid_in_out", "layering", "mule_network", "funnel", "fan_out", "cycle", "structuring",
    "dormant_reactivation", "behavioral_deviation", "scam_proceeds", "model_anomaly", "other",
}

CURRENCY_NAMES = {
    "usdollar": "USD", "dollar": "USD", "euro": "EUR", "ukpound": "GBP", "pound": "GBP", "yen": "JPY",
    "canadiandollar": "CAD", "mexicanpeso": "MXN", "rupee": "INR", "yuan": "CNY", "swissfranc": "CHF",
    "australiandollar": "AUD", "brazilreal": "BRL", "saudiriyal": "SAR", "ruble": "RUB",
    "shekel": "ILS", "bitcoin": "XBT",
}

# Demonstration FX rates (USD per unit). Not market data; replace with a governed rate source in production.
DEMO_FX_TO_USD = {
    "USD": "1", "EUR": "1.08", "GBP": "1.27", "CAD": "0.73", "MXN": "0.055", "JPY": "0.0067", "INR": "0.012",
    "CNY": "0.14", "CHF": "1.12", "AUD": "0.66", "BRL": "0.18", "SAR": "0.27", "RUB": "0.011", "ILS": "0.27",
    "XBT": "60000",
}
KNOWN_CURRENCIES = set(DEMO_FX_TO_USD)

NULL_ACCOUNT_TOKENS = {"", "unknown", "n/a", "na", "null", "none", "?", "-", "0", "tbd"}
ACCOUNT_PATTERN = re.compile(r"^[A-Za-z0-9_\-:.]{2,64}$")

ENTITY_SUFFIXES = re.compile(r"\b(LLC|L\.L\.C|INC|INCORPORATED|CORP|CORPORATION|LTD|LIMITED|CO|COMPANY|PLC)\b\.?")


def key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def normalize_entity_name(name: str) -> str:
    upper = name.upper()
    upper = ENTITY_SUFFIXES.sub(" ", upper)
    upper = re.sub(r"[^A-Z0-9 ]", " ", upper)
    return re.sub(r"\s+", " ", upper).strip()


def auto_map(headers: list[str], overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Map canonical fields to source columns. Explicit overrides win; aliases fill the rest."""
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for field, column in (overrides or {}).items():
        if field in CANONICAL_FIELDS and column:
            mapping[field] = column
            used.add(column)
    by_key = {key(h): h for h in headers}
    for field in CANONICAL_FIELDS:
        if field in mapping:
            continue
        for alias in (key(field),) + FIELD_ALIASES.get(field, ()):
            column = by_key.get(alias)
            if column and column not in used:
                mapping[field] = column
                used.add(column)
                break
    return mapping
