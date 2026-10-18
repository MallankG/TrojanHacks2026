"""Chat sessions and the case scope that bounds them.

A chat is bound to exactly one case. `build_scope` resolves it once, at `start_chat`, from the
case's alert and evidence bundle plus the bounded neighbourhood `GraphService.subgraph` returns
around that evidence, clipped to the case time window. Every tool checks its arguments against
this `CaseScope` before calling a service and filters what comes back to it, so a chat can never
read another case's transactions or accounts. Tools receive the scope, never a case_id.

Sessions live in memory (`ChatStore`); the audit trail is the durable record (TEAM_QUESTIONS).
"""

from __future__ import annotations

import threading
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from tracelayer_agents.interfaces import Services
from tracelayer_agents.nodes.derive import compute_derived_facts
from tracelayer_agents.schemas import (
    ActorRole,
    Alert,
    ChatContext,
    ChatSession,
    DerivedFact,
    EvidenceBundle,
    SubgraphEdge,
)

# TODO(review): how far around the evidence a chat may look. Evidence span ± 7 days.
CASE_WINDOW_PAD = timedelta(days=7)
SCOPE_HOPS = 3  # GraphService.subgraph accepts 1–3
MAX_PINS = 50
HUB_ACCOUNTS = 3


@dataclass(frozen=True)
class CaseScope:
    """Everything one chat may see. `edges` are the scope's transactions by id (evidence plus the
    bounded neighbourhood inside the window)."""

    case_id: str
    alert: Alert
    bundle: EvidenceBundle
    derived_facts: tuple[DerivedFact, ...]
    window_start: datetime
    window_end: datetime
    edges: dict[str, SubgraphEdge]
    account_ids: frozenset[str]
    hub_accounts: tuple[str, ...]
    notes: tuple[str, ...] = ()

    @property
    def tx_ids(self) -> frozenset[str]:
        return frozenset(self.edges)

    @property
    def evidence_tx_ids(self) -> list[str]:
        return [t.transaction_id for t in self.bundle.transactions]

    @property
    def evidence_account_ids(self) -> frozenset[str]:
        return frozenset(
            a for t in self.bundle.transactions
            for a in (t.origin_account_id, t.destination_account_id)
        )

    @property
    def graph_snapshot_id(self) -> str:
        return self.alert.graph_snapshot_id

    @property
    def pinnable_ids(self) -> frozenset[str]:
        """Ids a reviewer may pin: scope transactions and accounts, and derived-fact ids."""
        return self.tx_ids | self.account_ids | {f.fact_id for f in self.derived_facts}

    def in_window(self, ts: datetime) -> bool:
        return self.window_start <= ts <= self.window_end


def _edge(tx) -> SubgraphEdge:
    return SubgraphEdge(
        transaction_id=tx.transaction_id, origin_account_id=tx.origin_account_id,
        destination_account_id=tx.destination_account_id, amount=tx.amount,
        currency=tx.currency, timestamp=tx.timestamp,
    )


def build_scope(case_id: str, services: Services) -> CaseScope:
    """Resolve a case into its scope. Raises `KeyError` for an unknown case, a case whose alert or
    evidence bundle is missing. If the graph service fails, the scope is the evidence only and a
    note says so."""
    case = services.cases.get_case(case_id)
    if case is None:
        raise KeyError(f"unknown case_id {case_id!r}")
    alert = services.cases.get_alert(case.alert_id)
    if alert is None:
        raise KeyError(f"case {case_id!r}: alert {case.alert_id!r} not found")
    bundle = services.cases.get_evidence(case.alert_id)
    if bundle is None:
        raise KeyError(f"case {case_id!r}: no evidence bundle for alert {case.alert_id!r}")

    times = [t.timestamp for t in bundle.transactions]
    start, end = min(times) - CASE_WINDOW_PAD, max(times) + CASE_WINDOW_PAD
    edges = {t.transaction_id: _edge(t) for t in bundle.transactions}
    notes: list[str] = []
    try:
        sub = services.graph.subgraph(list(edges), SCOPE_HOPS)
        for e in sub.edges:
            if start <= e.timestamp <= end:
                edges.setdefault(e.transaction_id, e)
    except Exception as exc:  # the chat still works on the evidence alone
        notes.append(f"graph neighbourhood unavailable ({type(exc).__name__}); "
                     "scope is the evidence bundle only")

    accounts = frozenset(a for e in edges.values()
                         for a in (e.origin_account_id, e.destination_account_id))
    touches = Counter(a for t in bundle.transactions
                      for a in (t.origin_account_id, t.destination_account_id))
    hubs = tuple(a for a, _ in sorted(touches.items(), key=lambda kv: (-kv[1], kv[0])))
    return CaseScope(
        case_id=case_id, alert=alert, bundle=bundle,
        derived_facts=tuple(compute_derived_facts(bundle)),
        window_start=start, window_end=end, edges=edges, account_ids=accounts,
        hub_accounts=hubs[:HUB_ACCOUNTS], notes=tuple(notes),
    )


def build_context(scope: CaseScope, pinned: list[str]) -> ChatContext:
    return ChatContext(
        case_id=scope.case_id,
        alert_id=scope.alert.alert_id,
        alert=scope.alert,
        evidence_bundle=scope.bundle,
        derived_facts=list(scope.derived_facts),
        quality_issues=list(scope.bundle.quality_caveats),
        graph_snapshot_id=scope.graph_snapshot_id,
        window_start=scope.window_start,
        window_end=scope.window_end,
        entity_scope=sorted(scope.account_ids),
        scope_transaction_count=len(scope.edges),
        pinned_evidence_ids=list(pinned),
        scope_notes=list(scope.notes),
    )


# --------------------------------------------------------------------------- store


@dataclass
class ChatEntry:
    session: ChatSession
    scope: CaseScope
    lock: threading.Lock = field(default_factory=threading.Lock)


class ChatStore:
    """In-memory chat sessions by chat_id."""

    def __init__(self) -> None:
        self._entries: dict[str, ChatEntry] = {}

    def create(self, scope: CaseScope, actor_id: str, actor_role: ActorRole) -> ChatEntry:
        chat_id = f"chat_{uuid.uuid4().hex[:12]}"
        session = ChatSession(
            chat_id=chat_id, case_id=scope.case_id, alert_id=scope.alert.alert_id,
            actor_id=actor_id, actor_role=actor_role, created_at=datetime.now(UTC),
            context=build_context(scope, []),
        )
        entry = ChatEntry(session=session, scope=scope)
        self._entries[chat_id] = entry
        return entry

    def get(self, chat_id: str) -> ChatEntry:
        try:
            return self._entries[chat_id]
        except KeyError:
            raise KeyError(f"unknown chat_id {chat_id!r}") from None
