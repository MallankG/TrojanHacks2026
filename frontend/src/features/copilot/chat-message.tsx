"use client";

import { buildAnswer, coverageTone } from "@/lib/domain/copilot";
import { addAnswerToCase, recordCopilotFeedback, setCopilotFocus, updateMessage } from "@/lib/store/actions";
import type { ChatMessage } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { CiteChip } from "@/components/ui/cite";
import { Pill } from "@/components/ui/pill";
import { SensitiveText } from "@/components/ui/sensitive";

interface ChatMessageViewProps {
  message: ChatMessage;
  focus: string;
  fixed: boolean;
  graphDown: boolean;
}

export function ChatMessageView({ message, focus, fixed, graphDown }: ChatMessageViewProps) {
  if (message.who === "user") {
    return <div className="max-w-[85%] self-end rounded-[14px_14px_4px_14px] bg-ink px-3.5 py-2.5 text-sm text-white"><SensitiveText value={message.text} /></div>;
  }

  // Answers are rebuilt from current state so they always reflect data fixes and outages.
  const answer = buildAnswer(message.answerKey ?? "clarify", { fixed, graphDown });
  const tone = coverageTone(answer.coverage);

  return (
    <article className="flex flex-col gap-2.5 rounded-[14px] border border-line bg-white px-4 py-3.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <b>{answer.title}</b>
        <Pill tone={tone}>Evidence coverage: {answer.coverage}</Pill>
      </div>
      {answer.blocked ? (
        <Callout tone="bad" role="alert" title="Verifier blocked these claims before display:">
          <ul className="mt-1 flex list-disc flex-col gap-1 pl-[18px]">
            {answer.blocked.map((b) => (
              <li key={b.claim}>
                <span className="font-mono">{b.claim}</span>: {b.reason}
              </li>
            ))}
          </ul>
        </Callout>
      ) : null}
      {answer.sections.map((section) => (
        <div key={section.heading}>
          <div className="text-[13.5px] font-semibold text-ink-2">{section.heading}</div>
          <ul className="mt-1 flex list-disc flex-col gap-1.5 pl-[18px] text-[13.5px]">
            {section.items.map((item) => (
              <li key={item.text}>
                <SensitiveText value={item.text} />{" "}
                {item.cites.map((id) => (
                  <CiteChip key={id} id={id} pressed={focus === id} onClick={() => setCopilotFocus(id)} />
                ))}
              </li>
            ))}
          </ul>
        </div>
      ))}
      <span className="text-xs text-muted">
        As of Oct 9, 14:00 UTC · snapshot {fixed ? "GS-0412-b" : "GS-0412-a"} · {answer.policySources.length} policy source(s) · {answer.tools.length} tool action(s)
      </span>
      <div className="flex flex-wrap items-center gap-3">
        {answer.tools.length > 0 ? (
          <Button variant="link" onClick={() => updateMessage(message.id, { showTrace: !message.showTrace })} aria-expanded={Boolean(message.showTrace)}>
            {message.showTrace ? "Hide tool actions" : `Show tool actions (${answer.tools.length})`}
          </Button>
        ) : null}
        <Button variant="link" onClick={() => addAnswerToCase(answer.title)}>
          Add to narrative
        </Button>
        <Button variant="link" onClick={() => recordCopilotFeedback(message.id, true)}>
          Helpful
        </Button>
        <Button variant="link" onClick={() => recordCopilotFeedback(message.id, false)}>
          Not helpful
        </Button>
      </div>
      {message.feedback ? <span className="text-xs text-muted">{message.feedback}</span> : null}
      {message.showTrace ? (
        <ol aria-label="Tool actions" className="flex flex-col gap-1.5">
          {answer.tools.map((tool) => (
            <li key={`${tool.name}-${tool.args}`} className="flex flex-wrap justify-between gap-2 rounded-lg bg-subtle px-2.5 py-2 text-[12.5px]">
              <span>
                <b className="font-mono">{tool.name}</b> <span className="text-xs text-muted">(<SensitiveText value={tool.args} />)</span>
              </span>
              <span className="flex items-center gap-1.5">
                <span className="text-xs text-muted">{tool.duration}</span>
                <Pill tone={tool.status === "ok" ? "ok" : tool.status === "partial" ? "warn" : "bad"}>{tool.status}</Pill>
              </span>
            </li>
          ))}
        </ol>
      ) : null}
    </article>
  );
}
