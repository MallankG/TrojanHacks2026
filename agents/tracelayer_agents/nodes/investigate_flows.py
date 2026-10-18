"""Flow findings and evidence bundles for the alerts worth a closer look: the top N by rank plus
every typology-rule hit, capped at `config.max_investigations`."""

import asyncio
from collections.abc import Mapping
from typing import Any

from tracelayer_agents.audit import sha256_json
from tracelayer_agents.config import AgentConfig
from tracelayer_agents.schemas import Alert, EvidenceBundle, InvestigateOutput
from tracelayer_agents.wrappers import NodeContext, SkipNode, traced_node


def select_targets(alerts: list[Alert], config: AgentConfig) -> list[Alert]:
    ordered = sorted(alerts, key=lambda a: a.rank or 0)
    candidates = ordered[: config.investigate_top_n] + [a for a in ordered if a.typology_tags]
    seen: set[str] = set()
    targets = []
    for alert in candidates:
        if alert.alert_id not in seen:
            seen.add(alert.alert_id)
            targets.append(alert)
    return targets[: config.max_investigations]


def investigate_one(alert: Alert, state: Mapping[str, Any], ctx: NodeContext) -> EvidenceBundle:
    snapshot_id = state["graph_snapshot_id"]
    findings = ctx.services.flows.investigate(alert, snapshot_id)
    transactions = ctx.services.graph.get_transactions(findings.evidence_tx_ids)
    caveats = state.get("quality_issues", []) if state.get("governance_status") != "pass" else []
    fields = {
        "alert_id": alert.alert_id,
        "transactions": transactions,
        "flow_findings": findings,
        "quality_caveats": caveats,
        "lineage_refs": [
            f"ingestion_run:{state['ingestion_run_id']}",
            f"transaction_batch:{state['transaction_batch_id']}",
            f"graph_snapshot:{snapshot_id}",
        ],
    }
    bundle = EvidenceBundle(**fields, bundle_hash=sha256_json(fields))
    ctx.services.cases.save_evidence(bundle)
    return bundle


@traced_node("investigate_flows", InvestigateOutput)
async def investigate_flows(state: Mapping[str, Any], ctx: NodeContext) -> InvestigateOutput:
    targets = select_targets(state.get("alerts") or [], ctx.config)
    if not targets:
        raise SkipNode("no alerts to investigate")
    bundles = [await asyncio.to_thread(investigate_one, a, state, ctx) for a in targets]
    top = bundles[0]
    return InvestigateOutput(
        top_evidence_transaction_ids=[t.transaction_id for t in top.transactions],
        evidence_bundle=top,
    )
