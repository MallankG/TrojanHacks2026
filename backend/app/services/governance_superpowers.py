"""PRD 20.2: bounded governance evaluation. Receipts contain no raw source values."""
from collections import Counter, deque
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from difflib import get_close_matches
import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class FieldRule(StrictModel):
    kind: Literal["string", "decimal", "timestamp"] = "string"
    required: bool = True
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    allowed: list[str] = Field(default_factory=list, max_length=100)
    references: list[str] = Field(default_factory=list, max_length=500)
    timezone_required: bool = True
    sensitive: bool = False

    @model_validator(mode="after")
    def bounds(self):
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum must be <= maximum")
        return self


class Contract(StrictModel):
    version: str = Field(min_length=1, max_length=80)
    fields: dict[str, FieldRule] = Field(min_length=1, max_length=50)
    currency_field: str = "currency"
    amount_field: str = "amount"
    timestamp_field: str = "timestamp"
    id_field: str = "transaction_id"
    unit: Literal["major", "minor"] = "major"
    freshness_hours: int = Field(default=24, ge=1, le=8760)
    valid_sla: float = Field(default=0.995, ge=0, le=1)


class MetadataNode(StrictModel):
    id: str = Field(min_length=1, max_length=120)
    kind: Literal["dataset", "field", "owner", "steward", "contract", "pipeline", "transformation", "quality_check", "feature", "model", "policy", "dashboard", "alert", "graph_edge", "case", "report", "outcome"]
    label: str = Field(min_length=1, max_length=160)


class MetadataEdge(StrictModel):
    source: str
    target: str


class Product(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    source_system: str = Field(min_length=1, max_length=120)
    owner: str = Field(min_length=1, max_length=120)
    steward: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1, max_length=240)
    classification: Literal["internal", "confidential", "restricted"] = "restricted"
    retention_days: int = Field(ge=1, le=36500)
    review_at: datetime
    access_conditions: str = Field(min_length=1, max_length=500)
    example_query: str = Field(default="Which alerts depend on amount?", max_length=300)
    contract: Contract
    nodes: list[MetadataNode] = Field(default_factory=list, max_length=200)
    edges: list[MetadataEdge] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def graph(self):
        ids = {n.id for n in self.nodes}
        if len(ids) != len(self.nodes) or any(e.source not in ids or e.target not in ids for e in self.edges):
            raise ValueError("Metadata graph requires unique nodes and known edge endpoints")
        if self.review_at.tzinfo is None:
            raise ValueError("review_at must include a timezone")
        return self


class Evaluation(StrictModel):
    rows: list[dict[str, str | int | float | bool | None]] = Field(max_length=500)
    baseline: list[dict[str, str | int | float | bool | None]] = Field(default_factory=list, max_length=500)
    source_checksum: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    observed_unit: Literal["major", "minor"] = "major"
    ledger_totals: dict[str, Decimal] = Field(default_factory=dict, max_length=30)
    document: str = Field(default="", max_length=10000)

    @model_validator(mode="after")
    def bounded_cells(self):
        for row in self.rows + self.baseline:
            if len(row) > 50 or any(len(k) > 120 or (isinstance(v, str) and len(v) > 1000) for k, v in row.items()):
                raise ValueError("Rows allow at most 50 fields, 120-character keys and 1000-character values")
        return self


SENSITIVE = re.compile(r"account|acct|email|phone|address|ssn|tax.?id|passport|birth|name", re.I)
DOCUMENT_PII = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\b\d{3}-\d{2}-\d{4}\b|\b\d{3}[- .]\d{3}[- .]\d{4}\b")


def timestamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo else None
    except ValueError:
        return None


