"""Generate the reproducible demo bank dataset (synthetic only) with seeded scenarios from PRD section 15.

    cd backend
    python -m scripts.generate_demo_data            # writes sample_data/demo_bank_transactions.csv + demo_scenarios.json

The seed is fixed, so re-running produces byte-identical files. Ground-truth labels (is_suspicious, typology) are
written for evaluation only; the detection engine never reads them. Scenario ids, accounts and expected signals are
listed in sample_data/README.md and, per transaction, in demo_scenarios.json.
"""

import csv
import json
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

SEED = 20261017
START = datetime(2026, 8, 1, tzinfo=UTC)
END = datetime(2026, 10, 10, tzinfo=UTC)
HOME = "TLBANK"
OUT_DIR = Path(__file__).resolve().parent.parent / "sample_data"
CSV_NAME = "demo_bank_transactions.csv"
MANIFEST_NAME = "demo_scenarios.json"
COLUMNS = [
    "transaction_id", "timestamp", "amount", "currency", "channel", "direction", "origin_account", "origin_bank",
    "origin_name", "destination_account", "destination_bank", "destination_name", "transaction_status", "description",
    "is_suspicious", "typology",
]


@dataclass
class Row:
    when: datetime
    amount: Decimal
    channel: str
    origin: str
    destination: str
    description: str
    scenario: str | None = None
    typology: str | None = None
    overrides: dict = field(default_factory=dict)  # raw values for deliberately malformed rows


