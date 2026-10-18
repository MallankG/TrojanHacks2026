"""Run the analysis workflow on a mock fixture and print the trace and the top alert.

    python -m tracelayer_agents.demo --scenario fan_out [--mode exploratory]
    python -m tracelayer_agents.demo --scenario fan_out --draft   # + case narrative for top alert
    python -m tracelayer_agents.demo --scenario fan_out --draft --decide escalate --role analyst \
        --reason "..."                                            # + reviewer decision, audit chain
    python -m tracelayer_agents.demo --copilot "What should be documented when escalating ..."
    python -m tracelayer_agents.demo --golden [--decide escalate --reason "..."] [--save]
    python -m tracelayer_agents.demo --scenario fan_out --chat    # case chat: 3 chips + 1 question

`--golden` runs the fixed demo sequence through the public facade only (see `run_golden`);
`--save` also writes it to `mocks/fixtures/golden_run.json`, which `TRACELAYER_PRECOMPUTED=1`
serves for the golden alert and question.

Uses mocks only: no keys. The copilot and the narrative drafter use Ollama if it is running, else
the template (`LLM_PROVIDER=template` skips Ollama).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import random
import sys
import time
from datetime import UTC, datetime
from typing import Any

from tracelayer_agents import api, precomputed
from tracelayer_agents.audit import sha256_json, verify_chain
from tracelayer_agents.config import AgentConfig
from tracelayer_agents.llm.client import PROMPTS, LLMSettings
from tracelayer_agents.mocks import FIXTURES, SCENARIOS
from tracelayer_agents.nodes.human_decision import DecisionRejected
from tracelayer_agents.registry import get_services
from tracelayer_agents.schemas import (
    AnalysisResult,
    AuditEvent,
    CaseDecisionResult,
    CaseDraftResult,
    ChatTurnResult,
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
          f"prompt: {answer.prompt_version}   fallback: {answer.fallback_used}   "
          f"precomputed: {result.precomputed}")
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
          f"llm calls: {meta.attempts}   fallback: {meta.fallback_used}   "
          f"precomputed: {result.precomputed}\n")
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


# --------------------------------------------------------------------------- golden run

GOLDEN_SEED = 20261017
# (label, scenario, mode), in demo order.
GOLDEN_ANALYSES = (
    ("fan_out/strict", precomputed.GOLDEN_SCENARIO, "strict"),
    ("bad_batch/strict", "bad_batch", "strict"),
    ("bad_batch/exploratory", "bad_batch", "exploratory"),
    ("cycle/strict", "cycle", "strict"),
)
GOLDEN_INPUTS = (
    "fan_out.csv", "bad_batch.csv", "cycle.csv", "accounts.csv", "policy_chunks.json",
)


def golden_hashes(config: AgentConfig) -> tuple[dict[str, str], str]:
    """sha256 of every fixture the golden run reads, and of the settings that shape its output
    (agent config without the precomputed switch, LLM provider/model, prompt versions, seed)."""
    datasets = {
        name: hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() for name in GOLDEN_INPUTS
    }
    llm = LLMSettings.from_env()
    config_hash = sha256_json({
        "agent_config": config.model_dump(mode="json", exclude={"use_precomputed"}),
        "llm": {"provider": llm.provider, "model": llm.ollama_model,
                "timeout_s": llm.timeout_s, "max_calls_per_run": llm.max_calls_per_run},
        "prompts": {name: path.stem for name, path in sorted(PROMPTS.items())},
        "seed": GOLDEN_SEED,
    })
    return datasets, config_hash


def _top(result: AnalysisResult):
    return min(result.alerts, key=lambda a: a.rank or 0) if result.alerts else None


def _analysis_line(label: str, result: AnalysisResult, ms: float) -> list[str]:
    head = (f"{label:<22} {result.status:<7} governance={result.governance_status or '-'}  "
            f"alerts={len(result.alerts)}  {ms:,.0f} ms")
    lines = [head]
    if result.halted:
        lines.append("governance halt: case_status=held_for_data_review, no alerts scored")
    elif result.error:
        lines.append(f"error: {result.error}")
    top = _top(result)
    if top is not None:
        caveated = sum(a.confidence_caveat == "incomplete_data" for a in result.alerts)
        scores = "  ".join(f"{k}={v:.3f}" for k, v in sorted(top.component_scores.items()))
        lines.append(f"top {top.alert_id}  tier={top.tier}  composite={top.composite_score:.3f}"
                     f"  tags={','.join(top.typology_tags) or '-'}")
        lines.append(f"components: {scores}")
        lines.append(f"caveat incomplete_data on {caveated}/{len(result.alerts)} alerts")
    return lines


def _latency_table(steps: list[tuple[str, list[TraceEvent]]]) -> str:
    """Per-node latency (ms), one column per step."""
    labels = [label for label, _ in steps]
    nodes: list[str] = []
    cells: dict[tuple[str, str], float] = {}
    for label, trace in steps:
        for e in trace:
            if e.node not in nodes:
                nodes.append(e.node)
            cells[(e.node, label)] = cells.get((e.node, label), 0.0) + e.duration_ms
    width = max(len(n) for n in nodes) if nodes else 4
    cols = [max(len(label), 9) for label in labels]

    def line(first: str, values: list[str]) -> str:
        return f"{first:<{width}}  " + "  ".join(
            f"{v:>{w}}" for v, w in zip(values, cols, strict=True)
        )

    lines = [line("node", labels)]
    lines.append("-" * len(lines[0]))
    for node in nodes:
        lines.append(line(node, [
            f"{cells[(node, lb)]:,.1f}" if (node, lb) in cells else "-" for lb in labels
        ]))
    return "\n".join(lines)


async def run_golden(decision: dict[str, Any] | None, config: AgentConfig) -> dict[str, Any]:
    """The fixed demo sequence, through the public facade only: reset → fan_out → bad_batch
    strict (halt) → bad_batch exploratory (caveated) → cycle → start_case on the fan_out top
    alert → copilot on the golden question → optional decision → audit chain check."""
    random.seed(GOLDEN_SEED)  # the mocks are hash-deterministic; this pins anything that isn't
    api.configure(config=config)  # fresh in-memory services, repositories and audit chain

    run: dict[str, Any] = {"analyses": {}, "timings": {}}
    for label, scenario, mode in GOLDEN_ANALYSES:
        t0 = time.perf_counter()
        run["analyses"][label] = await api.run_analysis(scenario, mode)
        run["timings"][label] = (time.perf_counter() - t0) * 1000

    top = _top(run["analyses"]["fan_out/strict"])
    if top is None:
        return run
    t0 = time.perf_counter()
    run["case"] = await api.start_case(top.alert_id)
    run["timings"]["start_case"] = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    run["copilot"] = await api.copilot_query(precomputed.GOLDEN_QUESTION, top.alert_id)
    run["timings"]["copilot"] = (time.perf_counter() - t0) * 1000
    if decision is not None and run["case"].status == "ok":
        t0 = time.perf_counter()
        run["decision"] = await api.resume_case(run["case"].case_id, decision)
        run["timings"]["decision"] = (time.perf_counter() - t0) * 1000
    run["audit"] = api.verify_audit_chain()
    return run


def _golden_report(run: dict[str, Any], config: AgentConfig) -> None:
    datasets, config_hash = golden_hashes(config)
    llm = LLMSettings.from_env()
    switch = "on" if config.use_precomputed else "off"
    print(f"golden run   seed={GOLDEN_SEED}   precomputed={switch}   LLM_PROVIDER={llm.provider}"
          f"   OLLAMA_MODEL={llm.ollama_model or '(first listed)'}")
    print("datasets:  " + "  ".join(f"{k}={v[:10]}" for k, v in datasets.items()))
    print(f"config:    {config_hash[:16]}\n")

    steps: list[tuple[str, list[TraceEvent]]] = []
    for i, (label, result) in enumerate(run["analyses"].items(), 1):
        print(f"[{i}] " + "\n    ".join(_analysis_line(label, result, run["timings"][label])))
        steps.append((label.replace("/strict", "").replace("/exploratory", "/expl"), result.trace))

    n = len(run["analyses"])
    case: CaseDraftResult | None = run.get("case")
    if case is None:
        print("\nno fan_out alert: nothing to open a case on")
        return
    n += 1
    ms = run["timings"]["start_case"]
    print(f"[{n}] start_case {case.alert_id}  {case.status}  {ms:,.0f} ms")
    if case.status == "ok" and case.draft is not None and case.llm_meta is not None:
        meta = case.llm_meta
        print(f"    case {case.case_id}  case_status={case.case_status}  "
              f"claims={len(case.draft.observed_facts)}  "
              f"verifier_errors={len(case.verifier_errors)}  flagged={case.flagged_claims or '-'}")
        print(f"    {meta.provider}/{meta.model}  {meta.prompt_version}  "
              f"fallback={meta.fallback_used}  precomputed={case.precomputed}")
        print(f"    summary: {case.draft.summary[:160]}")
    else:
        print(f"    error: {case.error}")
    steps.append(("case", case.trace))

    copilot: CopilotResult = run["copilot"]
    n += 1
    print(f"[{n}] copilot  {copilot.status}  {run['timings']['copilot']:,.0f} ms")
    if copilot.status == "ok" and copilot.answer is not None:
        ans = copilot.answer
        print(f"    Q: {ans.question}")
        print(f"    claims={len(ans.claims)}  citations={', '.join(ans.citations) or '-'}  "
              f"verifier_errors={len(copilot.verifier_errors)}")
        print(f"    {ans.provider}/{ans.model}  {ans.prompt_version}  "
              f"fallback={ans.fallback_used}  precomputed={copilot.precomputed}")
    else:
        print(f"    error: {copilot.error}")
    steps.append(("copilot", copilot.trace))

    decided: CaseDecisionResult | None = run.get("decision")
    if decided is not None:
        n += 1
        print(f"[{n}] decision  {decided.status}  {run['timings']['decision']:,.0f} ms")
        if decided.status == "ok" and decided.audit_event is not None:
            ev = decided.audit_event
            print(f"    {ev.metadata.get('action')} by {ev.actor_id} ({ev.actor_role}) → "
                  f"case_status={decided.case_status}  audit hash {_short(ev.hash)}")
        else:
            print(f"    {decided.error}")
        steps.append(("decision", decided.trace[-2:]))

    check = run["audit"]
    n += 1
    print(f"[{n}] audit chain  " + (f"verified ok ({check.checked} events)" if check.ok else
                                    f"FAILED at {check.first_bad_index}: {check.reason}"))
    print("\nper-node latency (ms)\n" + _latency_table(steps))


def _save_golden(run: dict[str, Any], config: AgentConfig) -> str | None:
    """Write the golden run; returns an error message instead if it isn't fit to serve."""
    if config.use_precomputed:
        return "refusing to save while TRACELAYER_PRECOMPUTED is on (would re-save served output)"
    case: CaseDraftResult | None = run.get("case")
    copilot: CopilotResult | None = run.get("copilot")
    if case is None or case.status != "ok" or copilot is None or copilot.status != "ok":
        return "case or copilot step failed; nothing saved"
    assert case.llm_meta is not None and copilot.answer is not None
    if case.llm_meta.fallback_used or copilot.answer.fallback_used:
        return ("the template answered (Ollama down or too slow); start Ollama with "
                f"{os.environ.get('OLLAMA_MODEL') or 'the team model'} and rerun")
    datasets, config_hash = golden_hashes(config)
    golden = precomputed.GoldenRun(
        created_at=datetime.now(UTC),
        seed=GOLDEN_SEED,
        dataset_hashes=datasets,
        config_hash=config_hash,
        golden_alert_id=case.alert_id,
        golden_question=precomputed.GOLDEN_QUESTION,
        copilot_source_ids=[c.chunk_id for c in copilot.sources],
        analyses=run["analyses"],
        case=case,
        copilot=copilot,
        decision=run.get("decision"),
    )
    precomputed.GOLDEN_RUN_PATH.write_text(golden.model_dump_json(indent=2) + "\n")
    return None


