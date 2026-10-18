"""model_explanation, quality_lineage, policy_retrieval, similar_cases and shared_token_search
(the last two are mocks over fixtures)."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import Field

from tracelayer_agents.chat.session import CaseScope
from tracelayer_agents.mocks import FIXTURES
from tracelayer_agents.nodes.derive import NOT_AVAILABLE
from tracelayer_agents.nodes.retrieve_policy import build_query
from tracelayer_agents.schemas import DetectorOutput, ToolRefs
from tracelayer_agents.tools.base import (
    MAX_ROWS,
    ToolInput,
    ToolOutput,
    cap,
    require_accounts,
    require_txs,
    uniq,
)
from tracelayer_agents.wrappers import NodeContext

# --------------------------------------------------------------------------- model_explanation


class ModelExplanationInput(ToolInput):
    tx_ids: list[str] = Field(default_factory=list, max_length=MAX_ROWS)


def check_model(args: ModelExplanationInput, scope: CaseScope) -> None:
    require_txs(args.tx_ids, scope)


def model_explanation(
    args: ModelExplanationInput, scope: CaseScope, ctx: NodeContext
) -> ToolOutput:
    alert = scope.alert
    tx_ids = uniq(args.tx_ids or alert.tx_ids)
    rows: list[dict[str, Any]] = [{
        "kind": "composite", "composite_score": round(alert.composite_score, 4),
        "tier": alert.tier, "rank": alert.rank, "typology_tags": list(alert.typology_tags),
    }]
    rows += [
        {"kind": "component", "detector": name, "component_score": round(score, 4),
         "model_version": alert.model_versions.get(name)}
        for name, score in sorted(alert.component_scores.items())
    ]
    warnings: list[str] = []
    outputs: list[DetectorOutput] = []
    for label, call in (("graph risk scorer", lambda: [ctx.services.graph_risk.score(
                            scope.graph_snapshot_id)]),
                        ("behaviour scorers", lambda: ctx.services.behavior.score(
                            scope.graph_snapshot_id))):
        try:
            outputs += call()
        except Exception as exc:
            warnings.append(f"{label} unavailable ({type(exc).__name__}); its top factors "
                            "were not checked")
    for out in outputs:
        if out.status != "ok":
            warnings.append(f"detector {out.detector_name} reported unavailable")
            continue
        for tx_id in tx_ids:
            if tx_id in out.top_factors:
                rows.append({
                    "kind": "factor", "detector": out.detector_name, "tx_id": tx_id,
                    "score": round(out.scores.get(tx_id, 0.0), 4),
                    "factors": [f.model_dump(mode="json") for f in out.top_factors[tx_id]],
                })
    rows, truncated = cap(rows)
    return ToolOutput(
        rows=rows, warnings=warnings, truncated=truncated,
        refs=ToolRefs(tx_ids=tx_ids, model_run_id=alert.model_run_id),
    )


# --------------------------------------------------------------------------- quality_lineage


class EmptyInput(ToolInput):
    pass


def quality_lineage(args: EmptyInput, scope: CaseScope, ctx: NodeContext) -> ToolOutput:
    alert, bundle = scope.alert, scope.bundle
    rows: list[dict[str, Any]] = [{
        "kind": "governance", "quality_status": alert.quality_status,
        "confidence_caveat": alert.confidence_caveat,
        "graph_snapshot_id": scope.graph_snapshot_id,
    }]
    rows += [{"kind": "quality_issue", **i.model_dump(mode="json", exclude={"sample_record_ids"})}
             for i in bundle.quality_caveats]
    rows += [{"kind": "lineage", "ref": ref} for ref in bundle.lineage_refs]
    rows += [{"kind": "not_available", "item": item, "note": note}
             for item, note in NOT_AVAILABLE]
    rows, truncated = cap(rows)
    return ToolOutput(
        rows=rows, truncated=truncated,
        refs=ToolRefs(evidence_ids=[*bundle.lineage_refs,
                                    *(i.check_id for i in bundle.quality_caveats),
                                    f"graph_snapshot:{scope.graph_snapshot_id}"]),
    )


# --------------------------------------------------------------------------- policy_retrieval


class PolicyRetrievalInput(ToolInput):
    query: str | None = Field(default=None, max_length=300)


def policy_retrieval(
    args: PolicyRetrievalInput, scope: CaseScope, ctx: NodeContext
) -> ToolOutput:
    query = (args.query or "").strip() or build_query(
        {"alert": scope.alert, "evidence_bundle": scope.bundle}
    )
    if not query:
        return ToolOutput(warnings=["no query: the alert has no typology tags or flow findings"])
    chunks = ctx.services.policy.search(query, {}, ctx.config.policy_top_k)
    rows = [c.model_dump(mode="json", include={"chunk_id", "doc_title", "section", "source_uri",
                                                "excerpt", "score"}) for c in chunks]
    rows, truncated = cap(rows)
    return ToolOutput(rows=rows, truncated=truncated,
                      refs=ToolRefs(chunk_ids=[r["chunk_id"] for r in rows]))


# --------------------------------------------------------------------------- similar_cases


SIMILAR_METHOD = "synthetic_case_library_v1"
SIMILAR_TOP_K = 5
SIMILAR_CAVEAT = "Similarity is not proof; prior decisions can be wrong."
DEFAULT_SINCE_DAYS = 730
_LIBRARY_PATH = FIXTURES / "case_library.json"
_GRAPH_FEATURES = ("accounts", "transactions", "max_out_degree", "max_in_degree", "span_hours")
_WORD_RE = re.compile(r"[a-z]+")
_STOP_WORDS = frozenset({"a", "an", "the", "of", "to", "by", "in", "on", "and", "then", "one",
                         "within", "each", "other", "from", "into", "out", "same"})


class SimilarCasesInput(ToolInput):
    """`typology`: None filters by the alert's typology tags, "any" turns the filter off.
    `since_days`: only library cases closed at most this many days before the case window end."""

    typology: str | None = Field(default=None, max_length=40, pattern=r"^[a-z_]+$")
    since_days: int = Field(default=DEFAULT_SINCE_DAYS, ge=1, le=3650)


def _words(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOP_WORDS}


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def _flow_shape(motifs: set[str]) -> str:
    shapes = {"fan_out": "one_to_many", "fan_in": "many_to_one", "pass_through": "chain",
              "paths": "chain", "cycle": "loop"}
    found = {shapes[m] for m in motifs if m in shapes}
    return found.pop() if len(found) == 1 else "mixed" if found else "none"


def case_features(scope: CaseScope) -> dict[str, Any]:
    """The current case described the way the library is: typology, dominant rail, flow motifs
    and shape, graph features of the evidence, and descriptive words."""
    txs = scope.bundle.transactions
    motifs = set(scope.alert.typology_tags)
    findings = scope.bundle.flow_findings
    if findings is not None:
        motifs |= {"cycle" if k == "cycles" else k
                   for k in ("fan_in", "fan_out", "pass_through", "cycles", "paths")
                   if getattr(findings, k)}
    channels = Counter(t.channel for t in txs if t.channel)
    rail = sorted(channels.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if channels else None
    outs = Counter(t.origin_account_id for t in txs)
    ins = Counter(t.destination_account_id for t in txs)
    times = sorted(t.timestamp for t in txs)
    shape = _flow_shape(motifs)
    return {
        "typologies": sorted(scope.alert.typology_tags),
        "rail": rail,
        "motifs": sorted(motifs),
        "flow_shape": shape,
        "graph_features": {
            "accounts": len(scope.evidence_account_ids),
            "transactions": len(txs),
            "max_out_degree": max(outs.values()),
            "max_in_degree": max(ins.values()),
            "span_hours": round((times[-1] - times[0]).total_seconds() / 3600, 2),
        },
        "words": _words(" ".join([*motifs, shape, rail or "", "transfers recipients accounts"])
                        .replace("_", " ")),
    }


def _graph_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """Mean over features of 1 - |a-b| / max(a, b) (1.0 when both are 0)."""
    parts = []
    for k in _GRAPH_FEATURES:
        x, y = float(a.get(k, 0)), float(b.get(k, 0))
        parts.append(1.0 if max(x, y) == 0 else 1 - abs(x - y) / max(x, y))
    return sum(parts) / len(parts)


def similar_cases(args: SimilarCasesInput, scope: CaseScope, ctx: NodeContext) -> ToolOutput:
    """Mock: ranks a fixed library of synthetic closed cases against this case. Never returns
    another real case's data: library entries are synthetic references with no customer data.

    Score = plain mean of five components in [0, 1] (motif Jaccard, rail match, flow-shape match,
    graph-feature similarity, word overlap). # TODO(review): equal weights are a placeholder,
    not fitted; replace with the real similar-case service."""
    me = case_features(scope)
    typologies = (set() if args.typology == "any"
                  else {args.typology} if args.typology else set(me["typologies"]))
    cutoff = scope.window_end - timedelta(days=args.since_days)
    library = json.loads(_LIBRARY_PATH.read_text())
    candidates = [c for c in library
                  if (not typologies or c["typology"] in typologies)
                  and cutoff <= datetime.fromisoformat(c["closed_at"]) <= scope.window_end]
    rows = []
    for c in candidates:
        parts = {
            "motif_overlap": round(_jaccard(set(me["motifs"]), set(c["motifs"])), 3),
            "rail_match": 1.0 if me["rail"] and me["rail"] == c["rail"] else 0.0,
            "flow_shape_match": 1.0 if me["flow_shape"] == c["flow_shape"] else 0.0,
            "graph_similarity": round(_graph_similarity(me["graph_features"],
                                                        c["graph_features"]), 3),
            "text_overlap": round(_jaccard(me["words"], _words(c["summary"])), 3),
        }
        rows.append({
            "method": SIMILAR_METHOD, "case_ref": c["case_ref"], "typology": c["typology"],
            "similarity": round(sum(parts.values()) / len(parts), 3), "components": parts,
            "matched_motifs": sorted(set(me["motifs"]) & set(c["motifs"])),
            "rail": c["rail"], "flow_shape": c["flow_shape"],
            "graph_features": c["graph_features"], "summary": c["summary"],
            "prior_disposition": c["prior_disposition"], "caveat": SIMILAR_CAVEAT,
        })
    rows.sort(key=lambda r: (-r["similarity"], r["case_ref"]))
    truncated = len(rows) > SIMILAR_TOP_K
    rows = rows[:SIMILAR_TOP_K]
    return ToolOutput(
        rows=rows, truncated=truncated,
        warnings=[] if rows else ["no synthetic library case matches this typology and window"],
        refs=ToolRefs(evidence_ids=[r["case_ref"] for r in rows]),
    )


# --------------------------------------------------------------------------- shared_token_search


_TOKENS_PATH = FIXTURES / "shared_tokens.json"
TokenType = Literal["device", "ip", "token"]


class SharedTokenSearchInput(ToolInput):
    token_types: list[TokenType] = Field(default_factory=lambda: ["device", "ip", "token"],
                                         min_length=1, max_length=3)
    account_ids: list[str] = Field(default_factory=list, max_length=MAX_ROWS)


def check_tokens(args: SharedTokenSearchInput, scope: CaseScope) -> None:
    require_accounts(args.account_ids, scope)


def shared_token_search(
    args: SharedTokenSearchInput, scope: CaseScope, ctx: NodeContext
) -> ToolOutput:
    """Mock: devices, IP addresses and payment tokens shared by at least two of this case's
    accounts (`mocks/fixtures/shared_tokens.json`). Accounts outside the case are only counted,
    never named. # TODO(review): backend to supply the real identifier links."""
    focus = set(args.account_ids) or set(scope.account_ids)
    rows = []
    for t in json.loads(_TOKENS_PATH.read_text()):
        if t["token_type"] not in args.token_types:
            continue
        inside = sorted(a for a in t["account_ids"] if a in scope.account_ids)
        if len(inside) < 2 or not focus & set(inside):
            continue
        rows.append({
            "token_id": t["token_id"], "token_type": t["token_type"], "label": t["label"],
            "account_ids": inside,
            "outside_account_count": len(set(t["account_ids"]) - set(inside)),
        })
    rows, truncated = cap(rows)
    return ToolOutput(
        rows=rows, truncated=truncated,
        warnings=[] if rows else ["no shared device, IP or token links among this case's "
                                  "accounts"],
        refs=ToolRefs(account_ids=uniq(a for r in rows for a in r["account_ids"]),
                      evidence_ids=[r["token_id"] for r in rows]),
    )
