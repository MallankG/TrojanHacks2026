"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { cn } from "@/lib/cn";
import { CASE_EVIDENCE_IDS, evidenceOrFallback } from "@/lib/domain/evidence";
import { PROMPT_CHIPS } from "@/lib/domain/copilot";
import { formatMoney } from "@/lib/format";
import { ROUTES } from "@/lib/routes";
import { EDGES, edgeByTx, NODES } from "@/lib/seed/graph";
import { askCopilot, clearChat, copilotBlockedReason, openModal, setCopilotFocus, switchCopilotCase, updateGraph } from "@/lib/store/actions";
import { useDemoState, useEvidenceRegistry, useRole } from "@/lib/store/hooks";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Card, Panel } from "@/components/ui/card";
import { CiteChip } from "@/components/ui/cite";
import { Chip, OptionCard } from "@/components/ui/controls";
import { TextInput } from "@/components/ui/field";
import { PageHeader } from "@/components/ui/page-header";
import { Pill } from "@/components/ui/pill";
import { EvidenceBody } from "@/features/evidence/evidence-body";
import { ChatMessageView } from "./chat-message";

const MIN_QUESTION_LENGTH = 3;

export function CopilotWorkspace() {
  const router = useRouter();
  const state = useDemoState();
  const role = useRole();
  const registry = useEvidenceRegistry();
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const { messages, focus, contextCase } = state.copilot;
  const fixed = state.data.driftFixed;
  const blocked = copilotBlockedReason(state);
  const evidence = evidenceOrFallback(registry, focus);
  const focusEdge = edgeByTx(focus);

  // Scroll only when a message arrives; Activity restores the scroll position on revisits.
  const shownCount = useRef(messages.length);
  useEffect(() => {
    if (messages.length > shownCount.current) endRef.current?.scrollIntoView({ block: "nearest" });
    shownCount.current = messages.length;
  }, [messages.length]);

  const send = (event: FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (text.length < MIN_QUESTION_LENGTH) return;
    askCopilot(text);
    setDraft("");
  };

  const hops = EDGES.filter((e) => !e.cycle);

  return (
    <>
      <PageHeader
        title="AI investigation copilot"
        description="Case-scoped and read-only. Every answer is checked against evidence before you see it."
        actions={
          <>
            <Pill tone="info" mono>
              Context: {contextCase}
            </Pill>
            <Button variant="ghost" size="sm" onClick={() => openModal({ kind: "switchCase" })}>
              Switch case
            </Button>
          </>
        }
      />
      <div className="flex flex-wrap items-start gap-4">
        <Panel aria-label="Transaction and source evidence" className="min-w-0 flex-[1_1_280px]">
          <div className="eyebrow">1 · Evidence</div>
          <div>
            <span className="text-xs text-muted">{evidence.kind}</span>
            <h2 className="mt-0.5 text-base font-semibold">{evidence.title}</h2>
          </div>
          <EvidenceBody item={evidence} />
          {focusEdge ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                updateGraph({ selection: { kind: "edge", key: focusEdge.key } });
                router.push(ROUTES.graph);
              }}
            >
              Highlight in graph workbench
            </Button>
          ) : null}
          <div className="border-t border-line-soft pt-2.5">
            <div className="mb-1.5 text-xs text-muted">Case evidence (click to select)</div>
            <div className="flex flex-wrap">
              {CASE_EVIDENCE_IDS.map((id) => (
                <CiteChip key={id} id={id} pressed={focus === id} onClick={() => setCopilotFocus(id)} />
              ))}
            </div>
          </div>
        </Panel>

        <Panel aria-label="Graph and timeline context" className="min-w-0 flex-[1_1_240px]">
          <div className="eyebrow">2 · Path and timeline</div>
          <span className="text-xs text-muted">7 accounts · Oct 6 to 9 · {fixed ? "GS-0412-b" : "GS-0412-a"}</span>
          <ol className="flex flex-col gap-2">
            {hops.map((e) => {
              const held = e.key === "e3" && !fixed;
              const on = focus === e.tx || focus === NODES[e.from].id || focus === NODES[e.to].id;
              return (
                <li key={e.key}>
                  <OptionCard pressed={on} onClick={() => setCopilotFocus(e.tx)} className="flex-col items-start gap-0.5">
                    <b className="font-mono text-xs">{e.tx}</b>
                    <span className="text-[12.5px]">
                      {NODES[e.from].id} to {NODES[e.to].id}
                    </span>
                    <span className="text-xs text-muted">
                      {held ? "On hold" : formatMoney(e.amount)} · {e.initiated}
                    </span>
                  </OptionCard>
                </li>
              );
            })}
          </ol>
          <span className="text-xs text-muted">Selecting a citation highlights the matching hop here and its record on the left.</span>
        </Panel>

        <Card aria-label="Copilot chat" className="flex min-w-0 flex-[2_1_420px] flex-col">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-[18px] py-3.5">
            <div>
              <div className="eyebrow">3 · Copilot</div>
              <span className="text-xs text-muted">Allowlisted read-only tools · as of Oct 9, 14:00 UTC</span>
            </div>
            <Button variant="ghost" size="sm" onClick={clearChat} disabled={messages.length === 0}>
              Clear chat
            </Button>
          </div>
          <div className="flex flex-col gap-2 px-[18px] pt-3">
            {state.sim.llm ? (
              <Callout tone="bad" role="alert" title="LLM unavailable.">
                Answers are paused. Evidence, graph snapshots and manual review still work. Nothing is implied as inspected.
              </Callout>
            ) : null}
            {role.readOnly ? <Callout tone="info">Auditor view: you can read the transcript and citations but not ask questions.</Callout> : null}
            {contextCase !== "CASE-0412" ? (
              <Callout tone="info" action={<Button variant="ghost" size="sm" onClick={() => switchCopilotCase("CASE-0412")}>Back to CASE-0412</Button>}>
                {contextCase} is closed. Its context is read-only in this prototype.
              </Callout>
            ) : null}
          </div>
          <div className="flex min-h-[280px] flex-col gap-3 px-[18px] py-3.5" aria-live="polite">
            {messages.length === 0 ? (
              <div className="px-2.5 py-7 text-center text-muted">
                <b className="text-ink">Ask about CASE-0412</b>
                <p className="mt-1 text-[13.5px]">Start with a suggested prompt. Answers cite transactions, accounts, model runs and policy passages.</p>
              </div>
            ) : null}
            {messages.map((message) => (
              <ChatMessageView key={message.id} message={message} focus={focus} fixed={fixed} graphDown={state.sim.graph} />
            ))}
            <div ref={endRef} />
          </div>
          <form onSubmit={send} className="flex flex-col gap-2.5 border-t border-line px-[18px] py-3">
            <div className="flex flex-wrap gap-1.5">
              {PROMPT_CHIPS.map((chip) => (
                <Chip key={chip.key} disabled={Boolean(blocked)} onClick={() => askCopilot(chip.label, chip.key)}>
                  {chip.label}
                </Chip>
              ))}
            </div>
            <div className="flex gap-2">
              <label className="flex-1">
                <span className="sr-only">Ask a question about this case</span>
                <TextInput value={draft} disabled={Boolean(blocked)} onChange={(e) => setDraft(e.target.value)} placeholder="Ask about CASE-0412, for example: where did the funds go next?" />
              </label>
              <Button type="submit" disabled={Boolean(blocked) || draft.trim().length < MIN_QUESTION_LENGTH}>
                Send
              </Button>
            </div>
            <span className={cn("text-xs text-muted")}>The copilot cannot file reports, freeze accounts or contact customers. Questions outside your case scope are refused and logged.</span>
          </form>
        </Card>
      </div>
    </>
  );
}