class Generator:
    def __init__(self, seed: int = SEED):
        self.rng = random.Random(seed)
        self.rows: list[Row] = []
        self.banks: dict[str, str] = {}
        self.names: dict[str, str] = {}

    # --- helpers ------------------------------------------------------------------------------
    def account(self, ref: str, name: str, bank: str = HOME) -> str:
        self.banks[ref] = bank
        self.names[ref] = name
        return ref

    def money(self, low: float, high: float) -> Decimal:
        return Decimal(str(self.rng.uniform(low, high))).quantize(Decimal("0.01"), ROUND_HALF_UP)

    def at(self, day: datetime, hour_low: int = 8, hour_high: int = 20) -> datetime:
        return day.replace(hour=self.rng.randint(hour_low, hour_high - 1), minute=self.rng.randint(0, 59))

    def add(self, when: datetime, amount: Decimal | float | str, channel: str, origin: str, destination: str,
            description: str, scenario: str | None = None, typology: str | None = None,
            raw: dict | None = None) -> Row:
        row = Row(when, Decimal(str(amount)).quantize(Decimal("0.01")), channel, origin, destination, description,
                  scenario, typology, raw or {})
        self.rows.append(row)
        return row

    @staticmethod
    def days(start: datetime = START, end: datetime = END):
        day = start
        while day < end:
            yield day
            day += timedelta(days=1)

    # --- normal activity ------------------------------------------------------------------------
    def background(self, num_customers: int = 30) -> None:
        rng = self.rng
        self.customers = [self.account(f"CUST-{i:03d}", f"Synthetic Customer {i:03d}") for i in range(1, num_customers + 1)]
        grocery = self.account("MER-GROCERY", "Northside Grocery LLC", "EXTBANK-NY")
        fuel = self.account("MER-FUEL", "Route 9 Fuel Inc", "EXTBANK-NJ")
        online = self.account("MER-ONLINE", "Parcelhub Online Inc", "EXTBANK-CA")
        utility = self.account("UTIL-POWER", "Metro Power Co", "EXTBANK-NY")
        landlord = self.account("LANDLORD-01", "Harbor Rentals LLC", "EXTBANK-NJ")
        market = self.account("BIZ-MARKET", "Eastgate Wholesale Market LLC")
        treasury = self.account("BIZ-MARKET-TRSY", "Eastgate Wholesale Treasury", "EXTBANK-NY")
        self.employer = self.account("EMP-ACME", "Acme Tools LLC")
        ext_employer = self.account("EXT-EMPLOYER", "Bayview Hospital Corp", "EXTBANK-NJ")
        clients = [self.account(f"CLIENT-{i:02d}", f"Acme Client {i:02d} Inc", "EXTBANK-CA") for i in range(1, 5)]
        restaurant = self.account("BIZ-REST", "Lantern Restaurant LLC")
        supplier = self.account("SUPPLIER-FOOD", "Coastal Food Supply Inc", "EXTBANK-NY")
        self.desks = [self.account("CASHDESK-BR01", "TLBank Branch 01 Teller"),
                      self.account("CASHDESK-BR02", "TLBank Branch 02 Teller")]

        # Scenario I: benign payroll. Clients pay Acme on Monday/Tuesday mornings; Acme pays 20 employees every
        # other Friday afternoon. Same employees every cycle, funded by balance rather than a fresh inflow.
        employees = self.customers[:20]
        salaries = {c: self.money(2000, 3400) for c in self.customers}
        for day in self.days():
            if day.weekday() in (0, 1):
                for client in clients:
                    if rng.random() < 0.55:
                        self.add(self.at(day, 9, 11), self.money(6000, 14000), "wire", client, self.employer,
                                 "Invoice payment", "S-I")
        payday = datetime(2026, 8, 14, tzinfo=UTC)
        while payday < END:
            for emp in employees:
                self.add(payday.replace(hour=15, minute=rng.randint(0, 40)), salaries[emp] + self.money(-15, 15),
                         "ach", self.employer, emp, "Payroll", "S-I")
            for cust in self.customers[20:]:
                self.add(payday.replace(hour=7, minute=rng.randint(0, 40)), salaries[cust], "ach", ext_employer, cust,
                         "Payroll", "S-I")
            payday += timedelta(days=14)

        # Everyday customer activity: card purchases, wholesale market purchases, rent, utilities, small P2P.
        for cust in self.customers:
            rent = self.money(1200, 2200)
            for month_start in (datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC),
                                datetime(2026, 10, 1, tzinfo=UTC)):
                day = month_start + timedelta(days=rng.randint(0, 2))
                self.add(self.at(day), rent, "ach", cust, landlord, "Rent")
                self.add(self.at(day + timedelta(days=rng.randint(3, 8))), self.money(60, 180), "ach", cust, utility,
                         "Electricity bill")
            for day in self.days():
                if rng.random() < 0.45:
                    merchant = rng.choice((grocery, grocery, fuel, online))
                    self.add(self.at(day), self.money(12, 160), "card", cust, merchant, "Card purchase")
                if rng.random() < 0.2:
                    self.add(self.at(day), self.money(100, 600), "card", cust, market, "Wholesale purchase", "S-I2")
                if rng.random() < 0.05:
                    friend = rng.choice([c for c in self.customers if c != cust])
                    self.add(self.at(day), self.money(20, 250), "p2p", cust, friend, "Split dinner")
                if rng.random() < 0.015:
                    self.add(self.at(day), self.money(60, 300), "cash", cust, rng.choice(self.desks), "ATM withdrawal")

        # Scenario I2: benign high-volume business. Many customers buy at the market every week and the market
        # sweeps the takings to its own treasury account weekly. Looks like a funnel, but the volume is its normal
        # baseline (every week looks the same).
        sweep_day = datetime(2026, 8, 8, tzinfo=UTC)
        while sweep_day < END:
            week_take = sum((r.amount for r in self.rows if r.destination == market
                             and sweep_day - timedelta(days=7) <= r.when < sweep_day), Decimal("0"))
            if week_take:
                self.add(sweep_day.replace(hour=18), (week_take * Decimal("0.95")).quantize(Decimal("0.01")), "wire",
                         market, treasury, "Weekly treasury sweep", "S-I2")
            sweep_day += timedelta(days=7)

        # Scenario C-benign: restaurant with daily cash deposits spread from 5k to 14k (some fall inside the review
        # band, many above it) and weekly supplier payments.
        for day in self.days():
            if day.weekday() < 6:
                self.add(self.at(day, 17, 21), self.money(5000, 14000), "cash", rng.choice(self.desks), restaurant,
                         "Cash deposit", "S-C-benign")
            if day.weekday() == 3:
                self.add(self.at(day, 10, 12), self.money(26000, 40000), "ach", restaurant, supplier,
                         "Supplier invoice", "S-C-benign")

    def light_activity(self, ref: str, start: datetime = START, end: datetime = END, every_days: int = 6) -> None:
        """Small, regular activity so scenario accounts are not empty shells (and are not dormant)."""
        day = start
        while day < end:
            self.add(self.at(day), self.money(20, 140), "card", ref, "MER-GROCERY", "Card purchase")
            if self.rng.random() < 0.5:
                self.add(self.at(day), self.money(50, 300), "p2p", self.rng.choice(self.customers), ref, "Transfer")
            day += timedelta(days=self.rng.randint(every_days - 2, every_days + 2))

    # --- seeded scenarios -----------------------------------------------------------------------
    def scenarios(self) -> None:
        rng = self.rng

        # A) 5-hop layering chain with small hop-to-hop decay, a few hours between hops.
        chain = [self.account("L-SRC", "Orchid Trading Ltd", "OFFSHORE-KY")]
        chain += [self.account(f"L-{i:02d}", f"Synthetic Layer Holder {i:02d}") for i in range(1, 5)]
        chain += [self.account("L-DST", "Bluewater Consulting Ltd", "OFFSHORE-PA")]
        for ref in chain[1:-1]:
            self.light_activity(ref)
        when, amount = datetime(2026, 9, 22, 9, 5, tzinfo=UTC), Decimal("48500.00")
        for hop, (src, dst) in enumerate(zip(chain, chain[1:], strict=False)):
            self.add(when, amount, "wire" if hop % 2 == 0 else "ach", src, dst,
                     ("Invoice settlement", "Vendor payment", "Consulting fee", "Transfer", "Advisory fee")[hop],
                     "S-A", "layering")
            when += timedelta(hours=rng.randint(2, 6), minutes=rng.randint(0, 59))
            amount = (amount * Decimal(str(rng.uniform(0.975, 0.99)))).quantize(Decimal("0.01"))

        # B) Repeated round-trip cycle: R-A -> R-B -> R-C -> R-A, three loops a week apart.
        loop = [self.account(f"R-{x}", f"Synthetic Loop Holder {x}") for x in "ABC"]
        for ref in loop:
            self.light_activity(ref)
        for week in range(3):
            when = datetime(2026, 9, 1, 10, 0, tzinfo=UTC) + timedelta(days=7 * week)
            amount = self.money(24000, 26000)
            for src, dst in zip(loop, loop[1:] + loop[:1], strict=True):
                self.add(when, amount, "wire", src, dst, "Loan repayment", "S-B", "cycle")
                when += timedelta(hours=rng.randint(5, 20))
                amount = (amount * Decimal(str(rng.uniform(0.97, 0.995)))).quantize(Decimal("0.01"))

        # C) Structuring-like cash deposits just under the 10k review band upper edge, at two branches, then a wire out.
        st = self.account("ST-01", "Synthetic Customer ST01")
        st_ext = self.account("ST-EXT", "Golden Gate Imports Ltd", "EXTBANK-CA")
        self.light_activity(st)
        day = datetime(2026, 9, 3, tzinfo=UTC)
        for i, amount in enumerate(("9400.00", "9750.00", "9100.00", "9850.00", "9300.00")):
            self.add(self.at(day + timedelta(days=(0, 1, 3, 4, 5)[i]), 10, 16), amount, "cash", self.desks[i % 2], st,
                     "Cash deposit", "S-C", "structuring")
        self.add(datetime(2026, 9, 9, 11, 20, tzinfo=UTC), "46000.00", "wire", st, st_ext, "Purchase of goods", "S-C",
                 "structuring")

        # D) Dormant reactivation then dispersal: active in early August, silent ~54 days, then a large inflow is
        # dispersed to six never-before-paid beneficiaries within a day.
        dor = self.account("DOR-01", "Synthetic Customer DOR01")
        d_src = self.account("D-SRC", "Northwind Holdings SA", "OFFSHORE-KY")
        bens = [self.account(f"D-BEN-{i}", f"Synthetic Beneficiary D{i}", ("EXTBANK-NY", "EXTBANK-NJ", "EXTBANK-CA")[i % 3])
                for i in range(1, 7)]
        for offset, (amount, channel, origin, dest, memo) in enumerate((
            ("400.00", "p2p", "CUST-011", dor, "Transfer"), ("85.20", "card", dor, "MER-GROCERY", "Card purchase"),
            ("42.75", "card", dor, "MER-FUEL", "Card purchase"), ("150.00", "p2p", dor, "CUST-012", "Transfer"),
        )):
            self.add(datetime(2026, 8, 2 + 2 * offset, 13, 0, tzinfo=UTC), amount, channel, origin, dest, memo)
        self.add(datetime(2026, 10, 1, 9, 10, tzinfo=UTC), "38000.00", "wire", d_src, dor, "Settlement", "S-D",
                 "dormant_reactivation")
        when = datetime(2026, 10, 1, 13, 0, tzinfo=UTC)
        for ben in bens:
            self.add(when, self.money(5600, 5950), "p2p" if ben.endswith(("1", "3")) else "ach", dor, ben, "Payment",
                     "S-D", "fan_out")
            when += timedelta(hours=rng.randint(1, 3))

        # E) Funnel: a new account collects from eight customers over five days and wires ~92% out to one beneficiary.
        fun = self.account("FUN-01", "Synthetic Customer FUN01")
        fun_out = self.account("FUN-OUT", "Silverline Exports Ltd", "OFFSHORE-PA")
        senders = self.customers[21:29]
        total = Decimal("0")
        for i, sender in enumerate(senders):
            amount = self.money(1800, 4200)
            total += amount
            self.add(self.at(datetime(2026, 9, 12, tzinfo=UTC) + timedelta(days=i // 2)), amount,
                     "p2p" if i % 2 else "ach", sender, fun, "Investment contribution", "S-E", "funnel")
        self.add(datetime(2026, 9, 17, 10, 30, tzinfo=UTC), (total * Decimal("0.92")).quantize(Decimal("0.01")), "wire",
                 fun, fun_out, "Investment placement", "S-E", "funnel")

        # H) Scam proceeds to a new beneficiary then dispersal: an established customer sends an unusually large wire to
        # an account it never paid before, which forwards almost all of it within a day.
        victim = "CUST-007"
        sc = self.account("SC-BEN", "Synthetic Customer SCBEN")
        outs = [self.account(f"SC-OUT-{i}", f"Synthetic Recipient SC{i}", "EXTBANK-NJ") for i in range(1, 5)]
        self.add(datetime(2026, 9, 24, 15, 40, tzinfo=UTC), "15000.00", "wire", victim, sc, "Urgent invoice", "S-H",
                 "scam_proceeds")
        when = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)
        for out in outs:
            self.add(when, self.money(3500, 3650), "p2p", sc, out, "Payment", "S-H", "scam_proceeds")
            when += timedelta(hours=rng.randint(1, 4))

    def malformed(self) -> None:
        """J) Malformed rows: rejected rows and rows accepted with warnings that touch scenario accounts."""
        self.add(datetime(2026, 9, 5, 12, tzinfo=UTC), "64.10", "card", "CUST-004", "MER-GROCERY", "Card purchase", "S-J",
                 raw={"timestamp": "2026-13-45 99:99"})
        self.add(datetime(2026, 9, 6, 12, tzinfo=UTC), "9650.00", "cash", "CASHDESK-BR01", "ST-01", "Cash deposit", "S-J",
                 raw={"currency": "USX"})
        self.add(datetime(2026, 9, 7, 12, tzinfo=UTC), "250.00", "ach", "CUST-009", "UTIL-POWER", "Refund", "S-J",
                 raw={"amount": "-250.00"})
        self.add(datetime(2026, 9, 8, 12, tzinfo=UTC), "120.00", "p2p", "CUST-010", "CUST-011", "Transfer", "S-J",
                 raw={"destination_account": ""})
        self.add(datetime(2026, 9, 9, 12, tzinfo=UTC), "75.00", "card", "CUST-012", "MER-FUEL", "Card purchase", "S-J",
                 raw={"origin_account": "unknown"})
        # Accepted with warnings: unknown channel on a funnel inflow, missing timezone on a dispersal payment.
        self.add(datetime(2026, 9, 15, 16, 5, tzinfo=UTC), "2950.00", "p2p", "CUST-029", "FUN-01",
                 "Investment contribution", "S-J", "funnel", raw={"channel": "carrier pigeon"})
        self.add(datetime(2026, 10, 2, 9, 30, tzinfo=UTC), "5725.00", "ach", "DOR-01", "D-BEN-6", "Payment", "S-J",
                 "fan_out", raw={"timestamp": "2026-10-02 09:30:00"})

    # --- output ---------------------------------------------------------------------------------
    def write(self, out_dir: Path = OUT_DIR, csv_name: str = CSV_NAME, manifest_name: str = MANIFEST_NAME) -> tuple[Path, Path]:
        self.rows.sort(key=lambda r: (r.when, r.origin, r.destination, r.amount))
        records, manifest_txns = [], {}
        for i, r in enumerate(self.rows, start=1):
            ref = f"DEMO-{i:06d}"
            suspicious = r.typology is not None
            record = {
                "transaction_id": ref,
                "timestamp": r.when.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "amount": format(r.amount, "f"),
                "currency": "USD",
                "channel": r.channel,
                "direction": "internal" if self.banks.get(r.origin) == HOME and self.banks.get(r.destination) == HOME
                else ("outbound" if self.banks.get(r.origin) == HOME else "inbound"),
                "origin_account": r.origin,
                "origin_bank": self.banks.get(r.origin, HOME),
                "origin_name": self.names.get(r.origin, ""),
                "destination_account": r.destination,
                "destination_bank": self.banks.get(r.destination, HOME),
                "destination_name": self.names.get(r.destination, ""),
                "transaction_status": "posted",
                "description": r.description,
                "is_suspicious": "true" if suspicious else "false",
                "typology": r.typology or "",
            }
            record.update(r.overrides)
            records.append(record)
            if r.scenario and r.scenario not in ("S-I", "S-I2", "S-C-benign"):
                manifest_txns.setdefault(r.scenario, []).append(ref)
        # One duplicate transaction_id (rejected as a duplicate within the file).
        dup = dict(records[100])
        dup["description"] = "Duplicate transaction id"
        records.insert(101, dup)
        manifest_txns.setdefault("S-J", []).append(dup["transaction_id"] + " (second occurrence)")

        out_dir.mkdir(parents=True, exist_ok=True)
        csv_path = out_dir / (csv_name or CSV_NAME)
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(records)
        manifest = {
            "dataset": csv_name or CSV_NAME,
            "seed": SEED,
            "rows": len(records),
            "accounts": len({r["origin_account"] for r in records} | {r["destination_account"] for r in records}),
            "window": [START.isoformat(), END.isoformat()],
            "note": "Synthetic data. Labels are for evaluation only; detectors never read them.",
            "scenarios": {sid: {**SCENARIOS[sid], "transaction_ids": manifest_txns.get(sid, [])} for sid in SCENARIOS},
        }
        manifest_path = out_dir / (manifest_name or MANIFEST_NAME)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return csv_path, manifest_path


SCENARIOS = {
    "S-A": {"name": "5-hop layering chain", "accounts": ["L-SRC", "L-01", "L-02", "L-03", "L-04", "L-DST"],
            "expected_typology": "layering", "expected_signals": ["layering_chain", "rapid_pass_through"]},
    "S-B": {"name": "Repeated round-trip cycle", "accounts": ["R-A", "R-B", "R-C"], "expected_typology": "cycle",
            "expected_signals": ["bounded_cycle", "rapid_pass_through"]},
    "S-C": {"name": "Structuring-like cash deposits", "accounts": ["ST-01", "CASHDESK-BR01", "CASHDESK-BR02", "ST-EXT"],
            "expected_typology": "structuring", "expected_signals": ["structuring_band"],
            "benign_confounder": "S-C-benign"},
    "S-C-benign": {"name": "Restaurant with daily cash deposits (benign lookalike)", "accounts": ["BIZ-REST"],
                   "expected_typology": None, "expected_non_signals": ["structuring_band"]},
    "S-D": {"name": "Dormant reactivation then dispersal", "accounts": ["DOR-01", "D-SRC"] + [f"D-BEN-{i}" for i in range(1, 7)],
            "expected_typology": "dormant_reactivation",
            "expected_signals": ["dormant_reactivation", "fan_out_dispersal", "rapid_pass_through"]},
    "S-E": {"name": "Funnel account", "accounts": ["FUN-01", "FUN-OUT"] + [f"CUST-{i:03d}" for i in range(22, 30)],
            "expected_typology": "funnel", "expected_signals": ["funnel_fan_in"], "benign_confounder": "S-I2"},
    "S-H": {"name": "Scam proceeds to a new beneficiary, then dispersal",
            "accounts": ["CUST-007", "SC-BEN"] + [f"SC-OUT-{i}" for i in range(1, 5)],
            "expected_typology": "scam_proceeds",
            "expected_signals": ["new_beneficiary_large_payment", "rapid_pass_through", "robust_amount_deviation"]},
    "S-I": {"name": "Benign payroll (Acme pays the same 20 employees every other Friday)",
            "accounts": ["EMP-ACME", "EXT-EMPLOYER", "CLIENT-01", "CLIENT-02", "CLIENT-03", "CLIENT-04"],
            "expected_typology": None, "expected_non_signals": ["fan_out_dispersal", "rapid_pass_through"]},
    "S-I2": {"name": "Benign high-volume wholesale market with weekly treasury sweep",
             "accounts": ["BIZ-MARKET", "BIZ-MARKET-TRSY"], "expected_typology": None,
             "expected_non_signals": ["funnel_fan_in"]},
    "S-J": {"name": "Malformed rows (5 rejected + 1 duplicate id, 2 accepted with warnings on scenario accounts)",
            "accounts": ["ST-01", "FUN-01", "DOR-01"], "expected_typology": None,
            "expected_effect": "Data-quality caveats on the structuring, funnel and dormant alerts"},
}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--customers", type=int, default=30, help="Number of synthetic customers (default: 30)")
    parser.add_argument("--csv-name", default=CSV_NAME, help="Output CSV filename")
    parser.add_argument("--manifest-name", default=MANIFEST_NAME, help="Output manifest filename")
    args = parser.parse_args()

    gen = Generator()
    gen.background(num_customers=max(30, args.customers))
    gen.scenarios()
    gen.malformed()
    csv_path, manifest_path = gen.write(csv_name=args.csv_name, manifest_name=args.manifest_name)
    print(f"wrote {csv_path} and {manifest_path} ({len(gen.rows)} rows)")


if __name__ == "__main__":
    main()
