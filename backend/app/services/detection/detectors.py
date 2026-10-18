"""Deterministic typology detectors over a Ledger. Pure functions: no database access.

Each detector returns Findings that carry the exact evidence transaction ids, the parameters it used and a plain
reason. A Finding is a lead for human review; it never states that anyone did anything wrong.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import median

from app.services import graph
from app.services.detection import config
from app.services.detection.ledger import Ledger, Txn, hours, total

ROLE_RANK = {"subject": 0, "intermediary": 1, "counterparty": 2}


@dataclass
class Finding:
    detector: str
    typology: str
    score: float
    threshold: float
    strength: float
    evidence: list[str]  # transaction ids; the first is the triggering transaction
    accounts: dict[str, str]  # account id -> subject | intermediary | counterparty
    window_start: datetime
    window_end: datetime
    amount_usd: Decimal
    details: dict
    subject: str | None = None
    transaction_id: str | None = None
    source: str = "rule"
    version: str = config.RULES_VERSION
    triggered: bool = True
    signal_id: str | None = None  # set once written to risk_signals
    extra: dict = field(default_factory=dict)


def _roles(subject: str | None, counterparties=(), intermediaries=()) -> dict[str, str]:
    roles: dict[str, str] = {}
    for role, ids in (("counterparty", counterparties), ("intermediary", intermediaries), ("subject", [subject] if subject else [])):
        for a in ids:
            if a not in roles or ROLE_RANK[role] < ROLE_RANK[roles[a]]:
                roles[a] = role
    return roles


def _finding(name: str, score: float, txns: list[Txn], amount: Decimal, details: dict, subject: str | None,
             counterparties=(), intermediaries=(), params: dict | None = None) -> Finding:
    cfg = params or config.DETECTORS[name]
    ids = list(dict.fromkeys(t.id for t in txns))
    return Finding(
        detector=name, typology=cfg["typology"], score=float(score), threshold=float(cfg["threshold"]),
        strength=config.strength(float(score), float(cfg["threshold"]), float(cfg["saturation"])),
        evidence=ids, accounts=_roles(subject, counterparties, intermediaries),
        window_start=min(t.at for t in txns), window_end=max(t.at for t in txns), amount_usd=amount,
        details={**details, "parameters": {k: v for k, v in cfg.items() if k not in ("description",)},
                 "description": cfg["description"]},
        subject=subject, transaction_id=ids[0] if len(ids) == 1 else None,
    )


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


# --- rapid pass-through ---------------------------------------------------------------------------
def rapid_pass_through(ledger: Ledger, cfg: dict | None = None) -> list[Finding]:
    cfg = cfg or config.DETECTORS["rapid_pass_through"]
    hold = timedelta(hours=cfg["max_hold_hours"])
    findings = []
    for account, inflows in ledger.inflows.items():
        episodes, txns = [], []
        for inflow in inflows:
            if inflow.usd < Decimal(str(cfg["min_inflow_usd"])):
                continue
            outs = [o for o in ledger.out_between(account, inflow.at, inflow.at + hold) if o.id != inflow.id]
            if not outs:
                continue
            ratio = float(total(outs) / inflow.usd)
            if cfg["min_forward_ratio"] <= ratio <= cfg["max_forward_ratio"]:
                episodes.append({"inflow_transaction_id": inflow.id, "inflow_usd": _money(inflow.usd),
                                 "forwarded_usd": _money(total(outs)), "forward_ratio": round(ratio, 4),
                                 "outflows": len(outs), "hold_hours": hours(outs[-1].at - inflow.at)})
                txns += [inflow, *outs]
        if len(episodes) >= cfg["threshold"]:
            inflow_ids = {e["inflow_transaction_id"] for e in episodes}
            amount = total(t for t in dict.fromkeys(txns) if t.id in inflow_ids)
            reason = (f"{len(episodes)} inflow(s) of at least {cfg['min_inflow_usd']} USD were forwarded out "
                      f"({cfg['min_forward_ratio']:.0%}-{cfg['max_forward_ratio']:.0%} of the inflow) within "
                      f"{cfg['max_hold_hours']}h")
            findings.append(_finding("rapid_pass_through", len(episodes), txns, amount,
                                     {"reason": reason, "episodes": episodes[:20]}, account,
                                     counterparties=[t.origin for t in txns] + [t.destination for t in txns], params=cfg))
    return findings


# --- funnel / fan-in --------------------------------------------------------------------------------
def _weekly_sender_baseline(ledger: Ledger, account: str, exclude: tuple[datetime, datetime], days: int) -> float:
    """Median distinct senders per `days`-long bucket over the account's observed life, excluding the burst window."""
    first, end = ledger.first_seen(account), ledger.end
    if first is None or end is None:
        return 0.0
    span, buckets, start = timedelta(days=days), [], first
    while start <= end:
        stop = start + span
        if not (start < exclude[1] and stop > exclude[0]):
            buckets.append(len({t.origin for t in ledger.in_between(account, start, stop) if t.at < stop}))
        start = stop
    return float(median(buckets)) if buckets else 0.0


