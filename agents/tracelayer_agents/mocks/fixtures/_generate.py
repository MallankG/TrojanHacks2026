"""Regenerate the synthetic fixture CSVs. Deterministic: re-running gives identical files.

    python tracelayer_agents/mocks/fixtures/_generate.py

Every row is synthetic (ACC-xxxx / TX-xxxxx). Columns are the PRD §13 `transactions` table. The
planted transaction IDs are listed in README.md; keep the two in sync.
"""

from __future__ import annotations

import csv
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).parent
COLUMNS = [
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
]
T0 = datetime(2026, 9, 1, tzinfo=UTC)
BENIGN_ACCOUNTS = [f"ACC-{n}" for n in range(1001, 1021)]
BENIGN_CHANNELS = ["card", "ach"]


def _utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _row(tx_id: str, when: datetime | str, amount: str, origin: str, dest: str,
         channel: str = "wire", currency: str = "USD") -> dict[str, str]:
    return {
        "transaction_id": tx_id,
        "timestamp": when if isinstance(when, str) else _ts(when),
        "amount": amount,
        "currency": currency,
        "channel": channel,
        "direction": "outbound",
        "origin_account_id": origin,
        "destination_account_id": dest,
        "transaction_status": "posted",
    }


def benign(seed: int, first_tx: int, n: int = 50) -> list[dict[str, str]]:
    """Everyday activity over seven days: 14 retail customers mostly paying 6 merchants by card or
    ACH, plus a few small person-to-person transfers between customers."""
    rng = random.Random(seed)
    customers, merchants = BENIGN_ACCOUNTS[:14], BENIGN_ACCOUNTS[14:]
    rows = []
    for i in range(n):
        if i % 5 == 4:
            origin, dest = rng.sample(customers, 2)
            channel, cents = "online_transfer", rng.randrange(2_000, 40_000)
        else:
            origin, dest = rng.choice(customers), rng.choice(merchants)
            channel, cents = rng.choice(BENIGN_CHANNELS[:2]), rng.randrange(1_500, 180_000)
        when = T0 + timedelta(minutes=rng.randrange(7 * 24 * 60))
        amount = Decimal(cents) / 100
        rows.append(_row(f"TX-{first_tx + i}", when, f"{amount:.2f}", origin, dest,
                         channel=channel))
    return rows


def fan_out() -> list[dict[str, str]]:
    start = _utc(2026, 9, 4, 13, 5)
    amounts = ["4850.00", "4920.00", "4775.00", "4990.00", "4810.00",
               "4905.00", "4860.00", "4950.00", "4795.00"]
    return [
        _row(f"TX-{51001 + i}", start + timedelta(minutes=17 * i), amt, "ACC-3001",
             f"ACC-{3101 + i}")
        for i, amt in enumerate(amounts)
    ]


def pass_through() -> list[dict[str, str]]:
    return [
        _row("TX-52001", _utc(2026, 9, 5, 10, 2), "12500.00", "ACC-2001", "ACC-2002"),
        _row("TX-52002", _utc(2026, 9, 5, 10, 9), "12350.00", "ACC-2002", "ACC-2003"),
        _row("TX-52003", _utc(2026, 9, 5, 15, 30), "8000.00", "ACC-2004", "ACC-2005"),
        _row("TX-52004", _utc(2026, 9, 5, 15, 41), "7920.00", "ACC-2005", "ACC-2006"),
    ]


def cycle() -> list[dict[str, str]]:
    return [
        _row("TX-53001", _utc(2026, 9, 2, 9, 0), "25000.00", "ACC-4001", "ACC-4002"),
        _row("TX-53002", _utc(2026, 9, 2, 14, 30), "24600.00", "ACC-4002", "ACC-4003"),
        _row("TX-53003", _utc(2026, 9, 3, 10, 15), "24150.00", "ACC-4003", "ACC-4004"),
        _row("TX-53004", _utc(2026, 9, 3, 16, 45), "23800.00", "ACC-4004", "ACC-4001"),
    ]


def bad_rows() -> list[dict[str, str]]:
    d = datetime(2026, 9, 3, tzinfo=UTC)
    dup = _row("TX-54001", d + timedelta(hours=9), "1200.00", "ACC-5001", "ACC-5002")
    return [
        dup,
        {**dup, "amount": "1250.00"},  # duplicate transaction_id, different amount
        _row("TX-54002", d + timedelta(hours=10), "640.00", "ACC-5001", ""),  # missing destination
        _row("TX-54003", d + timedelta(hours=11), "980.00", "ACC-5002", "ACC-9999"),  # unknown acct
        _row("TX-54004", "2026-09-03T17:15:00+05:00", "310.00", "ACC-5001", "ACC-5002"),  # non-UTC
        _row("TX-54005", "2026-02-30T10:00:00Z", "455.00", "ACC-5002", "ACC-5001"),  # unparseable
        _row("TX-54006", d + timedelta(hours=14), "720.00", "ACC-5001", "ACC-5002",
             currency="US$"),  # invalid currency
    ]


def write(name: str, rows: list[dict[str, str]]) -> None:
    rows = sorted(rows, key=lambda r: (r["timestamp"], r["transaction_id"]))
    for i, row in enumerate(rows, start=1):
        row["source_row_ref"] = f"row_{i:04d}"
    with (HERE / name).open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_accounts() -> None:
    planted = (
        ["ACC-3001"] + [f"ACC-{n}" for n in range(3101, 3110)]
        + [f"ACC-{n}" for n in range(2001, 2007)]
        + [f"ACC-{n}" for n in range(4001, 4005)]
        + ["ACC-5001", "ACC-5002"]  # ACC-9999 is deliberately absent
    )
    with (HERE / "accounts.csv").open("w", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["account_id", "customer_ref", "account_type", "open_date", "status"])
        for i, acc in enumerate(BENIGN_ACCOUNTS + planted):
            kind = "business_checking" if acc in {"ACC-3001", "ACC-4001"} else "checking"
            writer.writerow([acc, f"CUST-{acc[4:]}", kind, f"202{i % 5}-0{1 + i % 9}-15", "active"])


if __name__ == "__main__":
    write("fan_out.csv", benign(1, 11001) + fan_out())
    write("pass_through.csv", benign(2, 12001) + pass_through())
    write("cycle.csv", benign(3, 13001) + cycle())
    write("bad_batch.csv", benign(4, 14001) + bad_rows())
    write_accounts()
