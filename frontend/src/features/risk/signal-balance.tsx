"use client";

import Link from "next/link";
import { reasoningLadder, signalPairs, strengthTone } from "@/lib/domain/counter-evidence";
import type { ScoredAlert } from "@/lib/types";
import { buttonClasses } from "@/components/ui/button";
import { CardHeader, Panel } from "@/components/ui/card";
import { CiteList } from "@/components/ui/cite";
import { Pill } from "@/components/ui/pill";

/** Each positive signal next to its strongest counter-evidence, gaps, competing explanation and next step. */
export function SignalBalance({ alert, fixed }: { alert: ScoredAlert; fixed: boolean }) {
  const pairs = signalPairs(alert, fixed);
  const ladder = reasoningLadder(alert, fixed);
  return (
    <Panel>
      <CardHeader title="Why this alert, and why it might be benign" description="Every signal is paired with the strongest counter-evidence available. Observations, hypotheses and conclusions are kept apart." />
      <ol className="flex flex-col gap-3">
        {pairs.map((pair, i) => (
          <li key={pair.signal.text} className="grid gap-3 rounded-xl border border-line p-3.5 [grid-template-columns:repeat(auto-fit,minmax(240px,1fr))]">
            <div className="flex flex-col gap-1">
              <span className="eyebrow">Signal {i + 1} · observation</span>
              <span className="text-[13.5px]">{pair.signal.text}</span>
              <CiteList ids={pair.signal.cites} />
            </div>
            <div className="flex flex-col gap-1">
              <span className="flex items-center gap-2">
                <span className="eyebrow">Counter-evidence</span>
                <Pill tone={strengthTone(pair.strength)}>{pair.strength}</Pill>
              </span>
              {pair.counter ? (
                <>
                  <span className="text-[13.5px]">{pair.counter.text}</span>
                  <CiteList ids={pair.counter.cites} />
                </>
              ) : (
                <span className="text-[13.5px] text-muted">None found in the evidence reviewed so far. Absence of counter-evidence is not confirmation.</span>
              )}
            </div>
            <div className="flex flex-col gap-1.5 text-[13px]">
              {pair.missing.map((gap) => (
                <span key={gap.text}>
                  <b>Missing data:</b> {gap.text} <CiteList ids={gap.cites} />
                </span>
              ))}
              <span>
                <b>Competing explanation (hypothesis):</b> {pair.competing}
              </span>
              <span>
                <b>Next verification step:</b> {pair.nextStep.text} <CiteList ids={pair.nextStep.cites} />
                {pair.nextStep.href ? (
                  <Link href={pair.nextStep.href} className={buttonClasses("link")}>
                    Go
                  </Link>
                ) : null}
              </span>
            </div>
          </li>
        ))}
      </ol>
      <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(220px,1fr))]">
        <div className="rounded-xl bg-subtle px-3.5 py-3">
          <div className="eyebrow mb-1">Observations (cited facts)</div>
          <ul className="list-disc pl-4 text-[13px]">
            {ladder.observations.map((o) => (
              <li key={o.text}>{o.text}</li>
            ))}
          </ul>
        </div>
        <div className="rounded-xl bg-subtle px-3.5 py-3">
          <div className="eyebrow mb-1">Hypotheses (not findings)</div>
          <ul className="list-disc pl-4 text-[13px]">
            {ladder.hypotheses.map((h) => (
              <li key={h}>{h}</li>
            ))}
          </ul>
        </div>
        <div className="rounded-xl bg-subtle px-3.5 py-3">
          <div className="eyebrow mb-1">Conclusion</div>
          <p className="text-[13px]">{ladder.conclusion}</p>
        </div>
      </div>
    </Panel>
  );
}
