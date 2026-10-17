"""Deterministic mock implementations of every Protocol in `tracelayer_agents.interfaces`.

They let the whole pipeline run with no teammate code, no network and no keys. The mocks that
share data (ingestion → quality → graph → scorers → flows) do so through one `MockDataStore`;
`registry.get_services()` wires them to the same store.

These are heuristics, not models: scores come from degree and velocity counts and every detector
reports `model_version="mock-0"`. Nothing here reads scenario or typology labels.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import networkx as nx
import pandas as pd

from tracelayer_agents.mocks import patterns
from tracelayer_agents.schemas import (
    Alert,
    AuditEvent,
    CaseRecord,
    DetectorOutput,
    EvidenceBundle,
    EvidenceTransaction,
    Factor,
    FlowFindings,
    FlowPattern,
    FusionResult,
    GraphSnapshot,
    IngestionResult,
    NarrativeDraft,
    QualityIssue,
    QualityReport,
    RetrievedChunk,
    SubgraphEdge,
    SubgraphJSON,
    SubgraphNode,
)

FIXTURES = Path(__file__).parent / "fixtures"
SCENARIOS = ("fan_out", "pass_through", "cycle", "bad_batch")

MOCK_MODEL_VERSION = "mock-0"
FEATURE_SCHEMA_VERSION = "mock-fv0"
CONTRACT_VERSION = "mock_txn_contract_v1"
MAX_SUBGRAPH_HOPS = 2  # the Protocol accepts 1–3; the mock never walks further than 2

# PRD §13 `transactions` columns.
CANONICAL_COLUMNS = (
    "transaction_id",
    "source_row_ref",
    "timestamp",
    "amount",
    "currency",
    "channel",
    "direction",
    "origin_account_id",
    "destination_account_id",
    "transaction_status",
)
REQUIRED_COLUMNS = (
    "transaction_id",
    "source_row_ref",
    "timestamp",
    "amount",
    "currency",
    "origin_account_id",
    "destination_account_id",
)
# Subset of ISO 4217 that is enough for synthetic data; anything else is invalid.
ISO_4217 = frozenset(
    {"USD", "EUR", "GBP", "CAD", "MXN", "JPY", "CHF", "AUD", "CNY", "INR", "HKD", "SGD"}
)

Clock = Callable[[], datetime]


def _now() -> datetime:
    return datetime.now(UTC)


def _sha256(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


# --------------------------------------------------------------------------- parsing


def parse_timestamp(raw: str) -> datetime | None:
    """ISO 8601 with an explicit offset, else None (naive or unparseable)."""
    try:
        value = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    return value if value.tzinfo is not None else None


def parse_amount(raw: str) -> Decimal | None:
    """A finite, positive decimal, else None."""
    try:
        value = Decimal(raw.strip())
    except InvalidOperation:
        return None
    return value if value.is_finite() and value > 0 else None


def load_accounts(path: Path = FIXTURES / "accounts.csv") -> frozenset[str]:
    with path.open(newline="") as fh:
        return frozenset(row["account_id"] for row in csv.DictReader(fh))


# --------------------------------------------------------------------------- shared store


@dataclass
class BatchData:
    batch_id: str
    ingestion_run_id: str
    source_asset_id: str
    source_hash: str
    raw: pd.DataFrame  # every source row, as strings, exactly as read
    transactions: list[EvidenceTransaction]  # accepted, normalized rows


@dataclass
class SnapshotData:
    snapshot: GraphSnapshot
    graph: nx.MultiDiGraph


@dataclass
class MockDataStore:
    """In-memory stand-in for the backend's batch, account and graph tables."""

    accounts: frozenset[str] = field(default_factory=load_accounts)
    batches: dict[str, BatchData] = field(default_factory=dict)
    snapshots: dict[str, SnapshotData] = field(default_factory=dict)

    def batch(self, batch_id: str) -> BatchData:
        try:
            return self.batches[batch_id]
        except KeyError:
            raise KeyError(f"unknown transaction_batch_id {batch_id!r}") from None

    def snapshot(self, snapshot_id: str) -> SnapshotData:
        try:
            return self.snapshots[snapshot_id]
        except KeyError:
            raise KeyError(f"unknown graph_snapshot_id {snapshot_id!r}") from None


