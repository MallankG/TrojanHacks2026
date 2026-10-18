"""model_explanation, quality_lineage, policy_retrieval and similar_cases (mock)."""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field

from tracelayer_agents.chat.session import CaseScope
from tracelayer_agents.mocks import FIXTURES
from tracelayer_agents.nodes.retrieve_policy import build_query
from tracelayer_agents.schemas import DetectorOutput, ToolRefs
from tracelayer_agents.tools.base import (
    MAX_ROWS,
    ToolInput,
    ToolOutput,
    cap,
    require_txs,
    uniq,
)
from tracelayer_agents.wrappers import NodeContext

# What the agents never receive, so no chat answer may claim it was checked.
# TODO(review): drop items as the backend starts supplying them.
NOT_AVAILABLE = (
    ("kyc_profile", "No KYC or customer profile is available to the agents."),
    ("business_purpose", "No stated business purpose is available for these transfers."),
    ("account_ownership", "No account ownership or relationship data is available, so "
                          "treasury-sweep or shared-service explanations cannot be checked."),
)


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


SIMILAR_METHOD = "mock_typology_reference"
_REFERENCES_PATH = FIXTURES / "typology_references.json"


def similar_cases(args: EmptyInput, scope: CaseScope, ctx: NodeContext) -> ToolOutput:
    """Mock: matches the alert's typology tags and flow-pattern kinds against a fixed library of
    reference patterns. Never returns another case's data."""
    kinds = set(scope.alert.typology_tags)
    findings = scope.bundle.flow_findings
    if findings is not None:
        kinds |= {k.removesuffix("s") if k == "cycles" else k
                  for k in ("fan_in", "fan_out", "pass_through", "cycles")
                  if getattr(findings, k)}
    refs = json.loads(_REFERENCES_PATH.read_text())
    rows = [{"method": SIMILAR_METHOD, **r} for r in refs if r["typology"] in kinds]
    rows, truncated = cap(rows)
    return ToolOutput(
        rows=rows, truncated=truncated,
        warnings=[] if rows else ["no reference pattern matches this alert's typology"],
        refs=ToolRefs(evidence_ids=[r["reference_id"] for r in rows]),
    )