def funnel_fan_in(ledger: Ledger, cfg: dict | None = None) -> list[Finding]:
    cfg = cfg or config.DETECTORS["funnel_fan_in"]
    window, grace = timedelta(days=cfg["window_days"]), timedelta(hours=cfg["outflow_grace_hours"])
    findings = []
    for account, inflows in ledger.inflows.items():
        best = None
        for anchor in inflows:
            ins = ledger.in_between(account, anchor.at, anchor.at + window)
            senders = {t.origin for t in ins} - {account}
            inflow_usd = total(ins)
            if len(senders) < cfg["min_senders"] or inflow_usd < Decimal(str(cfg["min_inflow_usd"])):
                continue
            if best and len(senders) <= best["senders"]:
                continue
            best = {"anchor": anchor, "ins": ins, "senders": len(senders), "inflow_usd": inflow_usd, "sender_ids": senders}
        if not best:
            continue
        start = best["anchor"].at
        baseline = _weekly_sender_baseline(ledger, account, (start, start + window), cfg["window_days"])
        multiple = best["senders"] / max(baseline, 1.0)
        outs = ledger.out_between(account, start, start + window + grace, include_start=True)
        out_usd = total(outs)
        out_ratio = float(out_usd / best["inflow_usd"]) if best["inflow_usd"] else 0.0
        by_dest: dict[str, Decimal] = defaultdict(Decimal)
        for o in outs:
            by_dest[o.destination] += o.usd
        top = sorted(by_dest.values(), reverse=True)[: cfg["top_destinations"]]
        concentration = float(sum(top) / out_usd) if out_usd else 0.0
        checks = {"baseline_multiple": round(multiple, 2), "outflow_ratio": round(out_ratio, 4),
                  "destination_concentration": round(concentration, 4)}
        if (multiple < cfg["baseline_multiple"] or out_ratio < cfg["min_outflow_ratio"]
                or concentration < cfg["min_destination_concentration"]):
            continue
        reason = (f"{best['senders']} distinct senders paid {_money(best['inflow_usd'])} USD in {cfg['window_days']} days "
                  f"({multiple:.1f}x the account's usual {baseline:g} senders per period); {out_ratio:.0%} left again, "
                  f"{concentration:.0%} of it to the top {cfg['top_destinations']} destination(s)")
        findings.append(_finding(
            "funnel_fan_in", best["senders"], best["ins"] + outs, best["inflow_usd"],
            {"reason": reason, "baseline_senders": baseline, **checks, "outflow_usd": _money(out_usd)}, account,
            counterparties=list(best["sender_ids"]) + list(by_dest), params=cfg))
    return findings


# --- fan-out / dispersal ----------------------------------------------------------------------------
def fan_out_dispersal(ledger: Ledger, cfg: dict | None = None) -> list[Finding]:
    cfg = cfg or config.DETECTORS["fan_out_dispersal"]
    window, lookback = timedelta(hours=cfg["window_hours"]), timedelta(hours=cfg["funding_lookback_hours"])
    findings = []
    for account, outflows in ledger.outflows.items():
        best, used = None, set()
        for i, anchor in enumerate(outflows):
            if anchor.id in used:
                continue
            outs = ledger.out_between(account, anchor.at, anchor.at + window, include_start=True)
            recipients = {o.destination for o in outs} - {account}
            out_usd = total(outs)
            if len(recipients) < cfg["min_recipients"] or out_usd < Decimal(str(cfg["min_total_usd"])):
                continue
            used.update(o.id for o in outs)
            paid_before = {o.destination for o in outflows[:i]}
            new_share = len(recipients - paid_before) / len(recipients)
            funding = [t for t in ledger.in_between(account, anchor.at - lookback, outs[-1].at)]
            funded_ratio = float(total(funding) / out_usd)
            if new_share < cfg["min_new_recipient_share"] or funded_ratio < cfg["min_funded_ratio"]:
                continue
            if best is None or len(recipients) > best["recipients"]:
                best = {"outs": outs, "funding": funding, "recipients": len(recipients), "new_share": new_share,
                        "funded_ratio": funded_ratio, "out_usd": out_usd, "recipient_ids": recipients}
        if not best:
            continue
        reason = (f"Paid {best['recipients']} recipients ({best['new_share']:.0%} never paid before) "
                  f"{_money(best['out_usd'])} USD within {cfg['window_hours']}h, funded {best['funded_ratio']:.0%} by "
                  f"inflows in the preceding {cfg['funding_lookback_hours']}h")
        findings.append(_finding(
            "fan_out_dispersal", best["recipients"], best["outs"] + best["funding"], best["out_usd"],
            {"reason": reason, "new_recipient_share": round(best["new_share"], 4),
             "funded_ratio": round(best["funded_ratio"], 4), "dispersed_usd": _money(best["out_usd"])}, account,
            counterparties=list(best["recipient_ids"]) + [t.origin for t in best["funding"]], params=cfg))
    return findings