def bounded_subgraph(graph: nx.MultiDiGraph, tx_ids: list[str], hops: int) -> nx.MultiDiGraph:
    """Accounts within `hops` (capped at `MAX_SUBGRAPH_HOPS`) of the seed transactions' endpoints,
    ignoring direction, plus every transaction between those accounts."""
    if not 1 <= hops <= 3:
        raise ValueError(f"hops must be between 1 and 3, got {hops}")
    hops = min(hops, MAX_SUBGRAPH_HOPS)
    edges = {key: (u, v) for u, v, key in graph.edges(keys=True)}
    missing = sorted(set(tx_ids) - edges.keys())
    if missing:
        raise KeyError(f"transactions not in snapshot: {missing[:5]}")
    frontier = {acc for tx_id in tx_ids for acc in edges[tx_id]}
    reached = set(frontier)
    undirected = graph.to_undirected(as_view=True)
    for _ in range(hops - 1):
        frontier = {n for acc in frontier for n in undirected.neighbors(acc)} - reached
        reached |= frontier
    return graph.subgraph(reached).copy()


# --------------------------------------------------------------------------- backend mocks


class MockIngestion:
    """Parses a canonical-schema CSV. Rows that can't become a canonical transaction (missing IDs,
    duplicate `transaction_id`, unparseable timestamp or amount, invalid currency) are counted as
    rejected; the raw rows are kept so `MockQuality` can report on them."""

    def __init__(self, store: MockDataStore, clock: Clock = _now) -> None:
        self.store = store
        self.clock = clock

    def ingest(self, payload: bytes | str, source_asset_id: str) -> IngestionResult:
        started = self.clock()
        data = self._read(payload)
        source_hash = _sha256(data)
        batch_id = f"batch_{source_hash[:12]}"
        run_id = f"run_{source_hash[:12]}"

        if batch_id not in self.store.batches:  # idempotent per checksum
            raw = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
            self.store.batches[batch_id] = BatchData(
                batch_id=batch_id,
                ingestion_run_id=run_id,
                source_asset_id=source_asset_id,
                source_hash=source_hash,
                raw=raw,
                transactions=self._accept(raw),
            )
        batch = self.store.batches[batch_id]
        accepted = len(batch.transactions)
        rows = len(batch.raw)
        status = "completed" if accepted == rows else ("partial" if accepted else "failed")
        return IngestionResult(
            ingestion_run_id=run_id,
            transaction_batch_id=batch_id,
            source_asset_id=source_asset_id,
            file_id=f"file_{source_hash[:12]}",
            status=status,
            row_count=rows,
            accepted_count=accepted,
            rejected_count=rows - accepted,
            started_at=started,
            completed_at=self.clock(),
        )

    @staticmethod
    def _read(payload: bytes | str) -> bytes:
        """Bytes are file content; a str is a fixture scenario name, a file path, or CSV text."""
        if isinstance(payload, bytes):
            return payload
        if payload in SCENARIOS:
            return (FIXTURES / f"{payload}.csv").read_bytes()
        if "\n" in payload:
            return payload.encode()
        path = Path(payload)
        if path.is_file():
            return path.read_bytes()
        raise FileNotFoundError(f"not a fixture name, file path or CSV text: {payload[:80]!r}")

    @staticmethod
    def _accept(raw: pd.DataFrame) -> list[EvidenceTransaction]:
        if not set(REQUIRED_COLUMNS) <= set(raw.columns):
            return []
        accepted: list[EvidenceTransaction] = []
        seen: set[str] = set()
        for row in raw.to_dict("records"):
            if any(not row[c].strip() for c in REQUIRED_COLUMNS):
                continue
            tx_id = row["transaction_id"].strip()
            ts = parse_timestamp(row["timestamp"])
            amount = parse_amount(row["amount"])
            currency = row["currency"].strip()
            if tx_id in seen or ts is None or amount is None or currency not in ISO_4217:
                continue
            seen.add(tx_id)
            accepted.append(EvidenceTransaction(
                transaction_id=tx_id,
                source_row_ref=row["source_row_ref"].strip(),
                timestamp=ts,
                amount=amount,
                currency=currency,
                channel=row.get("channel") or None,
                direction=row.get("direction") or None,
                origin_account_id=row["origin_account_id"].strip(),
                destination_account_id=row["destination_account_id"].strip(),
                transaction_status=row.get("transaction_status") or None,
            ))
        return accepted


