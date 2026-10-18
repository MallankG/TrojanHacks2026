"""GraphAdapter contract (PRD v3.3 §6): canonical JSON + stable IDs, no provider-specific query syntax.

Every call is tenant-scoped and capped (max_hops, max_nodes, max_edges, time range, timeout). A request
that exceeds a hard cap is refused with LimitExceeded; work that runs into a cap mid-way returns a
summary with truncated=True and truncation_reason. Analytical responses carry the PRD 22.3 envelope:
status, as_of, graph_version / source_snapshot_id, caveats and limits.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

NODE_TYPES = ("Account", "Customer", "BusinessEntity", "BeneficialOwner", "CounterpartyBank", "Branch", "Merchant",
              "DeviceToken", "GeoToken", "PaymentInstrument", "Transaction", "Case")
RELATIONSHIP_TYPES = ("OWNS", "CONTROLS", "SHARES_TOKEN", "COMMON_DEVICE", "COMMON_COUNTERPARTY", "CASE_LINK")
TXN_REQUIRED = ("transaction_id", "origin_account_id", "destination_account_id", "amount", "currency", "occurred_at")
TXN_OPTIONAL = ("amount_usd", "rail", "status", "source_ref", "ingestion_run_id", "pattern_flags", "lineage_ref", "settled_at")
PATH_CAVEAT = ("Paths and traces are investigative context from timing and amounts; they do not establish that the "
               "same funds moved through commingled balances.")


class GraphError(Exception):
    """Base error; `code` is safe to show to clients."""
    code = "graph_error"


class LimitExceeded(GraphError):
    code = "limit_exceeded"


class NotFound(GraphError):
    code = "not_found"


@dataclass(frozen=True)
class GraphLimits:
    max_hops: int = 6
    max_nodes: int = 2000
    max_edges: int = 10_000
    max_range_days: int = 120
    timeout_s: float = 5.0
    max_results: int = 100

    def hops(self, hops: int) -> int:
        if not 1 <= hops <= self.max_hops:
            raise LimitExceeded(f"hops must be 1..{self.max_hops}")
        return hops

    def window(self, start, end):
        """Both bounds optional; when both given the range must fit max_range_days."""
        if start and end:
            if end < start:
                raise LimitExceeded("end is before start")
            if end - start > timedelta(days=self.max_range_days):
                raise LimitExceeded(f"time range exceeds {self.max_range_days} days")
        return start, end

    def as_dict(self):
        return asdict(self)


def envelope(status="ok", *, graph_version, caveats=(), limits: GraphLimits, truncated=False, truncation_reason=None,
             snapshot_id=None, **payload):
    return {"status": status, "as_of": datetime.now(timezone.utc).isoformat(), "graph_version": graph_version,
            "source_snapshot_id": snapshot_id, "truncated": truncated, "truncation_reason": truncation_reason,
            "caveats": list(caveats), "limits": limits.as_dict(), **payload}


class GraphAdapter(Protocol):
    def upsert_entities(self, tenant_id: str, entities: list[dict], relationships: list[dict] | None = None) -> dict: ...
    def upsert_transactions(self, tenant_id: str, transactions: list[dict]) -> dict: ...
    def get_neighborhood(self, tenant_id: str, account_id: str, *, hops: int = 1, start=None, end=None) -> dict: ...
    def trace_funds(self, tenant_id: str, account_id: str, *, direction: str = "downstream", hops: int = 3,
                    start=None, end=None, max_gap_hours: float | None = None) -> dict: ...
    def find_time_respecting_paths(self, tenant_id: str, source: str, target: str | None = None, *, hops: int = 4,
                                   start=None, end=None, max_gap_hours: float = 72, min_amount_ratio: float = 0.0,
                                   limit: int = 20) -> dict: ...
    def find_cycles_bounded(self, tenant_id: str, *, account_id: str | None = None, min_hops: int = 3, max_hops: int = 6,
                            start=None, end=None, max_gap_hours: float | None = None) -> dict: ...
    def get_graph_metrics(self, tenant_id: str, *, account_ids: list[str] | None = None, start=None, end=None) -> dict: ...
    def get_graph_snapshot(self, tenant_id: str, *, account_id: str | None = None, hops: int = 2, start=None,
                           end=None, snapshot_id: str | None = None) -> dict: ...
    def health_check(self) -> dict: ...