CHAT_DEMO_CHIPS = ("explain_alert", "trace_forward", "benign_explanations")
CHAT_DEMO_QUESTION = "Where did the money go after it left the hub account?"


def _chat_turn_report(label: str, result: ChatTurnResult) -> None:
    print(f"\n--- {label} ---")
    if result.status == "error" or result.turn is None:
        print(f"error: {result.error}")
        return
    turn = result.turn
    print(f"status: {turn.status}   intent: {turn.intent}   verification: "
          f"{turn.verification_status}   llm: "
          + ", ".join(f"{m.provider}{' (fallback)' if m.fallback_used else ''}"
                      for m in turn.llm_meta))
    for call in turn.tool_calls:
        print(f"  tool {call.tool:<22} {call.status:<8} rows={call.row_count:<3} "
              f"{call.duration_ms:>8.1f} ms  {call.error or ''}")
    print(f"answer: {turn.answer}")
    for claim in turn.claims[:8]:
        print(f"  [{claim.label}] {claim.text}")
    if len(turn.claims) > 8:
        print(f"  ... {len(turn.claims) - 8} more claims")
    for gap in turn.gaps:
        print(f"  GAP ({gap.label}: {', '.join(gap.codes)}) -> {gap.suggested_query}")
    for step in turn.next_steps:
        print(f"  next: {step}")