# rule_id -> (severity, owner_role, steward_role, field, issue_type, remediation_note)
_RULES: dict[str, tuple[str, str, str, str | None, str, str]] = {
    "schema_columns_present": (
        "critical", "transactions_data_owner", "transactions_data_steward", None,
        "missing_column", "Source file is missing canonical columns; fix the export mapping.",
    ),
    "transaction_id_unique": (
        "critical", "transactions_data_owner", "transactions_data_steward", "transaction_id",
        "duplicate", "Duplicate transaction IDs; confirm with the source system which row is "
        "authoritative.",
    ),
    "timestamp_iso8601_tz": (
        "critical", "transactions_data_owner", "transactions_data_steward", "timestamp",
        "invalid_value", "Timestamp is unparseable or has no timezone; re-export as ISO 8601 UTC.",
    ),
    "timestamp_utc": (
        "warning", "transactions_data_owner", "transactions_data_steward", "timestamp",
        "non_utc", "Timestamp carries a non-UTC offset; it was normalized to UTC and the source "
        "value kept.",
    ),
    "amount_positive_decimal": (
        "critical", "transactions_data_owner", "transactions_data_steward", "amount",
        "invalid_value", "Amount is not a positive decimal.",
    ),
    "currency_iso4217": (
        "critical", "transactions_data_owner", "transactions_data_steward", "currency",
        "invalid_value", "Currency is not an ISO 4217 code.",
    ),
    "account_exists": (
        "critical", "transactions_data_owner", "reference_data_steward", None,
        "unresolved_reference", "Account is not in the account master; resolve or onboard it.",
    ),
}


class MockQuality:
    """Runs a small data contract with pandas: completeness, uniqueness, validity and referential
    integrity. Read-only on the batch."""

    def __init__(self, store: MockDataStore, clock: Clock = _now) -> None:
        self.store = store
        self.clock = clock

    def run_contract(self, batch_id: str) -> QualityReport:
        batch = self.store.batch(batch_id)
        df = batch.raw
        found: list[tuple[str, str | None, pd.Series]] = []  # (rule_id, field, failing-row mask)

        missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing_cols:
            found.append(("schema_columns_present", ",".join(missing_cols),
                          pd.Series(True, index=df.index)))
            df = df.reindex(columns=[*df.columns, *missing_cols], fill_value="")

        blank = df[list(REQUIRED_COLUMNS)].apply(lambda col: col.str.strip() == "")
        for col in REQUIRED_COLUMNS:
            if col not in missing_cols:
                found.append((f"required:{col}", col, blank[col]))

        ids = df["transaction_id"].str.strip()
        found.append(("transaction_id_unique", None, ids.ne("") & ids.duplicated(keep=False)))

        ts_raw = df["timestamp"]
        parsed = ts_raw.map(parse_timestamp)
        found.append(("timestamp_iso8601_tz", None, ~blank["timestamp"] & parsed.isna()))
        non_utc = parsed.map(lambda t: t is not None and t.utcoffset() != timedelta(0))
        found.append(("timestamp_utc", None, non_utc.astype(bool)))

        bad_amount = ~blank["amount"] & df["amount"].map(parse_amount).isna()
        found.append(("amount_positive_decimal", None, bad_amount))

        currency = df["currency"].str.strip()
        found.append(("currency_iso4217", None, ~blank["currency"] & ~currency.isin(ISO_4217)))

        for col in ("origin_account_id", "destination_account_id"):
            acc = df[col].str.strip()
            found.append(("account_exists", col, ~blank[col] & ~acc.isin(self.store.accounts)))

        issues = []
        for rule_key, field_name, mask in found:
            if not mask.any():
                continue
            issues.append(self._issue(len(issues) + 1, rule_key, field_name, df[mask], batch))

        severities = {i.severity for i in issues}
        status = "fail" if "critical" in severities else ("qualified" if issues else "pass")
        return QualityReport(
            transaction_batch_id=batch_id,
            contract_version=CONTRACT_VERSION,
            status=status,
            issues=issues,
            rows_checked=len(df),
            checked_at=self.clock(),
        )

    @staticmethod
    def _issue(
        n: int, rule_key: str, field_name: str | None, rows: pd.DataFrame, batch: BatchData
    ) -> QualityIssue:
        if rule_key.startswith("required:"):
            rule_id = "required_field_present"
            severity, owner, steward = _RULES["transaction_id_unique"][:3]
            issue_type, note = "missing_value", f"Required field {field_name!r} is empty."
        else:
            rule_id = rule_key
            severity, owner, steward, default_field, issue_type, note = _RULES[rule_key]
            field_name = field_name or default_field
        refs = rows["source_row_ref"].str.strip() if "source_row_ref" in rows else pd.Series()
        samples = [r if r else f"line_{i + 2}" for i, r in zip(rows.index, refs, strict=False)]
        return QualityIssue(
            check_id=f"chk_{n:04d}",
            rule_id=rule_id,
            asset_id=batch.source_asset_id,
            severity=severity,
            affected_rows=len(rows),
            sample_record_ids=samples[:5],
            owner_role=owner,
            steward_role=steward,
            field_name=field_name,
            issue_type=issue_type,
            remediation_note=note,
        )


