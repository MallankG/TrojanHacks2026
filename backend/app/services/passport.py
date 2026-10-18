"""Evidence passport: a compact, tamper-evident case package (PRD v3.3 section 20.1).

Each section is hashed over canonical JSON (sorted keys, compact separators, UTF-8). The package hash
covers the header and the per-section hashes, so editing any value changes a section hash and the
package hash. The package is not signed: signing needs key management the prototype does not have.
The frontend builds passports in the same format, so either side can verify the other's output.
"""

import hashlib
import json
from typing import Any

FORMAT = "tracelayer.evidence-passport/v1"
CANONICALIZATION = "json-sorted-keys-compact-utf8"
SECTIONS = (
    "source_references",
    "data_quality",
    "model",
    "feature_contributions",
    "graph_paths",
    "policy_passages",
    "copilot_claims",
    "human_decisions",
)
HEADER_FIELDS = ("format", "subject", "issued_at", "issued_by")
SIGNATURE_NOTE = "Not signed: key management is not configured for this prototype. Hashes give tamper evidence only."


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def package_hash(header: dict, section_hashes: dict[str, str]) -> str:
    return sha256_hex({**{k: header.get(k) for k in HEADER_FIELDS}, "sections": section_hashes})


def seal(header: dict, sections: dict[str, Any]) -> dict:
    """Attach the manifest (per-section hashes and the package hash) to a passport."""
    missing = [name for name in SECTIONS if name not in sections]
    if missing:
        raise ValueError(f"Passport is missing sections: {missing}")
    hashes = {name: sha256_hex(sections[name]) for name in SECTIONS}
    return {
        **{k: header.get(k) for k in HEADER_FIELDS},
        "sections": {name: sections[name] for name in SECTIONS},
        "manifest": {
            "algorithm": "sha256",
            "canonicalization": CANONICALIZATION,
            "sections": hashes,
            "package_hash": package_hash(header, hashes),
        },
        "signature": None,
        "signature_note": SIGNATURE_NOTE,
    }


def verify(passport: dict) -> dict:
    """Recompute every hash. Reports which sections changed rather than a single pass/fail."""
    if not isinstance(passport, dict) or passport.get("format") != FORMAT:
        return {"valid": False, "reason": f"Not a {FORMAT} document", "sections": [], "package_hash_matches": False}
    manifest = passport.get("manifest") or {}
    recorded = manifest.get("sections") or {}
    sections = passport.get("sections") or {}
    results = []
    for name in SECTIONS:
        if name not in sections:
            results.append({"section": name, "status": "missing"})
            continue
        actual = sha256_hex(sections[name])
        results.append({"section": name, "status": "match" if recorded.get(name) == actual else "modified",
                        "recorded": recorded.get(name), "actual": actual})
    # The header is covered only through the package hash, so recompute it from the recorded section hashes.
    header_ok = package_hash(passport, {name: recorded.get(name) for name in SECTIONS}) == manifest.get("package_hash")
    sections_ok = all(r["status"] == "match" for r in results)
    reason = None
    if not sections_ok:
        reason = "One or more sections changed after the passport was issued"
    elif not header_ok:
        reason = "The header or manifest changed after the passport was issued"
    return {"valid": sections_ok and header_ok, "reason": reason, "sections": results, "package_hash_matches": header_ok}


def _claim(c: dict) -> dict:
    return {
        "index": c.get("claim_index"),
        "text": c.get("claim_text"),
        "kind": c.get("claim_kind"),
        "verification": c.get("verification"),
        "transaction_ids": c.get("referenced_transaction_ids") or [],
        "account_ids": c.get("referenced_account_ids") or [],
        "cited_chunk_ids": c.get("cited_chunk_ids") or [],
    }


def build_sections(bundle: dict, model_runs: list[dict], narrative: dict | None, claims: list[dict],
                   decisions: list[dict]) -> dict[str, Any]:
    """Map the case evidence bundle (services.cases.evidence_bundle) onto the passport sections."""
    signals = bundle.get("risk_signals", [])
    return {
        "source_references": [
            {"transaction_id": t["id"], "transaction_ref": t.get("transaction_ref"), "occurred_at": t.get("occurred_at"),
             "amount": t.get("amount"), "currency": t.get("currency"), "source_row_id": t.get("source_row_id"),
             "ingestion_run_id": t.get("ingestion_run_id")}
            for t in bundle.get("transactions", [])
        ],
        "data_quality": [
            {"issue_id": i["id"], "source_row_id": i.get("source_row_id"), "issue_type": i.get("issue_type"),
             "field_name": i.get("field_name"), "severity": i.get("severity"), "status": i.get("status")}
            for i in bundle.get("data_quality_issues", [])
        ],
        "model": {
            "runs": [
                {"run_id": r["id"], "model_name": r.get("model_name"), "model_kind": r.get("model_kind"),
                 "model_version": r.get("model_version"), "feature_set_version": r.get("feature_set_version"),
                 "dataset_version": r.get("dataset_version"), "code_version": r.get("code_version")}
                for r in sorted(model_runs, key=lambda r: r["id"])
            ],
            "alerts": [
                {"alert_id": a["id"], "model_run_id": a.get("model_run_id"), "priority_band": a.get("priority_band"),
                 "rank_score": a.get("rank_score"), "ranking_strategy": a.get("ranking_strategy")}
                for a in bundle.get("alerts", [])
            ],
        },
        "feature_contributions": [
            {"signal_id": s["id"], "signal_name": s.get("signal_name"), "signal_version": s.get("signal_version"),
             "score": s.get("score"), "threshold": s.get("threshold"), "triggered": s.get("triggered"),
             "contributions": (s.get("details") or {}).get("contributions")}
            for s in signals
        ],
        "graph_paths": [
            {"evidence_id": e["id"], "transaction_ids": e.get("transaction_ids") or [], "explanation": e.get("explanation")}
            for e in bundle.get("evidence", []) if e.get("evidence_type") == "transaction_path"
        ],
        "policy_passages": [
            {"chunk_id": p["id"], "section": p.get("section"), "content": p.get("content"),
             "source": (p.get("policy_documents") or {}).get("source_url")}
            for p in bundle.get("policy_citations", [])
        ],
        "copilot_claims": {
            "narrative_id": narrative["id"] if narrative else None,
            "narrative_version": narrative.get("version") if narrative else None,
            "verification_status": narrative.get("verification_status") if narrative else None,
            "claims": [_claim(c) for c in sorted(claims, key=lambda c: c.get("claim_index") or 0)],
        },
        "human_decisions": [
            {"decision_id": d["id"], "decision": d.get("decision"), "disposition": d.get("disposition"),
             "decided_by": d.get("decided_by"), "decided_at": d.get("decided_at"), "narrative_id": d.get("narrative_id")}
            for d in sorted(decisions, key=lambda d: d.get("decided_at") or "")
        ],
    }
