"""Explainable entity resolution (PRD §5): exact normalized-token match first, then fuzzy name/address/business
matching with per-field scores and a confidence band. Candidates are returned for review; nothing is merged.

    from ml.engine.resolution import resolve
    resolve({"name": "ACME Holdings, LLC", "address": "12 Main St"}, candidates)   # candidates: list of dicts with "id"
"""
import re
import unicodedata
from difflib import SequenceMatcher

LEGAL_SUFFIXES = {"inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company", "plc", "lp", "llp", "gmbh", "sa"}
STREET = {"street": "st", "avenue": "ave", "road": "rd", "suite": "ste", "boulevard": "blvd", "drive": "dr"}
FIELD_WEIGHTS = {"name": 0.6, "address": 0.25, "business_id": 0.15}  # ponytail: hand-set; tune on labelled pairs
BANDS = ((0.92, "high"), (0.80, "medium"), (0.65, "low"))  # below the lowest band: not a candidate


def normalize(text: str | None, field: str = "name") -> str:
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower()
    toks = re.sub(r"[^a-z0-9 ]+", " ", t.replace(".", "")).split()  # "L.L.C." -> "llc", "St." -> "st"
    if field == "name":
        toks = [x for x in toks if x not in LEGAL_SUFFIXES]
    elif field == "address":
        toks = [STREET.get(x, x) for x in toks]
    return " ".join(toks)


def _field_score(a: str, b: str) -> float:
    """Max of character similarity and token-set overlap, so word order and dropped tokens both count."""
    if not a or not b:
        return float("nan")
    ta, tb = set(a.split()), set(b.split())
    return max(SequenceMatcher(None, a, b).ratio(), len(ta & tb) / max(len(ta | tb), 1))


def score(query: dict, cand: dict) -> dict:
    fields = {}
    for f in FIELD_WEIGHTS:
        a, b = normalize(query.get(f), f), normalize(cand.get(f), f)
        if a and b:
            fields[f] = {"query": a, "candidate": b, "score": round(_field_score(a, b), 3), "exact": a == b}
    if not fields:
        return {"id": cand.get("id"), "score": 0.0, "band": None, "fields": {}, "method": "none"}
    if "business_id" in fields and fields["business_id"]["exact"]:
        total, method = 1.0, "exact_business_id"
    elif "name" in fields and fields["name"]["exact"]:
        total, method = max(0.95, _weighted(fields)), "exact_name"
    else:
        total, method = _weighted(fields), "fuzzy"
    band = next((b for th, b in BANDS if total >= th), None)
    return {"id": cand.get("id"), "score": round(total, 3), "band": band, "fields": fields, "method": method}


def _weighted(fields):
    w = {f: FIELD_WEIGHTS[f] for f in fields}  # renormalize over the fields both records actually have
    return sum(fields[f]["score"] * w[f] for f in fields) / sum(w.values())


def resolve(query: dict, candidates: list[dict], max_candidates: int = 5) -> list[dict]:
    """Ranked candidate matches with explainable field-level evidence. Never auto-merges: a human confirms."""
    qn = normalize(query.get("name"), "name").split()
    block = qn[0][:3] if qn else None  # blocking on the first name token's prefix keeps this sub-quadratic
    pool = [c for c in candidates if not block or normalize(c.get("name"), "name").startswith(block)
            or (query.get("business_id") and c.get("business_id") == query.get("business_id"))]
    scored = [s for s in (score(query, c) for c in pool) if s["band"]]
    return sorted(scored, key=lambda s: -s["score"])[:max_candidates]