class MockGraphService:
    """NetworkX MultiDiGraph per batch. The snapshot ID is a hash of the sorted edge list plus the
    feature schema version, so the same batch always yields the same ID."""

    def __init__(self, store: MockDataStore, clock: Clock = _now) -> None:
        self.store = store
        self.clock = clock

    def build_snapshot(self, batch_id: str) -> GraphSnapshot:
        txs = self.store.batch(batch_id).transactions
        graph = nx.MultiDiGraph()
        for tx in txs:
            graph.add_edge(
                tx.origin_account_id, tx.destination_account_id, key=tx.transaction_id, tx=tx
            )
        edge_list = sorted(
            (t.transaction_id, t.origin_account_id, t.destination_account_id, str(t.amount),
             t.currency, t.timestamp.isoformat())
            for t in txs
        )
        digest = _sha256(json.dumps([FEATURE_SCHEMA_VERSION, edge_list]))
        snapshot_id = f"snap_{digest[:16]}"
        built = self.clock()
        times = [t.timestamp for t in txs] or [built]
        snapshot = GraphSnapshot(
            graph_snapshot_id=snapshot_id,
            transaction_batch_id=batch_id,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            node_count=graph.number_of_nodes(),
            edge_count=graph.number_of_edges(),
            window_start=min(times),
            window_end=max(times),
            built_at=built,
        )
        self.store.snapshots[snapshot_id] = SnapshotData(snapshot, graph)
        return snapshot

    def subgraph(self, tx_ids: list[str], hops: int) -> SubgraphJSON:
        """Uses the most recently built snapshot that contains every requested transaction."""
        if not tx_ids:
            raise ValueError("tx_ids must not be empty")
        for snap in reversed(list(self.store.snapshots.values())):
            keys = {k for _, _, k in snap.graph.edges(keys=True)}
            if set(tx_ids) <= keys:
                return to_subgraph_json(
                    snap.snapshot.graph_snapshot_id, bounded_subgraph(snap.graph, tx_ids, hops)
                )
        raise KeyError(f"no snapshot contains all of {sorted(tx_ids)[:5]}")

    def get_transactions(self, tx_ids: list[str]) -> list[EvidenceTransaction]:
        """Looks the IDs up across every ingested batch (fixture IDs never collide)."""
        by_id = {
            t.transaction_id: t for b in self.store.batches.values() for t in b.transactions
        }
        missing = sorted(set(tx_ids) - by_id.keys())
        if missing:
            raise KeyError(f"unknown transaction_ids: {missing[:5]}")
        return [by_id[tx_id].model_copy(deep=True) for tx_id in tx_ids]


def to_subgraph_json(snapshot_id: str, graph: nx.MultiDiGraph) -> SubgraphJSON:
    return SubgraphJSON(
        graph_snapshot_id=snapshot_id,
        nodes=[SubgraphNode(account_id=acc) for acc in sorted(graph.nodes)],
        edges=[
            SubgraphEdge(
                transaction_id=t.transaction_id,
                origin_account_id=t.origin_account_id,
                destination_account_id=t.destination_account_id,
                amount=t.amount,
                currency=t.currency,
                timestamp=t.timestamp,
            )
            for t in patterns.transactions(graph)
        ],
    )


# --------------------------------------------------------------------------- ML mocks


def _saturate(count: int, half: float) -> float:
    """Map a non-negative count to [0, 1): `half` maps to 0.5."""
    return round(count / (count + half), 6) if count > 0 else 0.0


