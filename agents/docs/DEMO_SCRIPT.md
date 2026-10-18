# Demo script (5 minutes)

Setup, once before going on stage (from `agents/`, venv active):

```bash
# agents/.env is loaded automatically (LLM_PROVIDER=ollama, OLLAMA_MODEL=qwen3.5:9b)
ollama ps                            # model warm? if not: ollama run qwen3.5:9b "hi"
python -m tracelayer_agents.demo --golden    # dry run; every step should print ok
```

Keep a second terminal with `export TRACELAYER_PRECOMPUTED=1` ready for the fallback (see the end).

The decision reason used below:
`REASON="Fan-out from one account to nine accounts within about two hours; escalate for supervisor review."`

---

### 0:00–0:30 Framing

> "AML investigators spend most of their time assembling evidence and writing narratives. TraceLayer
> automates the assembly, but every fact comes from code, every LLM sentence is checked against the
> evidence, and a human makes the decision. Everything you'll see is synthetic data."

### 0:30–1:15 Governance halt

```bash
python -m tracelayer_agents.demo --scenario bad_batch
```

Point at: the trace stops after `governance`, and the output shows `governance: fail   case_status: held_for_data_review   alerts: 0`.

> "This batch has a duplicate transaction ID, a payment to an account that doesn't exist, an
> impossible date and a bad currency code. In strict mode
> we don't score bad data at all. A data-quality failure goes to data review. It is never a risk signal."

Optional, if time allows: `python -m tracelayer_agents.demo --scenario bad_batch --mode exploratory`. Every alert
shows `caveat: incomplete_data`.

### 1:15–2:00 Alert with component scores and evidence

```bash
python -m tracelayer_agents.demo --scenario fan_out
```

Point at: the parallel `detect_graphml` / `detect_behavior` rows in the trace, then the top alert
(`alrt_a812f90d1b25`, tier high, composite 0.932). Below it are the individual detector scores, the
`fan_out` tag, the 9 evidence transactions and the `bundle_hash`.

> "We never hide the individual models behind one number. The composite comes from the ML team's
> fusion, and each detector's score stays next to it. The evidence bundle is hashed, so the case
> can be replayed exactly."

### 2:00–3:15 Grounded draft and verifier

```bash
python -m tracelayer_agents.demo --scenario fan_out --draft
```

Point at:
- the `provider: ollama   model: qwen3.5:9b ... precomputed: False` line;
- fact claims `[0]`–`[12]`, each with its `tx:`, `amounts:` and `dates:`. These were built in Python, not by the LLM;
- hypotheses, each with a "may have a legitimate explanation" caveat;
- the last line: `verifier: all claims verified against the evidence bundle and retrieved sources`.

> "The model writes only the summary and hypotheses. The verifier checks every ID, amount and date
> against the evidence, blocks phrases like 'SAR ready' or 'committed fraud', and requires a benign
> alternative. If a draft fails, it's redrafted once and then flagged for the human, never shown as
> verified."

### 3:15–4:00 Copilot with citations

```bash
python -m tracelayer_agents.demo --copilot "What should an analyst document when escalating an alert for supervisor review?"
```

Point at: each claim's `cites:` chunk_id and the citations list with document titles.

> "The copilot answers only from retrieved policy. A claim citing anything that wasn't retrieved is
> dropped. If nothing supports an answer, it says so."

### Optional (+1 min): case chat

```bash
python -m tracelayer_agents.demo --scenario fan_out --chat
```

This runs three chips (`explain_alert`, `trace_forward`, `benign_explanations`) and then the
free-text question "Where did the money go after it left the hub account?" on the fan_out case.
Point at: the read-only tool calls behind each turn, every claim's source ids, and the `gaps`
list, which holds statements the verifier dropped (for example, a benign explanation the tools
don't support).

> "The chat is bound to this one case. It can only run read-only lookups inside the case's scope.
> Python writes the facts; the model only writes the prose around them. Anything it can't trace
> back to a tool result is dropped, and we show that gap instead."

If Ollama is slow, the same command in the `TRACELAYER_PRECOMPUTED=1` terminal serves these four
turns from the recording (`precomputed: True`). Tools, the verifier and audit still run live.

### 4:00–4:45 Human decision and audit chain

```bash
python -m tracelayer_agents.demo --scenario fan_out --draft --decide escalate --role analyst --reason "$REASON"
```

Point at: `decision: escalate by demo_user (analyst)`, `final case_status: escalated`, the audit
table (each `prev_hash` equals the row above's `hash`) and `chain verified: ok`.

> "The workflow pauses until a person decides, with a role and a written reason. Escalation is an
> internal step: nothing is filed or frozen automatically. Every step is hash-chained, so tampering
> with any event breaks verification."

### 4:45–5:00 Close

```bash
python -m tracelayer_agents.demo --golden --decide escalate --reason "$REASON"
```

> "One command replays the whole story deterministically: clean batch, governance halt, caveated
> exploratory run, a cycle, the case, the copilot, the case chat, the decision and a verified audit chain, with
> dataset and config hashes."

---

## If Ollama is slow

Cues: `draft_narrative` takes more than ~20 s, or the output shows `fallback: True` (`provider: template`).

1. Say: *"The local model is slow on a laptop, so I'll switch to the recorded run of the same pipeline."*
2. In the second terminal (`TRACELAYER_PRECOMPUTED=1`), rerun the same command. The golden
   alert and question are served instantly from `mocks/fixtures/golden_run.json`.
3. Point at `precomputed: True` next to `provider: ollama  model: qwen3.5:9b`. This is real Ollama
   output recorded earlier, and the verifier, audit chain and decision still run live. Say so.

Only the fan_out top alert, the copilot question above, and the four chat turns (the three chips
plus the hub question, on the fan_out case) are precomputed. Any other alert or question
runs live, so stick to the script. If Ollama is completely down, the template fallback answers in
milliseconds with the same schema and a passing verifier (`fallback: True`).

Refresh the recording before the demo (needs Ollama up, takes ~4 min with the chat):

```bash
python -m tracelayer_agents.demo --golden --decide escalate --reason "$REASON" --save
```

It refuses to save if the template answered or if `TRACELAYER_PRECOMPUTED` is on.

## Measured latency (this Mac, fixtures, ms)

| node | template (no network) | Ollama qwen3.5:9b live | precomputed |
|---|---:|---:|---:|
| analysis, all 7 nodes (fan_out) | 13.2 | 12.5 | 20.9 |
| load_case + retrieve_policy | 0.7 | 0.7 | 0.8 |
| draft_narrative | 1.2 | 78,345 (2 attempts) | 1.0 |
| verify_evidence + save_draft | 0.8 | 8.0 | 1.0 |
| copilot_answer | 0.1 | 15,403 | 0.1 |
| record_decision | 0.2 | 0.6 | 0.2 |
