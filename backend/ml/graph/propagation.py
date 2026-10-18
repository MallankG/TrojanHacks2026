"""Network propagation (PRD v3.3 §5 item 8): after a human disposition, list bounded 1–2-hop neighbours to
re-evaluate. Proximity creates a review LEAD with its reason; it never labels anyone suspicious.
"""
from .adapter import LimitExceeded

PROPAGATING_DISPOSITIONS = frozenset({"escalated", "confirmed_suspicious"})


def propagation_leads(adapter, tenant_id, seed_account_id, disposition, *, case_id=None, hops=1, start=None, end=None,
                      min_amount=0.0, max_leads=50, propagating=PROPAGATING_DISPOSITIONS):
    """Neighbours of a dispositioned seed, each with the transactions that connect it and a plain-language reason.

    Returns [] for dispositions that should not propagate (e.g. false_positive). hops is capped at 2.
    """
    if hops not in (1, 2):
        raise LimitExceeded("propagation hops must be 1 or 2")
    if disposition not in propagating:
        return []
    nb = adapter.get_neighborhood(tenant_id, seed_account_id, hops=hops, start=start, end=end)
    hop_of = {n["entity_id"]: n["hop"] for n in nb["nodes"]}
    via: dict[str, list[dict]] = {}
    for e in nb["edges"]:
        if e["amount"] < min_amount:
            continue
        for acct in (e["origin_account_id"], e["destination_account_id"]):
            if acct != seed_account_id:
                via.setdefault(acct, []).append(e)
    seed = {"account_id": seed_account_id, "disposition": disposition, "case_id": case_id}
    leads = []
    for acct, es in via.items():
        h = hop_of.get(acct, hops)
        direct = [e for e in es if seed_account_id in (e["origin_account_id"], e["destination_account_id"])]
        total = round(sum(e["amount"] for e in (direct or es)), 2)
        reason = (f"{'Direct counterparty' if h == 1 else 'Two-hop neighbour'} of account {seed_account_id} "
                  f"(dispositioned '{disposition}'{f' on case {case_id}' if case_id else ''}): "
                  f"{len(direct or es)} linking transaction(s) totalling {total:,.2f}. Review lead only, not a finding.")
        leads.append({"account_id": acct, "hops": h, "reason": reason, "lead_type": "review_lead",
                      "via_transaction_ids": [e["transaction_id"] for e in (direct or es)], "linked_amount": total, "seed": seed})
    leads.sort(key=lambda l: (l["hops"], -l["linked_amount"], l["account_id"]))
    return leads[:max_leads]