class MockGraphRiskScorer:
    """Stand-in for GraphSAGE: a transaction is riskier when its sender has many distinct
    recipients or its receiver has many distinct senders."""

    detector_name = "mock_graph_risk"

    def __init__(self, store: MockDataStore) -> None:
        self.store = store

    def score(self, snapshot_id: str) -> DetectorOutput:
        graph = self.store.snapshot(snapshot_id).graph
        scores: dict[str, float] = {}
        factors: dict[str, list[Factor]] = {}
        for tx in patterns.transactions(graph):
            out_deg = len(set(graph.successors(tx.origin_account_id)))
            in_deg = len(set(graph.predecessors(tx.destination_account_id)))
            scores[tx.transaction_id] = _saturate(max(out_deg, in_deg), half=3)
            factors[tx.transaction_id] = [
                Factor(name="origin_distinct_recipients", kind="feature", value=out_deg),
                Factor(name="destination_distinct_senders", kind="feature", value=in_deg),
            ]
        return DetectorOutput(
            detector_name=self.detector_name,
            model_version=MOCK_MODEL_VERSION,
            scores=scores,
            top_factors=factors,
            feature_snapshot_id=snapshot_id,
        )


class MockBehaviorScorer:
    """Stand-ins for XGBoost/IsolationForest and the typology rules:

    - `mock_velocity`: bursts of outgoing transfers from one account, and how quickly funds leave
      an account after arriving in a similar amount.
    - `mock_typology_rules`: flags fan-out, fan-in, pass-through and cycles (`patterns.py`).
    """

    BURST_WINDOW = timedelta(hours=6)

    def __init__(self, store: MockDataStore) -> None:
        self.store = store

    def score(self, snapshot_id: str) -> list[DetectorOutput]:
        graph = self.store.snapshot(snapshot_id).graph
        return [self._velocity(graph, snapshot_id), self._typology(graph, snapshot_id)]

    def _velocity(self, graph: nx.MultiDiGraph, snapshot_id: str) -> DetectorOutput:
        txs = patterns.transactions(graph)
        quick: dict[str, timedelta] = {}  # tx_id -> fastest in→out gap it is part of
        for p in patterns.pass_through(graph):
            tx_in, tx_out = (
                graph.edges[p.account_ids[i], p.account_ids[i + 1], p.transaction_ids[i]]["tx"]
                for i in (0, 1)
            )
            gap = tx_out.timestamp - tx_in.timestamp
            for tx_id in p.transaction_ids:
                quick[tx_id] = min(gap, quick.get(tx_id, gap))

        scores: dict[str, float] = {}
        factors: dict[str, list[Factor]] = {}
        for tx in txs:
            burst = sum(
                1 for _, _, d in graph.out_edges(tx.origin_account_id, data=True)
                if abs(d["tx"].timestamp - tx.timestamp) <= self.BURST_WINDOW
            )
            burst_score = _saturate(burst - 1, half=3)
            dwell = quick.get(tx.transaction_id)
            dwell_score = 0.0
            if dwell is not None:
                window = patterns.PASS_THROUGH_MAX_GAP.total_seconds()
                dwell_score = round(1 - dwell.total_seconds() / (2 * window), 6)
            scores[tx.transaction_id] = max(burst_score, dwell_score)
            factors[tx.transaction_id] = [
                Factor(name="origin_outgoing_6h", kind="feature", value=burst),
                Factor(
                    name="in_out_gap_minutes", kind="feature",
                    value=None if dwell is None else dwell.total_seconds() / 60,
                ),
            ]
        return DetectorOutput(
            detector_name="mock_velocity",
            model_version=MOCK_MODEL_VERSION,
            scores=scores,
            top_factors=factors,
            feature_snapshot_id=snapshot_id,
        )

    @staticmethod
    def _typology(graph: nx.MultiDiGraph, snapshot_id: str) -> DetectorOutput:
        flags = patterns.flags_by_tx(graph)
        scores = {
            tx.transaction_id: (
                min(1.0, 0.7 + 0.15 * (len(flags[tx.transaction_id]) - 1))
                if tx.transaction_id in flags else 0.0
            )
            for tx in patterns.transactions(graph)
        }
        return DetectorOutput(
            detector_name="mock_typology_rules",
            model_version=MOCK_MODEL_VERSION,
            scores=scores,
            top_factors={
                tx_id: [Factor(name=f, kind="rule", value="true") for f in names]
                for tx_id, names in flags.items()
            },
            feature_snapshot_id=snapshot_id,
            typology_flags=flags,
        )


