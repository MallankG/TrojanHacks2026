"""In-memory, time-indexed view of the transactions considered by one detection run."""

from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal


def parse_ts(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class Txn:
    id: str
    ref: str | None
    at: datetime
    usd: Decimal  # amount in USD (exact); detectors compare money in Decimal
    amount: Decimal
    currency: str
    channel: str
    origin: str
    destination: str

    @classmethod
    def from_row(cls, row: dict) -> "Txn":
        amount = Decimal(str(row["amount"]))
        usd = row.get("amount_usd")
        return cls(
            id=str(row["id"]), ref=row.get("transaction_ref"), at=parse_ts(row["occurred_at"]),
            usd=Decimal(str(usd)) if usd is not None else amount, amount=amount, currency=row.get("currency") or "USD",
            channel=row.get("channel") or "other", origin=str(row["origin_account_id"]),
            destination=str(row["destination_account_id"]),
        )

    def edge(self) -> dict:
        """The dict shape app.services.graph expects."""
        return {"id": self.id, "transaction_ref": self.ref, "occurred_at": self.at.isoformat(), "amount": str(self.amount),
                "currency": self.currency, "amount_usd": str(self.usd), "origin_account_id": self.origin,
                "destination_account_id": self.destination}


@dataclass
class Ledger:
    txns: list[Txn]
    outflows: dict[str, list[Txn]] = field(default_factory=dict)
    inflows: dict[str, list[Txn]] = field(default_factory=dict)
    events: dict[str, list[Txn]] = field(default_factory=dict)
    by_id: dict[str, Txn] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.txns = sorted(self.txns, key=lambda t: (t.at, t.id))
        out, inn, ev = defaultdict(list), defaultdict(list), defaultdict(list)
        for t in self.txns:
            out[t.origin].append(t)
            inn[t.destination].append(t)
            ev[t.origin].append(t)
            if t.destination != t.origin:
                ev[t.destination].append(t)
        self.outflows, self.inflows, self.events = dict(out), dict(inn), dict(ev)
        self.by_id = {t.id: t for t in self.txns}
        self._out_times = {a: [t.at for t in v] for a, v in out.items()}
        self._in_times = {a: [t.at for t in v] for a, v in inn.items()}

    @property
    def start(self) -> datetime | None:
        return self.txns[0].at if self.txns else None

    @property
    def end(self) -> datetime | None:
        return self.txns[-1].at if self.txns else None

    def accounts(self) -> set[str]:
        return set(self.events)

    def out_between(self, account: str, start: datetime, end: datetime, *, include_start: bool = False) -> list[Txn]:
        """Outflows with start < at <= end (start inclusive when include_start)."""
        times = self._out_times.get(account, [])
        lo = (bisect_left if include_start else bisect_right)(times, start)
        return self.outflows.get(account, [])[lo:bisect_right(times, end)]

    def in_between(self, account: str, start: datetime, end: datetime, *, include_start: bool = True) -> list[Txn]:
        times = self._in_times.get(account, [])
        lo = (bisect_left if include_start else bisect_right)(times, start)
        return self.inflows.get(account, [])[lo:bisect_right(times, end)]

    def first_seen(self, account: str) -> datetime | None:
        ev = self.events.get(account)
        return ev[0].at if ev else None


def total(txns) -> Decimal:
    return sum((t.usd for t in txns), Decimal("0"))


def hours(delta: timedelta) -> float:
    return round(delta.total_seconds() / 3600, 2)
