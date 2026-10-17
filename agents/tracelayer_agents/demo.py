"""Run the analysis workflow on a mock fixture and print the trace and the top alert.

    python -m tracelayer_agents.demo --scenario fan_out [--mode exploratory]

Uses mocks only: no network, no keys.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from tracelayer_agents.mocks import SCENARIOS
from tracelayer_agents.registry import get_services
from tracelayer_agents.schemas import TraceEvent
from tracelayer_agents.state import AnalysisState
from tracelayer_agents.workflows.analysis import run_analysis
from tracelayer_agents.wrappers import NodeError


def _trace_table(events: list[TraceEvent]) -> str:
    rows = [("node", "status", "ms")] + [
        (e.node, e.status, f"{e.duration_ms:.1f}") for e in events
    ]
    w0 = max(len(r[0]) for r in rows)
    w1 = max(len(r[1]) for r in rows)
    lines = [f"{a:<{w0}}  {b:<{w1}}  {c:>8}" for a, b, c in rows]
    lines.insert(1, "-" * len(lines[0]))
    return "\n".join(lines)


def _report(state: AnalysisState, services) -> None:
    print(_trace_table(state.get("trace_events", [])))
    print()
    alerts = state.get("alerts", [])
    print(f"governance: {state.get('governance_status')}   case_status: "
          f"{state.get('case_status') or '-'}   alerts: {len(alerts)}")
    if not alerts:
        return
    top = min(alerts, key=lambda a: a.rank or 0)
    print()
    print(f"top alert {top.alert_id}  rank {top.rank}  tier {top.tier}  "
          f"composite {top.composite_score:.3f}")
    for name, score in sorted(top.component_scores.items()):
        print(f"  {name:<22} {score:.3f}")
    print(f"  tags: {', '.join(top.typology_tags) or '-'}   caveat: {top.confidence_caveat or '-'}")
    bundle = services.cases.get_evidence(top.alert_id)
    if bundle is not None:
        ids = [t.transaction_id for t in bundle.transactions]
        print(f"  evidence ({len(ids)} tx): {', '.join(ids)}")
        print(f"  bundle_hash: {bundle.bundle_hash}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tracelayer_agents.demo")
    parser.add_argument("--scenario", choices=SCENARIOS, default="fan_out")
    parser.add_argument(
        "--mode",
        choices=("strict", "exploratory"),
        default=os.environ.get("TRACELAYER_MODE", "strict"),
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.ERROR)  # hide the "uses mock" warnings in the demo

    services = get_services()
    print(f"scenario: {args.scenario}   mode: {args.mode}\n")
    try:
        state = asyncio.run(run_analysis(args.scenario, args.mode, services))
    except NodeError as err:
        print(_trace_table((err.state or {}).get("trace_events", [err.trace_event])))
        print(f"\nrun failed in {err.node}: {err.trace_event.error}", file=sys.stderr)
        return 1
    _report(state, services)
    return 0


if __name__ == "__main__":
    sys.exit(main())