async def run_chat_demo(scenario: str, mode: str) -> int:
    """Analysis → case draft → chat on the top alert's case, through the public facade."""
    api.configure()
    analysis = await api.run_analysis(scenario, mode)  # type: ignore[arg-type]
    if analysis.status != "ok" or not analysis.alerts:
        print(f"analysis {analysis.status}: {analysis.error or 'no alerts'}", file=sys.stderr)
        return 1
    top = min(analysis.alerts, key=lambda a: a.rank or 0)
    case = await api.start_case(top.alert_id)
    if case.status != "ok":
        print(f"case failed: {case.error}", file=sys.stderr)
        return 1
    chat = await api.start_chat(case.case_id, "demo_user", "analyst")
    if chat.status != "ok" or chat.chat is None:
        print(f"chat failed: {chat.error}", file=sys.stderr)
        return 1
    ctx = chat.chat.context
    print(f"chat {chat.chat.chat_id} bound to {ctx.case_id} (alert {ctx.alert_id}, snapshot "
          f"{ctx.graph_snapshot_id})")
    print(f"window {ctx.window_start:%Y-%m-%d} to {ctx.window_end:%Y-%m-%d}; "
          f"{len(ctx.entity_scope)} accounts and {ctx.scope_transaction_count} transactions "
          f"in scope; {len(ctx.evidence_bundle.transactions)} evidence transactions")
    for chip in CHAT_DEMO_CHIPS:
        _chat_turn_report(f"chip: {chip}", await api.send_message(chat.chat.chat_id, chip=chip))
    _chat_turn_report(f"question: {CHAT_DEMO_QUESTION}",
                      await api.send_message(chat.chat.chat_id, text=CHAT_DEMO_QUESTION))
    check = api.verify_audit_chain()
    print(f"\naudit chain: {'ok' if check.ok else 'BROKEN'} ({check.checked} events)")
    return 0 if check.ok else 1


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
        "--chat", action="store_true",
        help="open an investigation chat on the top alert's case: 3 chips + 1 question",
    )
    parser.add_argument(
        "--draft", action="store_true", help="then draft the case narrative for the top alert"
    )
    parser.add_argument(
        "--decide", choices=("escalate", "dismiss", "request_more_data"),
        help="with --draft or --golden: then record this reviewer decision and check the chain",
    )
    parser.add_argument(
        "--golden", action="store_true",
        help="run the fixed demo sequence through the public facade and print a summary",
    )
    parser.add_argument(
        "--save", action="store_true",
        help=f"with --golden: write the run to {precomputed.GOLDEN_RUN_PATH.name} "
             "(needs live Ollama output)",
    )
    parser.add_argument("--role", choices=("analyst", "supervisor"), default="analyst")
    parser.add_argument("--reason", help="why the reviewer decided (required with --decide)")
    parser.add_argument("--actor-id", default="demo_user")
    args = parser.parse_args(argv)
    if args.decide and not (args.draft or args.golden):
        parser.error("--decide needs --draft or --golden")
    if args.save and not args.golden:
        parser.error("--save needs --golden")
    if args.decide and not (args.reason or "").strip():
        parser.error("--decide needs a non-empty --reason")
    logging.basicConfig(level=logging.ERROR)  # hide the "uses mock" warnings in the demo

    if args.golden:
        config = AgentConfig.from_env()
        decision = None
        if args.decide:
            decision = {"action": args.decide, "actor_role": args.role,
                        "actor_id": args.actor_id, "reason": args.reason}
        run = asyncio.run(run_golden(decision, config))
        _golden_report(run, config)
        if args.save:
            problem = _save_golden(run, config)
            if problem:
                print(f"\nnot saved: {problem}", file=sys.stderr)
                return 1
            print(f"\nsaved {precomputed.GOLDEN_RUN_PATH}")
        return 0

    if args.chat:
        print(f"scenario: {args.scenario}   mode: {args.mode}\n")
        return asyncio.run(run_chat_demo(args.scenario, args.mode))

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