class MockRankFusion:
    """Mean of per-detector percentile ranks. Parameter-free, so `coefficients` is empty."""

    method = "mock_rank_mean"

    def fuse(self, outputs: dict[str, DetectorOutput]) -> FusionResult:
        ranks = [
            pd.Series(out.scores, dtype=float).rank(method="average", pct=True)
            for out in outputs.values()
            if out.status == "ok" and out.scores
        ]
        if not ranks:
            return FusionResult(method=self.method, composite={})
        mean = pd.concat(ranks, axis=1).mean(axis=1, skipna=True)
        return FusionResult(
            method=self.method,
            composite={tx_id: round(float(v), 6) for tx_id, v in sorted(mean.items())},
        )


def _flow_pattern(graph: nx.MultiDiGraph, p: patterns.Pattern) -> FlowPattern:
    txs = [d["tx"] for _, _, k, d in graph.edges(keys=True, data=True) if k in p.transaction_ids]
    currencies = {t.currency for t in txs}
    single = len(currencies) == 1
    return FlowPattern(
        account_ids=list(p.account_ids),
        transaction_ids=list(p.transaction_ids),
        total_amount=sum((t.amount for t in txs), Decimal(0)) if single else None,
        currency=currencies.pop() if single else None,
        window_start=min(t.timestamp for t in txs),
        window_end=max(t.timestamp for t in txs),
    )


class MockFlowInvestigator:
    """Paths, cycles, fan-in/out and pass-through on the 2-hop subgraph around an alert, keeping
    only patterns that touch one of the alert's accounts."""

    def __init__(self, store: MockDataStore) -> None:
        self.store = store

    def investigate(self, alert: Alert, snapshot_id: str) -> FlowFindings:
        graph = bounded_subgraph(
            self.store.snapshot(snapshot_id).graph, alert.tx_ids, MAX_SUBGRAPH_HOPS
        )
        alert_accounts = {
            acc for _, _, k, d in graph.edges(keys=True, data=True) if k in alert.tx_ids
            for acc in (d["tx"].origin_account_id, d["tx"].destination_account_id)
        }

        def relevant(found: list[patterns.Pattern]) -> list[FlowPattern]:
            return [_flow_pattern(graph, p) for p in found if alert_accounts & set(p.account_ids)]

        findings = {
            "paths": relevant(patterns.onward_paths(graph, alert.tx_ids)),
            "cycles": relevant(patterns.cycles(graph)),
            "fan_in": relevant(patterns.fan_in(graph)),
            "fan_out": relevant(patterns.fan_out(graph)),
            "pass_through": relevant(patterns.pass_through(graph)),
        }
        evidence = set(alert.tx_ids)
        for found in findings.values():
            for p in found:
                evidence.update(p.transaction_ids)
        return FlowFindings(
            alert_id=alert.alert_id,
            graph_snapshot_id=snapshot_id,
            evidence_tx_ids=sorted(evidence),
            **findings,
        )


# --------------------------------------------------------------------------- RAG mock

_TOKEN_RE = re.compile(r"[a-z0-9$][a-z0-9$,\-]*")
_STOPWORDS = frozenset(
    "a an and are as at be by can do does for from how i in is it of on or should the this to "
    "what when where which who why with".split()
)


def _tokens(text: str) -> set[str]:
    return {t.strip(",-") for t in _TOKEN_RE.findall(text.lower())} - _STOPWORDS - {""}


