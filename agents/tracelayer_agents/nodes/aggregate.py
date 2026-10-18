"""Turns the ML team's `FusionResult` into ranked `Alert`s.

The node never computes or adjusts weights: the composite comes from `RankFusion` as-is, and
each alert keeps every detector's own score next to it. Tiers come from the composite rank
(config thresholds). Any transaction a typology rule flagged becomes an alert whatever its rank,
so a rule hit is never dropped just because the fused score ranks it low.
"""

import asyncio
import hashlib
import math
from collections.abc import Mapping
from typing import Any

from tracelayer_agents import audit
from tracelayer_agents.config import AgentConfig
from tracelayer_agents.schemas import (
    AggregateOutput,
    Alert,
    AlertTier,
    AuditEvent,
    DetectorOutput,
    FusionResult,
)
from tracelayer_agents.wrappers import NodeContext, traced_node


def assign_tiers(composite: dict[str, float], config: AgentConfig) -> dict[str, AlertTier]:
    """Tier by rank position; tied scores share the best position among them."""
    ranked = sorted(composite.items(), key=lambda kv: (-kv[1], kv[0]))
    n = len(ranked)
    high_cut = math.ceil(config.tier_high_top_pct * n)
    medium_cut = math.ceil(config.tier_medium_top_pct * n)
    first_pos: dict[float, int] = {}
    tiers: dict[str, AlertTier] = {}
    for pos, (tx_id, score) in enumerate(ranked, start=1):
        p = first_pos.setdefault(score, pos)
        tiers[tx_id] = "high" if p <= high_cut else "medium" if p <= medium_cut else "low"
    return tiers


def build_alerts(
    fusion: FusionResult,
    model_scores: dict[str, DetectorOutput],
    *,
    snapshot_id: str,
    governance_status: str,
    config: AgentConfig,
) -> list[Alert]:
    detectors = {name: out for name, out in model_scores.items() if out.status == "ok"}
    tiers = assign_tiers(fusion.composite, config)
    flags: dict[str, set[str]] = {}
    for out in detectors.values():
        for tx_id, names in out.typology_flags.items():
            flags.setdefault(tx_id, set()).update(names)

    selected = [
        (tx_id, score)
        for tx_id, score in sorted(fusion.composite.items(), key=lambda kv: (-kv[1], kv[0]))
        if tiers[tx_id] != "low" or flags.get(tx_id)
    ]
    caveat = None if governance_status == "pass" else "incomplete_data"
    alerts = []
    for rank, (tx_id, score) in enumerate(selected, start=1):
        digest = hashlib.sha256(f"{snapshot_id}:{tx_id}".encode()).hexdigest()
        alerts.append(Alert(
            alert_id=f"alrt_{digest[:12]}",
            tx_ids=[tx_id],
            composite_score=score,
            component_scores={
                name: out.scores[tx_id] for name, out in detectors.items() if tx_id in out.scores
            },
            typology_tags=sorted(flags.get(tx_id, ())),
            tier=tiers[tx_id],
            quality_status=governance_status,
            confidence_caveat=caveat,
            graph_snapshot_id=snapshot_id,
            model_versions={name: out.model_version for name, out in detectors.items()},
            rank=rank,
            model_run_id=fusion.model_run_id,
        ))
    return alerts


@traced_node("aggregate", AggregateOutput)
async def aggregate(state: Mapping[str, Any], ctx: NodeContext) -> AggregateOutput:
    services = ctx.services
    model_scores: dict[str, DetectorOutput] = state.get("model_scores") or {}
    fusion = await asyncio.to_thread(services.fusion.fuse, model_scores)
    alerts = build_alerts(
        fusion,
        model_scores,
        snapshot_id=state["graph_snapshot_id"],
        governance_status=state["governance_status"],
        config=ctx.config,
    )

    def persist() -> list[AuditEvent]:
        services.cases.save_alerts(alerts)
        return [
            audit.record(
                services.audit,
                action="alert.created",
                object_type="alert",
                object_id=a.alert_id,
                model_versions=a.model_versions,
                metadata={
                    "trace_id": state.get("trace_id"),
                    "tier": a.tier,
                    "rank": a.rank,
                    "composite_score": a.composite_score,
                    "fusion_method": fusion.method,
                    "quality_status": a.quality_status,
                    "confidence_caveat": a.confidence_caveat,
                },
            )
            for a in alerts
        ]

    events = await asyncio.to_thread(persist)
    return AggregateOutput(alerts=alerts, audit_events=events)
