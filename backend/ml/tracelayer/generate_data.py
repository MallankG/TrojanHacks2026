"""Synthetic AMLSim-style transaction feed for TraceLayer.

Normal behaviour (card spend, P2P, payroll, rent, merchant settlements, large
B2B invoices) is mixed with seven labelled suspicious typologies from PRD §3.
Legit hubs (merchants, employers, landlords) deliberately look like fan-in /
fan-out so the rule baseline has realistic false positives.

Synthetic data only. Labels mark injected *patterns*, not criminals.

    python -m ml.tracelayer.generate_data --out ml/data
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

START = pd.Timestamp("2026-01-01", tz="UTC")
DAYS = 90
TYPOLOGIES = ["fan_in", "fan_out", "cycle", "layering", "structuring", "dormant_reactivation", "pass_through"]


def generate(seed=7, n_ind=7000, n_merch=300, n_emp=120, n_land=80, n_supp=60, instances_per_typology=60):
    rng = np.random.default_rng(seed)
    types = ["individual"] * n_ind + ["merchant"] * n_merch + ["employer"] * n_emp + ["landlord"] * n_land + ["supplier"] * n_supp
    n = len(types)
    acc = pd.DataFrame({"account_id": [f"A{i:06d}" for i in range(n)], "account_type": types})
    age_days = rng.gamma(2.0, 700, n).astype(int) + 30
    acc["open_date"] = START - pd.to_timedelta(age_days, unit="D")
    ind = np.arange(n_ind)
    merch = np.arange(n_ind, n_ind + n_merch)
    emp = merch[-1] + 1 + np.arange(n_emp)
    land = emp[-1] + 1 + np.arange(n_land)
    supp = land[-1] + 1 + np.arange(n_supp)

    # Dormant individuals: a little card spend in days 0-10, then silence (reactivation typology draws from them).
    dormant = rng.choice(ind, 150, replace=False)
    active = np.setdiff1d(ind, dormant)
    # Mule-ish accounts: some recently opened, so "new account" is a weak, not perfect, signal.
    mule_pool = rng.choice(active, 900, replace=False)
    new_mules = rng.choice(mule_pool, 300, replace=False)
    acc.loc[new_mules, "open_date"] = START + pd.to_timedelta(rng.integers(-60, 20, len(new_mules)), unit="D")
    # Legit new accounts too.
    legit_new = rng.choice(np.setdiff1d(active, mule_pool), 300, replace=False)
    acc.loc[legit_new, "open_date"] = START + pd.to_timedelta(rng.integers(-60, 20, len(legit_new)), unit="D")

    rows = []  # (t_seconds, amount, channel, orig, dest, label, typology, pattern_id)

    def add(t, amt, ch, o, d, label=0, typ="normal", pid=-1):
        t = np.atleast_1d(t)
        k = len(t)
        rows.append(pd.DataFrame({
            "t": t, "amount": np.round(np.broadcast_to(amt, k).astype(float), 2), "channel": np.broadcast_to(ch, k),
            "o": np.broadcast_to(o, k), "d": np.broadcast_to(d, k), "label": label, "typology": typ, "pattern_id": pid,
        }))

    def day_times(k, lo=0, hi=DAYS):
        day = rng.integers(lo, hi, k)
        hour = np.clip(rng.normal(14, 4, k), 0, 23.99)
        return (day * 86400 + hour * 3600).astype(int)

    # --- normal behaviour -------------------------------------------------
    k = rng.integers(1, 4, len(dormant))
    o = np.repeat(dormant, k)
    add(day_times(len(o), 0, 10), rng.lognormal(np.log(40), 0.9, len(o)), "card", o, rng.choice(merch, len(o)))
    rate = rng.lognormal(np.log(0.25), 0.6, len(active))
    k = rng.poisson(rate * DAYS)
    o = np.repeat(active, k)
    fav = rng.choice(merch, (len(active), 5))
    pick = rng.integers(0, 5, len(o))
    d = np.where(rng.random(len(o)) < 0.8, fav[np.repeat(np.arange(len(active)), k), pick], rng.choice(merch, len(o)))
    add(day_times(len(o)), rng.lognormal(np.log(40), 0.9, len(o)), "card", o, d)

    community = rng.integers(0, len(active) // 20, len(active))
    by_comm = pd.Series(np.arange(len(active))).groupby(community).apply(np.array)
    k = rng.poisson(0.05 * DAYS, len(active))
    o_idx = np.repeat(np.arange(len(active)), k)
    d_idx = np.array([rng.choice(by_comm[community[i]]) for i in o_idx])
    keep = o_idx != d_idx
    add(day_times(keep.sum()), rng.lognormal(np.log(60), 1.0, keep.sum()), "p2p", active[o_idx[keep]], active[d_idx[keep]])

    employer = rng.choice(emp, len(active))
    for payday in range(4, DAYS, 14):
        t = payday * 86400 + 9 * 3600 + rng.integers(0, 3600, len(active))
        add(t, rng.lognormal(np.log(2500), 0.3, len(active)), "ach", employer, active)
    landlord = rng.choice(land, len(active))
    renters = rng.random(len(active)) < 0.6
    for month_day in (0, 31, 59):
        t = (month_day + rng.integers(0, 3, renters.sum())) * 86400 + rng.integers(0, 86400, renters.sum())
        add(t, rng.lognormal(np.log(1500), 0.3, renters.sum()), "ach", active[renters], landlord[renters])
    # merchant weekly settlement to suppliers (legit large wires, legit funnel into suppliers)
    for week in range(DAYS // 7):
        t = (week * 7 + 5) * 86400 + rng.integers(0, 86400, n_merch)
        add(t, rng.lognormal(np.log(6000), 0.7, n_merch), "wire", merch, rng.choice(supp, n_merch))
    # B2B invoices, some landing just under 10k (legit "structuring-looking" activity)
    k = 2500
    amt = np.where(rng.random(k) < 0.3, rng.uniform(8500, 9990, k), rng.lognormal(np.log(4000), 0.8, k))
    add(day_times(k), amt, rng.choice(["wire", "ach"], k), rng.choice(np.r_[supp, emp], k), rng.choice(np.r_[supp, land], k))
    # legit large one-off wires between individuals (house deposit, car purchase)
    k = 400
    add(day_times(k), rng.lognormal(np.log(12000), 0.6, k), "wire", rng.choice(active, k), rng.choice(active, k))

    # --- suspicious typologies -------------------------------------------
    pid = 0
    H = 3600

    def mules(k):
        return rng.choice(mule_pool, k, replace=False)

    for typ in TYPOLOGIES:
        for _ in range(instances_per_typology):
            lo = 42 if typ == "dormant_reactivation" else 1  # > 30 days after the dormant accounts' last activity
            t0 = int(rng.integers(lo, DAYS - 6) * 86400 + rng.integers(0, 86400))
            if typ == "fan_in":
                m = mules(3)
                hub, outs = m[0], m[1:1 + rng.integers(1, 3)]
                s = rng.choice(active, rng.integers(8, 21), replace=False)
                ts = t0 + rng.integers(0, 3 * 86400, len(s))
                amts = rng.uniform(200, 3000, len(s))
                add(ts, amts, rng.choice(["p2p", "ach"], len(s)), s, hub, 1, typ, pid)
                total = amts.sum() * rng.uniform(0.8, 0.95)
                tl = ts.max()
                for j, dst in enumerate(outs):
                    add(tl + rng.integers(H, 24 * H), total / len(outs), "wire", hub, dst, 1, typ, pid)
            elif typ == "fan_out":
                m = mules(2)
                src, hub = m
                big = rng.uniform(20000, 80000)
                add(t0, big, "wire", src, hub, 1, typ, pid)
                r = rng.choice(active, rng.integers(8, 16), replace=False)
                parts = rng.dirichlet(np.ones(len(r))) * big * rng.uniform(0.85, 0.98)
                add(t0 + rng.integers(H, 48 * H, len(r)), parts, rng.choice(["p2p", "ach", "wire"], len(r)), hub, r, 1, typ, pid)
            elif typ == "cycle":
                c = mules(rng.integers(3, 7))
                amt, t = rng.uniform(3000, 40000), t0
                for j in range(len(c)):
                    add(t, amt, rng.choice(["wire", "ach"]), c[j], c[(j + 1) % len(c)], 1, typ, pid)
                    amt *= rng.uniform(0.97, 0.995)
                    t += int(rng.integers(H, 12 * H))
            elif typ == "layering":
                c = mules(rng.integers(4, 8))
                amt, t = rng.uniform(5000, 60000), t0
                for j in range(len(c) - 1):
                    if rng.random() < 0.3:  # fragmentation through a side account, re-aggregated at next hop
                        side = mules(1)[0]
                        add(t, amt / 2, "wire", c[j], c[j + 1], 1, typ, pid)
                        add(t + 600, amt / 2, "wire", c[j], side, 1, typ, pid)
                        add(t + int(rng.integers(H, 6 * H)), amt / 2 * 0.99, "wire", side, c[j + 1], 1, typ, pid)
                    else:
                        add(t, amt, rng.choice(["wire", "ach"]), c[j], c[j + 1], 1, typ, pid)
                    amt *= rng.uniform(0.97, 0.995)
                    t += int(rng.integers(H, 24 * H))
            elif typ == "structuring":
                s = rng.choice(active)
                b = mules(rng.integers(1, 4))
                k = rng.integers(5, 13)
                add(t0 + rng.integers(0, 7 * 86400, k), rng.uniform(8500, 9950, k), rng.choice(["cash", "wire"], k), s, rng.choice(b, k), 1, typ, pid)
            elif typ == "dormant_reactivation":
                dacc = rng.choice(dormant)
                src = rng.choice(active, rng.integers(2, 5))
                dst = rng.choice(active, rng.integers(2, 5))
                ins = rng.uniform(3000, 15000, len(src))
                add(t0 + rng.integers(0, 12 * H, len(src)), ins, "wire", src, dacc, 1, typ, pid)
                outs = rng.dirichlet(np.ones(len(dst))) * ins.sum() * 0.95
                add(t0 + rng.integers(12 * H, 48 * H, len(dst)), outs, "wire", dacc, dst, 1, typ, pid)
            elif typ == "pass_through":
                hub = mules(1)[0]
                t = t0
                for _ in range(rng.integers(3, 7)):
                    amt = rng.uniform(1500, 12000)
                    add(t, amt, rng.choice(["ach", "wire"]), rng.choice(active), hub, 1, typ, pid)
                    add(t + int(rng.integers(H // 2, 6 * H)), amt * rng.uniform(0.9, 0.99), "wire", hub, rng.choice(active), 1, typ, pid)
                    t += int(rng.integers(12 * H, 48 * H))
            pid += 1

    tx = pd.concat(rows, ignore_index=True)
    open_t = ((acc.open_date - START) // pd.Timedelta(seconds=1)).to_numpy()
    alive = (tx.t >= open_t[tx.o]) & (tx.t >= open_t[tx.d])  # no activity before an account is opened
    tx = tx[(tx.o != tx.d) & (tx.t < DAYS * 86400) & alive].sort_values("t", kind="stable").reset_index(drop=True)
    ids = acc.account_id.to_numpy()
    out = pd.DataFrame({
        "transaction_id": [f"T{i:08d}" for i in range(len(tx))],
        "timestamp": START + pd.to_timedelta(tx.t, unit="s"),
        "amount": tx.amount,
        "currency": "USD",
        "channel": tx.channel,
        "origin_account_id": ids[tx.o],
        "destination_account_id": ids[tx.d],
        "is_suspicious": tx.label.astype("int8"),
        "typology": tx.typology,
        "pattern_id": tx.pattern_id,
    })
    return out, acc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    tx, acc = generate(a.seed)
    Path(a.out).mkdir(parents=True, exist_ok=True)
    tx.to_parquet(f"{a.out}/transactions.parquet", index=False)
    acc.to_parquet(f"{a.out}/accounts.parquet", index=False)
    print(f"{len(tx):,} transactions, {len(acc):,} accounts, {tx.is_suspicious.mean():.2%} suspicious")
    print(tx[tx.is_suspicious == 1].typology.value_counts().to_string())