# --- bounded cycles ---------------------------------------------------------------------------------
def bounded_cycles(ledger: Ledger, cfg: dict | None = None) -> list[Finding]:
    cfg = cfg or config.DETECTORS["bounded_cycle"]
    min_amt = Decimal(str(cfg["min_amount_usd"]))
    gap, span = timedelta(hours=cfg["max_gap_hours"]), timedelta(days=cfg["max_cycle_days"])
    out = {a: [t for t in ts if t.usd >= min_amt and t.destination != a] for a, ts in ledger.outflows.items()}
    occurrences: dict[tuple, list[list[Txn]]] = defaultdict(list)
    budget = [config.LIMITS["dfs_expansion_budget"]]

    def dfs(start: str, path: list[Txn], visited: set[str]) -> None:
        if budget[0] <= 0:
            return
        budget[0] -= 1
        last = path[-1]
        for e in out.get(last.destination, []):
            if e.at <= last.at or e.at - last.at > gap or e.at - path[0].at > span:
                continue
            if e.usd < last.usd * Decimal(str(cfg["min_amount_ratio"])):
                continue
            if e.destination == start and len(path) + 1 >= cfg["min_hops"]:
                cyc = path + [e]
                nodes = [t.origin for t in cyc]
                pivot = nodes.index(min(nodes))  # canonical rotation so the same loop groups together
                occurrences[tuple(nodes[pivot:] + nodes[:pivot])].append(cyc)
                continue
            if e.destination in visited or len(path) + 1 >= cfg["max_hops"]:
                continue
            dfs(start, path + [e], visited | {e.destination})

    for account, edges in out.items():
        for e in edges:
            dfs(account, [e], {account, e.destination})

    findings = []
    for nodes, cycles in occurrences.items():
        # Keep disjoint occurrences only (the same loop can be found from several starting edges).
        chosen, seen = [], set()
        for cyc in sorted(cycles, key=lambda c: c[0].at):
            ids = {t.id for t in cyc}
            if ids & seen:
                continue
            chosen.append(cyc)
            seen |= ids
        txns = [t for c in chosen for t in c]
        reps = [{"start": c[0].at.isoformat(), "start_usd": _money(c[0].usd), "end_usd": _money(c[-1].usd),
                 "elapsed_hours": hours(c[-1].at - c[0].at), "transaction_ids": [t.id for t in c]} for c in chosen]
        reason = (f"Funds returned to the starting account through {len(nodes)} hops {len(chosen)} time(s), each loop "
                  f"within {cfg['max_cycle_days']} days and keeping at least {cfg['min_amount_ratio']:.0%} per hop")
        findings.append(_finding(
            "bounded_cycle", len(chosen), txns, max(c[0].usd for c in chosen),
            {"reason": reason, "hops": len(nodes), "account_cycle": list(nodes), "repetitions": reps,
             "search_budget_exhausted": budget[0] <= 0}, nodes[0], intermediaries=list(nodes[1:]), params=cfg))
    return findings