def decimal(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def check_row(row: dict, contract: Contract, now: datetime) -> list[dict]:
    failures = []
    for name, rule in contract.fields.items():
        value = row.get(name)
        code = None
        if value is None or value == "":
            code = "required" if rule.required else None
        elif rule.kind == "decimal":
            amount = decimal(value)
            if amount is None:
                code = "type"
            elif (rule.minimum is not None and amount < rule.minimum) or (rule.maximum is not None and amount > rule.maximum):
                code = "range"
        elif rule.kind == "timestamp":
            parsed = timestamp(value)
            if parsed is None:
                if rule.timezone_required:
                    code = "timezone_or_type"
                else:
                    try:
                        datetime.fromisoformat(str(value))
                    except ValueError:
                        code = "type"
            elif parsed > now + timedelta(minutes=5):
                code = "future_timestamp"
            elif parsed < now - timedelta(hours=contract.freshness_hours):
                code = "freshness"
        elif not isinstance(value, str):
            code = "type"
        if code is None and value is not None and value != "":
            if rule.allowed and str(value) not in rule.allowed:
                code = "enum"
            elif rule.references and str(value) not in rule.references:
                code = "referential"
        if code:
            failures.append({"field": name, "code": code, "action": "quarantine"})
    return failures


def impact(product: Product, fields: list[str]) -> dict:
    known = {n.id for n in product.nodes}
    seeds = {f"field:{f}" for f in fields} & known
    visited = set(seeds)
    queue = deque(seeds)
    while queue:
        current = queue.popleft()
        for edge in product.edges:
            if edge.source == current and edge.target not in visited:
                visited.add(edge.target)
                queue.append(edge.target)
    nodes = [n.model_dump() for n in product.nodes if n.id in visited - seeds]
    return {"nodes": nodes, "counts": dict(Counter(n["kind"] for n in nodes)), "total": len(nodes),
            "coverage": "Registered dependencies only; unregistered copies and external systems are unknown."}


def evaluate(product: Product, request: Evaluation, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    contract = product.contract
    headers = set().union(*(r.keys() for r in request.rows)) if request.rows else set()
    missing = set(contract.fields) - headers
    extra = headers - set(contract.fields)
    drift = []
    for field in sorted(missing):
        candidates = get_close_matches(field, sorted(extra), n=1, cutoff=0.45)
        drift.append({"field": field, "kind": "missing_field", "proposed_mapping": candidates[0] if candidates else None,
                      "requires_review": True})
    if request.observed_unit != contract.unit:
        drift.append({"field": contract.amount_field, "kind": "currency_unit_shift", "requires_review": True})
    checked = []
    seen = set()
    totals: dict[str, Decimal] = {}
    for index, row in enumerate(request.rows):
        failures = check_row(row, contract, now)
        ref = row.get(contract.id_field)
        if ref not in (None, ""):
            if ref in seen:
                failures.append({"field": contract.id_field, "code": "duplicate", "action": "quarantine"})
            seen.add(ref)
        if request.observed_unit != contract.unit:
            failures.append({"field": contract.amount_field, "code": "currency_unit_shift", "action": "quarantine"})
        for failure in failures:
            if failure["code"] in ("enum", "timezone_or_type", "type"):
                drift.append({"field": failure["field"], "kind": failure["code"], "requires_review": True})
            elif failure["code"] == "required" and extra:
                candidates = get_close_matches(failure["field"], sorted(extra), n=1, cutoff=0.45)
                if candidates:
                    drift.append({"field": failure["field"], "kind": "possible_rename", "proposed_mapping": candidates[0], "requires_review": True})
        checked.append({"row": index + 1, "status": "quarantined" if failures else "accepted", "failures": failures})
        amount = decimal(row.get(contract.amount_field))
        currency = str(row.get(contract.currency_field, "unknown"))
        if not failures and amount is not None:
            totals[currency] = totals.get(currency, Decimal(0)) + amount
    # Distribution signal is an explicit heuristic, never an automatic semantic remapping.
    def median(rows):
        values = sorted(v for r in rows if (v := decimal(r.get(contract.amount_field))) is not None)
        return values[len(values) // 2] if len(values) >= 10 else None
    before, after = median(request.baseline), median(request.rows)
    if before and after is not None and (after / before > 10 or after / before < Decimal("0.1")):
        drift.append({"field": contract.amount_field, "kind": "distribution_shift", "requires_review": True})
    drift = list({json.dumps(d, sort_keys=True): d for d in drift}.values())
    reconciliation = [{"currency": currency, "incoming": str(totals.get(currency, Decimal(0))), "ledger": str(expected),
                       "difference": str(totals.get(currency, Decimal(0)) - expected),
                       "status": "matched" if totals.get(currency, Decimal(0)) == expected else "unresolved"}
                      for currency, expected in sorted(request.ledger_totals.items())]
    size = len(request.rows)
    slots = sum(1 for r in contract.fields.values() if r.required) * size
    present = sum(r.get(f) not in (None, "") for r in request.rows for f, rule in contract.fields.items() if rule.required)
    accepted = sum(r["status"] == "accepted" for r in checked)
    fresh = sum(bool((t := timestamp(r.get(contract.timestamp_field))) and now - timedelta(hours=contract.freshness_hours) <= t <= now + timedelta(minutes=5)) for r in request.rows)
    components = {"provenance": 1.0 if request.source_checksum else None,
                  "completeness": present / slots if slots else None,
                  "validity": accepted / size if size else None,
                  "freshness": fresh / size if size else None,
                  "reconciliation": sum(r["status"] == "matched" for r in reconciliation) / len(reconciliation) if reconciliation else None}
    known = [v for v in components.values() if v is not None]
    affected_fields = sorted({f["field"] for r in checked for f in r["failures"]} | {d["field"] for d in drift})
    blast = impact(product, affected_fields)
    sensitive = [{"field": f, "classification": "probable_sensitive", "display": "[REDACTED]"}
                 for f in sorted(headers) if SENSITIVE.search(f) or (f in contract.fields and contract.fields[f].sensitive)]
    expiry = now + timedelta(days=product.retention_days)
    expiry_nodes = impact(product, list(contract.fields))
    incidents = []
    if size and (accepted / size < contract.valid_sla or drift or any(r["status"] != "matched" for r in reconciliation)):
        incidents.append({"title": "Contract, drift and reconciliation incident", "owner": product.steward,
                          "status": "open", "due_at": (now + timedelta(hours=24)).isoformat(),
                          "timeline": [{"at": now.isoformat(), "event": "Checks correlated", "rejected": size - accepted,
                                        "drift_signals": len(drift), "affected_dependencies": blast["total"]}],
                          "root_cause": "Unconfirmed; review field failures and source changes before assigning cause."})
    receipt = {"policy": "trust-v1: equal mean of observed components; missing components remain unknown",
               "components": components, "score": round(100 * sum(known) / len(known), 1) if known else None,
               "coverage": len(known) / len(components), "caveats": [k + " not supplied" for k, v in components.items() if v is None],
               "contract_version": contract.version, "evaluated_at": now.isoformat(),
               "provenance_note": "Supplied checksum is a source assertion; it is not independently verified."}
    result = {"rows": checked, "summary": {"total": size, "accepted": accepted, "quarantined": size - accepted},
              "drift": drift, "trust": receipt, "impact": blast,
              "remediation": {"status": "proposed", "approval_required": True,
                              "steps": ["Review source semantics and proposed mapping", "Repair source or version the contract", "Re-ingest quarantined rows", "Rebuild registered dependencies and verify results"],
                              "execution": "Planning only; no source writes, deletions or automatic rebuilds."},
              "sensitive": {"fields": sensitive, "document_matches": len(DOCUMENT_PII.findall(request.document)),
                            "prompt_warning": bool(sensitive or DOCUMENT_PII.search(request.document)), "policy": "Raw values excluded from receipts and audit logs; discovery is heuristic."},
              "retention": {"class_days": product.retention_days, "new_record_expiry": expiry.isoformat(),
                            "downstream": expiry_nodes, "action": "Review downstream expiry/rebuild obligations; no erasure performed."},
              "reconciliation": {"rows": reconciliation, "precedence": "Authorized ledger totals take precedence; comparison uses accepted rows only; currencies never mixed.",
                                 "caveats": "Missing legs, reversals and balances require rail-specific feeds; no inference from absent feeds."},
              "incidents": incidents}
    result["receipt_sha256"] = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return result


def demo(scenario: str = "drift", now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    fields = {"transaction_id": FieldRule(), "amount": FieldRule(kind="decimal", minimum=Decimal("0.01")),
              "currency": FieldRule(allowed=["USD", "EUR"]), "timestamp": FieldRule(kind="timestamp"),
              "destination_account": FieldRule(sensitive=True)}
    nodes = [MetadataNode(id="field:destination_account", kind="field", label="Destination account"),
             MetadataNode(id="field:amount", kind="field", label="Amount")]
    nodes += [MetadataNode(id=k, kind=k, label=label) for k, label in [
        ("dataset", "Synthetic payments"), ("owner", "Payments Operations"), ("steward", "Data Steward"),
        ("contract", "payments-v1"), ("pipeline", "CSV ingestion"), ("transformation", "Canonical mapping"),
        ("quality_check", "Contract checks"), ("feature", "Fan-in / amount features"), ("model", "Ranker"),
        ("policy", "Review policy"), ("dashboard", "Risk queue"), ("alert", "DEMO-AL-1"),
        ("graph_edge", "Payment edge"), ("case", "DEMO-CASE-1"), ("report", "Investigation report"), ("outcome", "Human review")]]
    edges = [MetadataEdge(source=a, target=b) for a, b in [
        ("dataset", "field:amount"), ("dataset", "field:destination_account"), ("owner", "dataset"),
        ("steward", "contract"), ("contract", "quality_check"), ("pipeline", "transformation"),
        ("quality_check", "transformation"), ("field:destination_account", "graph_edge"),
        ("field:amount", "feature"), ("graph_edge", "feature"), ("transformation", "feature"),
        ("feature", "model"), ("model", "alert"), ("policy", "alert"), ("alert", "dashboard"),
        ("alert", "case"), ("case", "report"), ("report", "outcome")]]
    product = Product(name="Synthetic payments", source_system="demo-payments", owner="Payments Operations", steward="Data Steward",
                      purpose="Synthetic transaction investigation", retention_days=30, review_at=now + timedelta(days=30),
                      access_conditions="Steward approval required", contract=Contract(version="payments-v1", fields=fields), nodes=nodes, edges=edges)
    rows = [{"transaction_id": f"DEMO-{i}", "amount": "100", "currency": "USD", "timestamp": now.isoformat(), "destination_account": "SYNTHETIC-ACCOUNT"} for i in range(12)]
    baseline = [dict(r) for r in rows]
    if scenario == "drift":
        for r in rows[:3]:
            r["beneficiary_account"] = r.pop("destination_account")
        rows[3]["currency"] = "US cents"
        rows[4]["timestamp"] = now.replace(tzinfo=None).isoformat()
        rows[5]["transaction_id"] = rows[6]["transaction_id"]
    evaluation = Evaluation(rows=rows, baseline=baseline, ledger_totals={"USD": Decimal("1200")}, document="Synthetic contact: example@example.invalid")
    return {"product": product.model_dump(mode="json"), "input": evaluation.model_dump(mode="json"), "assessment": evaluate(product, evaluation, now),
            "mode": "synthetic_demo", "persisted": False}
