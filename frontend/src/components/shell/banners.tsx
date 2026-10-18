"use client";

import { usePathname, useRouter } from "next/navigation";
import { cn } from "@/lib/cn";
import { ROUTES, screenForPath, CASE_SCOPED_SCREENS } from "@/lib/routes";
import { toast, toggleSimulation } from "@/lib/store/actions";
import { useDemoState, useRole } from "@/lib/store/hooks";
import type { Tone } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Pill } from "@/components/ui/pill";
import { TONE_CLASSES } from "@/components/ui/tone";

interface Banner {
  tone: Tone;
  title: string;
  text: string;
  cta: string;
  onClick: () => void;
}

export function Banners() {
  const router = useRouter();
  const pathname = usePathname();
  const state = useDemoState();
  const banners: Banner[] = [];

  if (!state.data.driftFixed && screenForPath(pathname) !== "data") {
    banners.push({ tone: "warn", title: "Data hold.", text: "ach_2026_10_06_b is quarantined after schema drift. 1,206 rows and 3 alerts are affected.", cta: "Open data hold", onClick: () => router.push(ROUTES.data) });
  }
  if (state.sim.llm) {
    banners.push({
      tone: "bad",
      title: "LLM unavailable.",
      text: "Copilot answers and drafting are paused. Evidence, graph snapshots and manual review still work.",
      cta: "Retry connection",
      onClick: () => {
        toggleSimulation("llm");
        toast("LLM connection restored.");
      },
    });
  }
  if (state.sim.graph) {
    banners.push({
      tone: "bad",
      title: "Graph service unavailable.",
      text: "Showing saved snapshot GS-0412-a. Live tracing and node expansion are paused.",
      cta: "Retry connection",
      onClick: () => {
        toggleSimulation("graph");
        toast("GraphBase connection restored.");
      },
    });
  }

  return (
    <>
      {banners.map((banner) => (
        <div key={banner.title} role="alert" className={cn("flex flex-wrap items-center gap-x-3.5 gap-y-2 border-b border-[rgba(14,26,43,0.08)] px-7 py-2.5 text-[13.5px]", TONE_CLASSES[banner.tone])}>
          <b>{banner.title}</b>
          <span className="flex-[1_1_300px]">{banner.text}</span>
          <Button variant="ghost" size="sm" onClick={banner.onClick}>
            {banner.cta}
          </Button>
        </div>
      ))}
    </>
  );
}

/** Persistent case context on investigation screens, with the six-step progress tracker. */
export function CaseContextBar() {
  const router = useRouter();
  const pathname = usePathname();
  const state = useDemoState();
  const role = useRole();
  const screen = screenForPath(pathname);
  if (!role.canAccessCases || !CASE_SCOPED_SCREENS.includes(screen)) return null;

  const fixed = state.data.driftFixed;
  const steps: Array<{ label: string; done: boolean; href: string }> = [
    { label: "Ingest", done: fixed, href: ROUTES.data },
    { label: "Score", done: true, href: ROUTES.risk },
    { label: "Trace", done: Boolean(state.visited.graph), href: ROUTES.graph },
    { label: "Ask", done: state.copilot.messages.length > 0, href: ROUTES.copilot },
    { label: "Decide", done: state.caseWork.status !== "In review", href: ROUTES.case("CASE-0412", "disposition") },
    { label: "Export", done: state.caseWork.report.done, href: ROUTES.case("CASE-0412", "report") },
  ];

  return (
    <div className="flex flex-wrap items-center gap-2.5 border-b border-line bg-white px-7 py-2.5">
      <span className="eyebrow text-ink-2">Case context</span>
      <b className="font-mono text-[13px]">CASE-0412</b>
      <span className="text-xs text-muted">AL-1027 + AL-1029 · as of Oct 9, 14:00 UTC · snapshot {fixed ? "GS-0412-b" : "GS-0412-a"}</span>
      <Pill tone={fixed ? "ok" : "warn"}>{fixed ? "Data complete" : "Data partial"}</Pill>
      <Pill tone="info">{state.caseWork.status}</Pill>
      <span className="flex-1" />
      <ol aria-label="Investigation progress" className="flex flex-wrap gap-1.5">
        {steps.map((step, i) => (
          <li key={step.label}>
            <button type="button" onClick={() => router.push(step.href)} className={cn("min-h-[30px] rounded-full px-2.5 text-xs font-semibold", TONE_CLASSES[step.done ? "ok" : "mute"])}>
              {i + 1} {step.label} · {step.done ? "done" : "to do"}
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}