# --- layering chains --------------------------------------------------------------------------------
def layering_chains(ledger: Ledger, cfg: dict | None = None) -> list[Finding]:
    cfg = cfg or config.DETECTORS["layering_chain"]
    edges = [t.edge() for t in ledger.txns if t.origin != t.destination]
    min_start = Decimal(str(cfg["min_start_usd"]))
    candidates = []
    for source in sorted({t.origin for t in ledger.txns if t.usd >= min_start}):
        for p in graph.time_respecting_paths(edges, source, None, cfg["max_hops"], timedelta(hours=cfg["max_gap_hours"]),
                                             cfg["min_amount_ratio"], cfg["path_limit"]):
            first = ledger.by_id[p["hops"][0]["transaction_id"]]
            if p["length"] >= cfg["min_hops"] and first.usd >= min_start:
                candidates.append(p)
    kept: list[dict] = []
    for p in sorted(candidates, key=lambda p: (-p["length"], p["hops"][0]["occurred_at"])):
        ids = {h["transaction_id"] for h in p["hops"]}
        if any(ids <= {h["transaction_id"] for h in k["hops"]} for k in kept):
            continue
        kept.append(p)
    findings = []
    for p in kept:
        txns = [ledger.by_id[h["transaction_id"]] for h in p["hops"]]
        nodes = [txns[0].origin] + [t.destination for t in txns]
        retained = float(txns[-1].usd / txns[0].usd)
        reason = (f"{p['length']}-hop time-ordered chain over {p['elapsed_hours']}h; the last hop still carries "
                  f"{retained:.0%} of the first ({_money(txns[0].usd)} -> {_money(txns[-1].usd)} USD)")
        findings.append(_finding(
            "layering_chain", p["length"], txns, txns[0].usd,
            {"reason": reason, "account_path": nodes, "retained_ratio": round(retained, 4),
             "elapsed_hours": p["elapsed_hours"], "max_gap_hours_observed": max((h["gap_hours"] or 0) for h in p["hops"]),
             "caveat": "Timing and amounts only; this does not prove the same funds moved through each account."},
            nodes[1], counterparties=[nodes[0], nodes[-1]], intermediaries=nodes[1:-1], params=cfg))
    return findings[: config.LIMITS["max_signals_per_detector"]]


# --- structuring-like deposits ---------------------------------------------------------------------
def structuring_band(ledger: Ledger, cfg: dict | None = None) -> list[Finding]:
    cfg = cfg or config.DETECTORS["structuring_band"]
    low, high = Decimal(str(cfg["band_low_usd"])), Decimal(str(cfg["band_high_usd"]))
    window = timedelta(days=cfg["window_days"])
    findings = []
    for account, inflows in ledger.inflows.items():
        cash = [t for t in inflows if t.channel == "cash"]
        best = None
        for anchor in cash:
            if not low <= anchor.usd < high:
                continue
            deposits = [t for t in cash if anchor.at <= t.at <= anchor.at + window]
            in_band = [t for t in deposits if low <= t.usd < high]
            share = len(in_band) / len(deposits)
            above = sum(1 for t in deposits if t.usd >= high)
            if len(in_band) >= cfg["min_deposits"] and share >= cfg["min_in_band_share"] and not above:
                if best is None or len(in_band) > len(best[0]):
                    best = (in_band, share, deposits)
        if not best:
            continue
        in_band, share, deposits = best
        reason = (f"{len(in_band)} cash deposits between {cfg['band_low_usd']} and {cfg['band_high_usd']} USD within "
                  f"{cfg['window_days']} days ({share:.0%} of the account's cash deposits in that window, none above "
                  f"the band) at {len({t.origin for t in in_band})} deposit point(s)")
        findings.append(_finding(
            "structuring_band", len(in_band), in_band, total(in_band),
            {"reason": reason, "in_band_share": round(share, 4), "deposit_points": len({t.origin for t in in_band}),
             "band_note": "Configurable review band, not a legal threshold."},
            account, counterparties=[t.origin for t in in_band], params=cfg))
    return findings