class MockPolicyRetriever:
    """Keyword overlap over `fixtures/policy_chunks.json`. Score = share of query terms found in
    the chunk's keywords, title, section or excerpt.

    Supported filters (exact match): `source_type`, `doc_title`, `chunk_id`. Any other key raises.
    Note: fixture excerpts are paraphrases labeled as such; the real corpus must be verbatim."""

    FILTER_KEYS = frozenset({"source_type", "doc_title", "chunk_id"})

    def __init__(self, path: Path = FIXTURES / "policy_chunks.json") -> None:
        self.chunks: list[dict[str, Any]] = json.loads(path.read_text())

    def search(self, query: str, filters: dict[str, Any], top_k: int) -> list[RetrievedChunk]:
        if top_k < 1:
            raise ValueError("top_k must be >= 1")
        unknown = set(filters) - self.FILTER_KEYS
        if unknown:
            raise ValueError(f"unsupported filter keys: {sorted(unknown)}")
        terms = _tokens(query)
        if not terms:
            return []
        hits = []
        for chunk in self.chunks:
            if any(chunk.get(k) != v for k, v in filters.items()):
                continue
            vocab = set(chunk["keywords"]) | _tokens(
                f"{chunk['doc_title']} {chunk.get('section', '')} {chunk['excerpt']}"
            )
            score = round(len(terms & vocab) / len(terms), 6)
            if score > 0:
                hits.append(RetrievedChunk.model_validate({**chunk, "score": score}))
        hits.sort(key=lambda c: (-c.score, c.chunk_id))
        return hits[:top_k]


# --------------------------------------------------------------------------- persistence


class InMemoryCaseRepository:
    def __init__(self) -> None:
        self.alerts: dict[str, Alert] = {}
        self.evidence: dict[str, EvidenceBundle] = {}
        self.cases: dict[str, CaseRecord] = {}
        self.drafts: dict[str, list[NarrativeDraft]] = {}

    def save_alerts(self, alerts: list[Alert]) -> None:
        for alert in alerts:
            self.alerts[alert.alert_id] = alert.model_copy(deep=True)

    def get_alert(self, alert_id: str) -> Alert | None:
        alert = self.alerts.get(alert_id)
        return alert.model_copy(deep=True) if alert else None

    def save_evidence(self, bundle: EvidenceBundle) -> None:
        self.evidence[bundle.alert_id] = bundle.model_copy(deep=True)

    def get_evidence(self, alert_id: str) -> EvidenceBundle | None:
        bundle = self.evidence.get(alert_id)
        return bundle.model_copy(deep=True) if bundle else None

    def create_case(self, case: CaseRecord) -> CaseRecord:
        if case.case_id in self.cases:
            raise ValueError(f"case {case.case_id!r} already exists")
        self.cases[case.case_id] = case.model_copy(deep=True)
        return case

    def get_case(self, case_id: str) -> CaseRecord | None:
        case = self.cases.get(case_id)
        return case.model_copy(deep=True) if case else None

    def update_case(self, case: CaseRecord) -> CaseRecord:
        if case.case_id not in self.cases:
            raise KeyError(f"case {case.case_id!r} does not exist")
        self.cases[case.case_id] = case.model_copy(deep=True)
        return case

    def save_draft(self, case_id: str, draft: NarrativeDraft) -> None:
        self.drafts.setdefault(case_id, []).append(draft.model_copy(deep=True))

    def list_drafts(self, case_id: str) -> list[NarrativeDraft]:
        """Not part of the Protocol; handy for tests and the demo."""
        return copy.deepcopy(self.drafts.get(case_id, []))


class InMemoryAuditRepository:
    """Append-only. Rejects events that don't extend the current chain head."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> AuditEvent:
        if event.hash is None:
            raise ValueError("event must be chained (hash set) before append")
        if event.prev_hash != self.last_hash():
            raise ValueError(
                f"prev_hash {event.prev_hash!r} does not match chain head {self.last_hash()!r}"
            )
        if any(e.event_id == event.event_id for e in self._events):
            raise ValueError(f"event_id {event.event_id!r} already stored")
        self._events.append(event.model_copy(deep=True))
        return event

    def last_hash(self) -> str | None:
        return self._events[-1].hash if self._events else None

    def list_events(self, object_id: str | None = None) -> list[AuditEvent]:
        return [
            e.model_copy(deep=True) for e in self._events
            if object_id is None or e.object_id == object_id
        ]


__all__ = [
    "SCENARIOS",
    "FIXTURES",
    "MAX_SUBGRAPH_HOPS",
    "MockDataStore",
    "MockIngestion",
    "MockQuality",
    "MockGraphService",
    "MockGraphRiskScorer",
    "MockBehaviorScorer",
    "MockRankFusion",
    "MockFlowInvestigator",
    "MockPolicyRetriever",
    "InMemoryCaseRepository",
    "InMemoryAuditRepository",
    "bounded_subgraph",
]
