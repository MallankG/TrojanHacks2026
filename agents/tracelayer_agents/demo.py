"""Run the analysis workflow on a mock fixture and print the trace and the top alert.

    python -m tracelayer_agents.demo --scenario fan_out [--mode exploratory]
    python -m tracelayer_agents.demo --scenario fan_out --draft   # + case narrative for top alert
    python -m tracelayer_agents.demo --scenario fan_out --draft --decide escalate --role analyst \
        --reason "..."                                            # + reviewer decision, audit chain
    python -m tracelayer_agents.demo --copilot "What should be documented when escalating ..."

Uses mocks only: no keys. The copilot and the narrative drafter use Ollama if it is running, else
the template (`LLM_PROVIDER=template` skips Ollama).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from tracelayer_agents.audit import verify_chain
from tracelayer_agents.mocks import SCENARIOS
from tracelayer_agents.nodes.human_decision import DecisionRejected
from tracelayer_agents.registry import get_services
from tracelayer_agents.schemas import (
    AuditEvent,
    CaseDecisionResult,
    CaseDraftResult,
    CopilotResult,
    TraceEvent,
)
from tracelayer_agents.state import AnalysisState
from tracelayer_agents.workflows.analysis import run_analysis
from tracelayer_agents.workflows.case import resume_case, start_case
from tracelayer_agents.workflows.copilot import run_copilot
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


def _copilot_report(result: CopilotResult) -> None:
    print(_trace_table(result.trace))
    print()
    answer = result.answer
    print(f"provider: {answer.provider}   model: {answer.model}   "
          f"prompt: {answer.prompt_version}   fallback: {answer.fallback_used}")
    print(f"no_answer: {answer.no_answer}\n")
    print(answer.answer)
    titles = {c.chunk_id: c.doc_title for c in result.sources}
    for i, claim in enumerate(answer.claims, 1):
        print(f"\n  [{i}] {claim.text}\n      cites: {', '.join(claim.citation_chunk_ids)}")
    if answer.unknowns:
        print("\nunknowns:")
        for item in answer.unknowns:
            print(f"  - {item}")
    if answer.citations:
        print("\ncitations:")
        for cid in answer.citations:
            print(f"  {cid}  ({titles.get(cid, '?')})")
    for err in result.verifier_errors:
        print(f"verifier: claim {err.claim_index} {err.code}: {err.detail}")


def _case_report(result: CaseDraftResult) -> None:
    print(_trace_table(result.trace))
    print()
    draft, meta = result.draft, result.llm_meta
    print(f"case {result.case_id}  alert {result.alert_id}  case_status: {result.case_status}")
    print(f"provider: {meta.provider}   model: {meta.model}   prompt: {meta.prompt_version}   "
          f"llm calls: {meta.attempts}   fallback: {meta.fallback_used}\n")
    print(draft.summary)
    flagged = set(result.flagged_claims)
    for i, claim in enumerate(draft.observed_facts):
        mark = "  ** unverified claim **" if i in flagged else ""
        print(f"\n  [{i}] ({claim.claim_type}) {claim.text}{mark}")
        for label, values in (
            ("tx", claim.transaction_ids),
            ("accounts", claim.account_ids),
            ("amounts", [str(a) for a in claim.amounts]),
            ("dates", [d.isoformat() for d in claim.dates]),
            ("cites", claim.citation_chunk_ids),
        ):
            if values:
                print(f"      {label}: {', '.join(values)}")
    if draft.uncertainties:
        print("\nuncertainties:")
        for item in draft.uncertainties:
            print(f"  - {item}")
    if draft.follow_up_questions:
        print("\nfollow-up questions:")
        for item in draft.follow_up_questions:
            print(f"  - {item}")
    print()
    if not result.verifier_errors:
        print("verifier: all claims verified against the evidence bundle and retrieved sources")
    for err in result.verifier_errors:
        print(f"verifier: claim {err.claim_index} {err.code}: {err.detail}")


def _short(h: str | None) -> str:
    return h[:10] if h else "-"


def _decision_report(result: CaseDecisionResult, events: list[AuditEvent]) -> None:
    event = result.audit_event
    print(_trace_table(result.trace[-2:]))
    print()
    print(f"decision: {event.metadata.get('action')} by {event.actor_id} ({event.actor_role})")
    print(f"  reason: {event.reason}")
    print(f"final case_status: {result.case_status}")
    print("\naudit chain:")
    rows = [("#", "action", "hash", "prev_hash")] + [
        (str(i), e.action, _short(e.hash), _short(e.prev_hash)) for i, e in enumerate(events)
    ]
    widths = [max(len(r[c]) for r in rows) for c in range(3)]
    for r in rows:
        print(f"  {r[0]:>{widths[0]}}  {r[1]:<{widths[1]}}  {r[2]:<{widths[2]}}  {r[3]}")
    check = verify_chain(events)
    print("\nchain verified: ok" if check.ok else
          f"\nchain verified: FAILED at event {check.first_bad_index}: {check.reason}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tracelayer_agents.demo")
    parser.add_argument("--scenario", choices=SCENARIOS, default="fan_out")
    parser.add_argument(
        "--mode",
        choices=("strict", "exploratory"),
        default=os.environ.get("TRACELAYER_MODE", "strict"),
    )
    parser.add_argument("--copilot", metavar="QUESTION", help="ask the policy copilot instead")
    parser.add_argument(
        "--draft", action="store_true", help="then draft the case narrative for the top alert"
    )
    parser.add_argument(
        "--decide", choices=("escalate", "dismiss", "request_more_data"),
        help="with --draft: then record this reviewer decision and print the audit chain",
    )
    parser.add_argument("--role", choices=("analyst", "supervisor"), default="analyst")
    parser.add_argument("--reason", help="why the reviewer decided (required with --decide)")
    parser.add_argument("--actor-id", default="demo_user")
    args = parser.parse_args(argv)
    if args.decide and not args.draft:
        parser.error("--decide needs --draft")
    if args.decide and not (args.reason or "").strip():
        parser.error("--decide needs a non-empty --reason")
    logging.basicConfig(level=logging.ERROR)  # hide the "uses mock" warnings in the demo

    services = get_services()
    if args.copilot is not None:
        print(f"copilot question: {args.copilot}\n")
        try:
            result = asyncio.run(run_copilot(args.copilot, services=services))
        except NodeError as err:
            print(_trace_table((err.state or {}).get("trace_events", [err.trace_event])))
            print(f"\ncopilot failed in {err.node}: {err.trace_event.error}", file=sys.stderr)
            return 1
        _copilot_report(result)
        return 0

    print(f"scenario: {args.scenario}   mode: {args.mode}\n")
    try:
        state = asyncio.run(run_analysis(args.scenario, args.mode, services))
    except NodeError as err:
        print(_trace_table((err.state or {}).get("trace_events", [err.trace_event])))
        print(f"\nrun failed in {err.node}: {err.trace_event.error}", file=sys.stderr)
        return 1
    _report(state, services)
    alerts = state.get("alerts", [])
    if not args.draft or not alerts:
        return 0

    top = min(alerts, key=lambda a: a.rank or 0)
    print(f"\n=== case narrative for {top.alert_id} ===\n")
    try:
        case = asyncio.run(start_case(top.alert_id, services=services))
    except NodeError as err:
        print(_trace_table((err.state or {}).get("trace_events", [err.trace_event])))
        print(f"\ncase drafting failed in {err.node}: {err.trace_event.error}", file=sys.stderr)
        return 1
    _case_report(case)
    if not args.decide:
        return 0

    print(f"\n=== reviewer decision for {case.case_id} ===\n")
    decision = {"action": args.decide, "actor_role": args.role, "actor_id": args.actor_id,
                "reason": args.reason}
    try:
        result = asyncio.run(resume_case(case.case_id, decision, services=services))
    except DecisionRejected as err:
        print(f"decision rejected: {err}", file=sys.stderr)
        return 1
    except NodeError as err:
        print(f"recording the decision failed in {err.node}: {err.trace_event.error}",
              file=sys.stderr)
        return 1
    _decision_report(result, services.audit.list_events())
    return 0


if __name__ == "__main__":
    sys.exit(main())