# --- dormant reactivation --------------------------------------------------------------------------
def dormant_reactivation(ledger: Ledger, cfg: dict | None = None) -> list[Finding]:
    cfg = cfg or config.DETECTORS["dormant_reactivation"]
    min_gap, react = timedelta(days=cfg["min_gap_days"]), timedelta(days=cfg["reactivation_days"])
    findings = []
    for account, events in ledger.events.items():
        for i in range(cfg["min_prior_events"] - 1, len(events) - 1):
            before, after = events[i], events[i + 1]
            gap = after.at - before.at
            if gap < min_gap:
                continue
            prior = events[: i + 1]
            burst = [t for t in events[i + 1:] if t.at <= after.at + react]
            volume = total(burst)
            prior_days = max(30.0, (before.at - prior[0].at).total_seconds() / 86400)
            prior_monthly = total(prior) / Decimal(str(prior_days / 30))
            multiple = float(volume / prior_monthly) if prior_monthly else float("inf")
            if volume < Decimal(str(cfg["min_reactivation_usd"])) or multiple < cfg["min_volume_multiple"]:
                continue
            gap_days = round(gap.total_seconds() / 86400, 1)
            reason = (f"Silent for {gap_days} days after {len(prior)} earlier transaction(s), then moved "
                      f"{_money(volume)} USD in {cfg['reactivation_days']} days "
                      f"({'no' if multiple == float('inf') else f'{multiple:.1f}x'} its pre-dormancy monthly volume)")
            findings.append(_finding(
                "dormant_reactivation", gap_days, burst, volume,
                {"reason": reason, "gap_days": gap_days, "last_activity_before": before.at.isoformat(),
                 "reactivated_at": after.at.isoformat(), "pre_dormancy_monthly_usd": _money(prior_monthly),
                 "volume_multiple": None if multiple == float("inf") else round(multiple, 2),
                 "context_transaction_id": before.id},
                account, counterparties=[t.origin for t in burst] + [t.destination for t in burst], params=cfg))
            break  # one reactivation per account per run
    return findings


# --- large payment to a new beneficiary (scam-proceeds pattern) -------------------------------------
def new_beneficiary_large_payment(ledger: Ledger, cfg: dict | None = None) -> list[Finding]:
    cfg = cfg or config.DETECTORS["new_beneficiary_large_payment"]
    fwd_window = timedelta(hours=cfg["forward_hours"])
    max_age = timedelta(days=cfg["max_beneficiary_age_days"])
    findings = []
    for payer, outflows in ledger.outflows.items():
        for i, pay in enumerate(outflows):
            prior = outflows[:i]
            if pay.usd < Decimal(str(cfg["min_amount_usd"])) or len(prior) < cfg["min_prior_outflows"]:
                continue
            ben = pay.destination
            if ben == payer or ben in {p.destination for p in prior}:
                continue
            ben_first = ledger.first_seen(ben)
            if ben_first is None or pay.at - ben_first > max_age:
                continue
            med = median(p.usd for p in prior)
            multiple = float(pay.usd / med) if med else float("inf")
            if multiple < cfg["min_median_multiple"]:
                continue
            # A payer forwarding money it just received is a conduit, not a payer from its own funds.
            recent_in = total(ledger.in_between(payer, pay.at - fwd_window, pay.at))
            if recent_in >= pay.usd * Decimal(str(cfg["max_payer_recent_inflow_ratio"])):
                continue
            forwards = ledger.out_between(ben, pay.at, pay.at + fwd_window)
            fwd_ratio = float(total(forwards) / pay.usd)
            if fwd_ratio < cfg["min_forward_ratio"]:
                continue
            reason = (f"An account with {len(prior)} earlier payments (median {_money(med)} USD) sent {_money(pay.usd)} "
                      f"USD ({multiple:.0f}x its median) to a beneficiary it had never paid, first seen "
                      f"{round((pay.at - ben_first).total_seconds() / 86400, 1)} days earlier, which forwarded "
                      f"{fwd_ratio:.0%} of it within {cfg['forward_hours']}h")
            findings.append(_finding(
                "new_beneficiary_large_payment", round(min(multiple, 1e5), 2), [pay, *forwards], pay.usd,
                {"reason": reason, "payer_account_id": payer, "payer_median_usd": _money(med),
                 "forward_ratio": round(fwd_ratio, 4), "forwarded_to": sorted({f.destination for f in forwards})},
                ben, counterparties=[payer] + [f.destination for f in forwards], params=cfg))
    return findings


RULE_DETECTORS = {
    "rapid_pass_through": rapid_pass_through,
    "funnel_fan_in": funnel_fan_in,
    "fan_out_dispersal": fan_out_dispersal,
    "bounded_cycle": bounded_cycles,
    "layering_chain": layering_chains,
    "structuring_band": structuring_band,
    "dormant_reactivation": dormant_reactivation,
    "new_beneficiary_large_payment": new_beneficiary_large_payment,
}


def run_rules(ledger: Ledger) -> list[Finding]:
    findings: list[Finding] = []
    cap = config.LIMITS["max_signals_per_detector"]
    for detect in RULE_DETECTORS.values():
        found = sorted(detect(ledger), key=lambda f: (-f.strength, f.window_start))
        findings += found[:cap]
    return findings
